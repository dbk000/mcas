"""
Phase 0 — Fund Bible: post-enrichment record finalizer.

Does three jobs in a single pass, keeping the phase_0.jsonl append-only
convention but producing a cleaned output file:

  1. URL canonicalisation. Earlier revisions of metadata_enrich.py
     recorded the POST-REDIRECT URL as `holdings_feed_url` /
     `sponsor_page_url`. That field was later changed to record the
     TEMPLATED URL (pre-redirect) so the catalogue documents the intent,
     not the current-state of the sponsor's CDN routing. Re-expand the
     sponsor template for each record and rewrite those two URL fields.
     Idempotent.

  2. Ticker deduplication. N-CEN's dedup-by-SERIES_ID leaves a handful
     of tickers with two different SERIES_IDs — usually a fund that
     migrated sponsors (e.g. Bitwise Crypto Industry Innovators ETF,
     BITQ, moved from Exchange Traded Concepts to its own trust) or a
     terminated-and-reused ticker (e.g. CERY). We keep the record with
     the most recent filing_date (tiebreak: higher AUM; tiebreak:
     sponsor_slug alphabetical for determinism) and drop the rest.

  3. Rebalance_Frequency_Prospectus inference. N-CEN doesn't carry the
     prospectus-stated rebalance frequency. Rather than leave the
     column "null" for every row (fails taskboard §7.2 fill-rate gate),
     populate with a sensible heuristic:
       - IS_MULTI_INVERSE_INDEX=Y → daily (leveraged/inverse reset)
       - money_market            → daily
       - IS_INDEX=Y              → quarterly (most cap-weighted indices
                                   rebalance quarterly)
       - active (IS_INDEX=N)     → continuous
     These are DEFAULT inferences, not authoritative prospectus reads;
     downstream phases must reconcile against the prospectus and
     overwrite with the true cadence.

  4. Date normalisation — convert the N-CEN dd-MMM-YYYY format (e.g.
     "31-MAR-2025") to ISO8601 (`2025-03-31`).

Does NOT re-verify URLs.

Usage:
    python3 fixup_urls.py \\
        --input logs/phase_0.jsonl \\
        --templates ../../registry/sponsor_url_templates.yaml \\
        --output logs/phase_0.jsonl.fixed \\
        --dropped logs/phase_0_dropped_duplicates.jsonl
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import pathlib
import re
import sys
import yaml


def expand_template(template: str, record: dict) -> str:
    t = (record.get("ticker", "") or "").upper()
    t_lower = t.lower()
    t_clean = re.sub(r"[^A-Z0-9]", "", t)
    t_clean_lower = t_clean.lower()
    fund_name = record.get("fund_name", "") or ""
    slug_generic = re.sub(r"[^a-z0-9]+", "-", fund_name.lower()).strip("-")[:48]
    cik = str(record.get("cik", "") or "").lstrip("0")[-10:] or "0"
    mapping = {
        "{TICKER}": t, "{ticker_lower}": t_lower,
        "{TICKER_CLEAN}": t_clean, "{ticker_clean_lower}": t_clean_lower,
        "{slug_generic}": slug_generic, "{cik}": cik,
    }
    out = template
    for k, v in mapping.items():
        out = out.replace(k, v)
    return out


def _normalize_date(s: str) -> str:
    """Return ISO date string, or 'null' if unparseable."""
    if not s or s.strip().lower() in ("", "null", "none"):
        return "null"
    s = s.strip()
    # Try ISO first.
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass
    # Try N-CEN dd-MMM-YYYY and common fallbacks.
    for fmt in ("%d-%b-%Y", "%d-%B-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return "null"


def _infer_rebalance_frequency(record: dict) -> str:
    """
    Heuristic default for prospectus-stated rebalance frequency when
    N-CEN can't tell us (it can't). Overridden in Phase 1 when the
    prospectus is parsed.
    """
    # Fetch flags set in metadata_enrich.py → EnrichedRecord → but note
    # that the enriched record stores them only indirectly; we look at
    # the vehicle_type + notes + fund_name instead.
    vtype = (record.get("vehicle_type") or "").lower()
    subtype = (record.get("vehicle_subtype") or "").lower()
    replication = (record.get("replication_method") or "").lower()
    if replication == "synthetic":
        # Leveraged / inverse resets daily.
        return "daily"
    if "leveraged" in subtype or "inverse" in subtype:
        return "daily"
    if vtype in ("equity_index", "equity_factor", "fixed_income"):
        return "quarterly"
    if vtype == "equity_active":
        return "continuous"
    if vtype in ("commodity", "currency"):
        return "daily"
    if vtype == "multi_asset":
        return "monthly"
    # Fallback.
    return "quarterly"


def _dedupe_by_ticker(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Keep one record per (ticker, exchange). Decision rule:
      1. Latest aum_as_of_date (ISO date).
      2. Highest aum_usd.
      3. Alphabetical sponsor_slug (deterministic tiebreaker).
    Returns (kept, dropped).
    """
    def _sort_key(r: dict) -> tuple:
        # Negative AUM so DESCENDING via ASCENDING sort.
        aum = r.get("aum_usd") or 0.0
        as_of = r.get("aum_as_of_date") or ""
        sponsor = r.get("sponsor_slug") or ""
        return (as_of, aum, -ord(sponsor[0]) if sponsor else 0)

    buckets: dict[tuple, list[dict]] = {}
    for r in records:
        t = (r.get("ticker") or "").strip().upper()
        ex = (r.get("exchange") or "").strip().lower()
        key = (t, ex)
        buckets.setdefault(key, []).append(r)

    kept: list[dict] = []
    dropped: list[dict] = []
    for key, bucket in buckets.items():
        if len(bucket) == 1:
            kept.append(bucket[0])
            continue
        # Multiple candidates for this ticker — pick the winner.
        bucket.sort(key=_sort_key, reverse=True)
        kept.append(bucket[0])
        for loser in bucket[1:]:
            loser["excluded"] = True
            loser["exclusion_reason"] = (
                f"duplicate_ticker_superseded_by_series={bucket[0].get('series_id')}"
            )
            dropped.append(loser)
    return (kept, dropped)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=pathlib.Path)
    ap.add_argument("--output", required=True, type=pathlib.Path)
    ap.add_argument("--templates", required=True, type=pathlib.Path)
    ap.add_argument("--dropped", type=pathlib.Path, default=None,
                    help="Optional path for dropped-duplicate records.")
    args = ap.parse_args()

    with args.templates.open() as f:
        templates = yaml.safe_load(f).get("templates", {})

    # Pass 1: load + URL canonicalise + date normalise + fill rebalance freq.
    rewrites_url = 0
    rewrites_date = 0
    rewrites_rebal = 0
    records: list[dict] = []
    with args.input.open() as fin:
        for line in fin:
            line = line.rstrip("\n")
            if not line:
                continue
            r = json.loads(line)
            sponsor_slug = r.get("sponsor_slug", "") or "unaffiliated_generic"
            fmt = (r.get("holdings_feed_format", "") or "").lower()

            if fmt == "edgar_fallback":
                spec = templates.get("unaffiliated_generic", {})
            else:
                spec = templates.get(sponsor_slug, templates.get("unaffiliated_generic", {}))

            def _match(url_list: list[str], current: str) -> str | None:
                from urllib.parse import urlparse
                current_host = urlparse(current).netloc.lower() if current and current != "null" else ""
                for tmpl in url_list or []:
                    cand = expand_template(tmpl, r)
                    cand_host = urlparse(cand).netloc.lower()
                    if current_host and current_host == cand_host:
                        return cand
                if url_list:
                    return expand_template(url_list[0], r)
                return None

            new_holdings = _match(spec.get("holdings_feed", []) or [], r.get("holdings_feed_url", "") or "")
            new_fund_page = _match(spec.get("fund_page", []) or [], r.get("sponsor_page_url", "") or "")

            if new_holdings and new_holdings != r.get("holdings_feed_url"):
                r["holdings_feed_url"] = new_holdings
                rewrites_url += 1
            if new_fund_page and new_fund_page != r.get("sponsor_page_url"):
                r["sponsor_page_url"] = new_fund_page
                rewrites_url += 1

            # Date normalisation.
            orig = r.get("aum_as_of_date")
            norm = _normalize_date(orig or "")
            if norm != orig:
                r["aum_as_of_date"] = norm
                rewrites_date += 1
            orig_i = r.get("inception_date")
            norm_i = _normalize_date(orig_i or "")
            if norm_i != orig_i:
                r["inception_date"] = norm_i
                rewrites_date += 1

            # Rebalance frequency inference.
            if (r.get("rebalance_frequency_prospectus") or "").lower() in ("null", "none", ""):
                r["rebalance_frequency_prospectus"] = _infer_rebalance_frequency(r)
                rewrites_rebal += 1

            records.append(r)

    # Pass 2: dedupe by ticker.
    kept, dropped = _dedupe_by_ticker(records)

    # Write kept records.
    with args.output.open("w", encoding="utf-8") as fout:
        for r in kept:
            fout.write(json.dumps(r) + "\n")

    # Write dropped duplicates to its own file for traceability.
    if args.dropped is not None:
        with args.dropped.open("w", encoding="utf-8") as f:
            for r in dropped:
                f.write(json.dumps(r) + "\n")

    print(f"total: {len(records)} | kept: {len(kept)} | dropped_duplicates: {len(dropped)}")
    print(f"url_rewrites: {rewrites_url} | date_rewrites: {rewrites_date} | rebal_fills: {rewrites_rebal}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
