#!/usr/bin/env python3
"""
Simulation Run Profiler
=======================

Extracts simulation results from PostgreSQL, builds a flattened feature matrix
from trace_json, and generates a human-readable statistical profile.

Outputs:
  <outdir>/data/trades.csv   — All sim result rows + opportunity data
  <outdir>/data/features.csv — Flattened trace_json feature matrix
  <outdir>/profile.md        — Statistical profile report

Usage:
    # In Docker (production server):
    docker compose -f docker-compose.yml run --rm -T worker \
      python3 /scripts/sim_run_profiler.py \
      --run-id d0987846 --outdir /tmp/sim_d0987846/

    # On host (dev):
    python Backend/scripts/sim_run_profiler.py \
      --run-id d0987846 --outdir analysis/sim_runs/d0987846/

Requires DATABASE_URL env var (reads from .env if available).
"""

import os
import sys
import csv
import json
import time
import argparse
import logging
from datetime import datetime, timezone
from collections import defaultdict, Counter

# Load .env if available
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─── Helpers ────────────────────────────────────────────────────────────────

def _to_float(value):
    """Convert value to float, returning None on failure or NaN/Inf."""
    if value is None:
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        if value != value or value == float('inf') or value == float('-inf'):
            return None
        return float(value)
    if isinstance(value, str):
        try:
            f = float(value)
            if f != f or f == float('inf') or f == float('-inf'):
                return None
            return f
        except (ValueError, TypeError):
            return None
    return None


def _safe_div(num, denom, default=0.0):
    """Safe division returning default on zero/None denominator."""
    if not denom:
        return default
    return num / denom


def _pct(count, total):
    """Format a percentage string."""
    if not total:
        return "0.0%"
    return f"{100 * count / total:.1f}%"


def _fmt(val, decimals=4):
    """Format a float for display."""
    if val is None:
        return "—"
    return f"{val:.{decimals}f}"


def _resolve_run_id(conn, prefix):
    """Resolve a run ID prefix to a full UUID. Fail if 0 or >1 match."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM runs WHERE id::text LIKE %s",
            (f"{prefix}%",),
        )
        rows = cur.fetchall()
    if len(rows) == 0:
        logger.error(f"No run found matching prefix '{prefix}'")
        sys.exit(1)
    if len(rows) > 1:
        logger.error(
            f"Ambiguous prefix '{prefix}' matches {len(rows)} runs: "
            + ", ".join(str(r[0]) for r in rows[:5])
        )
        sys.exit(1)
    return str(rows[0][0])


# ─── Phase A: Extract trades.csv ────────────────────────────────────────────

TRADE_COLUMNS = [
    "id", "run_id", "opportunity_id", "symbol", "timeframe", "setup_id",
    "direction", "opportunity_created_ts", "entry_ts", "entry_price",
    "entry_price_with_slippage", "position_size", "stop_price",
    "tp1_price", "tp2_price", "r_value", "exit_ts", "exit_price",
    "exit_price_with_slippage", "exit_reason", "bars_held",
    "pnl_gross", "fees", "pnl_net", "r_multiple",
    "data_available", "error_message",
    # From opportunities join
    "opportunity_score", "opportunity_payload_json",
]

TRADE_QUERY = """
    SELECT
        sr.id, sr.run_id, sr.opportunity_id, sr.symbol, sr.timeframe,
        sr.setup_id, sr.direction, sr.opportunity_created_ts,
        sr.entry_ts, sr.entry_price, sr.entry_price_with_slippage,
        sr.position_size, sr.stop_price, sr.tp1_price, sr.tp2_price,
        sr.r_value, sr.exit_ts, sr.exit_price, sr.exit_price_with_slippage,
        sr.exit_reason, sr.bars_held,
        sr.pnl_gross, sr.fees, sr.pnl_net, sr.r_multiple,
        sr.data_available, sr.error_message,
        o.score AS opportunity_score,
        o.payload_json AS opportunity_payload_json
    FROM simulation_results sr
    LEFT JOIN opportunities o ON sr.opportunity_id = o.id
    WHERE sr.run_id = %s
    ORDER BY sr.opportunity_created_ts, sr.symbol
"""

# Separate query to get trace_json (not written to trades.csv, used for features)
TRACE_QUERY = """
    SELECT sr.id, sr.trace_json
    FROM simulation_results sr
    WHERE sr.run_id = %s
    ORDER BY sr.opportunity_created_ts, sr.symbol
"""


def extract_trades(conn, run_id, outdir):
    """Phase A: Extract all simulation results + opportunity data to CSV."""
    t0 = time.time()
    logger.info("Phase A: Extracting trades...")

    with conn.cursor() as cur:
        cur.execute(TRADE_QUERY, (run_id,))
        rows = cur.fetchall()

    csv_path = os.path.join(outdir, "data", "trades.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(TRADE_COLUMNS)
        for row in rows:
            writer.writerow(row)

    elapsed = time.time() - t0
    logger.info(f"Phase A complete: {len(rows)} trades → {csv_path} ({elapsed:.1f}s)")
    return rows


# ─── Phase B: Extract features.csv ──────────────────────────────────────────

# Namespace mapping for trace_json top-level keys
NAMESPACE_MAP = {
    "indicator_values": "ind",
    "state_snapshot": "state",
    "score_components": "score",
    "conditions_met": "cond",
    "exit_conditions": "exit",
    "exit_indicator_values": "exit_ind",
}


def _flatten_trace(trace):
    """
    Flatten trace_json into namespaced key-value pairs.

    Returns dict of {namespaced_key: value_as_string}.
    Booleans → "0"/"1". Strings preserved. Numbers as-is.
    Missing/None → empty string (loads as NaN in pandas).
    """
    flat = {}
    if not trace:
        return flat

    # Handle known nested dicts
    for json_key, ns in NAMESPACE_MAP.items():
        section = trace.get(json_key)
        if not isinstance(section, dict):
            continue
        for k, v in section.items():
            col = f"{ns}.{k}"
            if isinstance(v, bool):
                flat[col] = "1" if v else "0"
            elif v is None:
                flat[col] = ""
            else:
                flat[col] = str(v)

    # Handle root-level booleans and meaningful strings
    root_keys = {
        "tp_invalidated": "meta.tp_invalidated",
        "tp_invalidation_reason": "meta.tp_invalidation_reason",
        "exit_reason": "meta.exit_reason",
        "entry_reason": "meta.entry_reason",
        "exit_explanation": "meta.exit_explanation",
        "rejection_reason": "meta.rejection_reason",
    }
    for json_key, col in root_keys.items():
        v = trace.get(json_key)
        if v is None:
            continue
        if isinstance(v, bool):
            flat[col] = "1" if v else "0"
        else:
            flat[col] = str(v)

    # Handle failed_gates (list → comma-separated string)
    fg = trace.get("failed_gates")
    if isinstance(fg, list) and fg:
        flat["meta.failed_gates"] = ",".join(str(g) for g in fg)

    return flat


def extract_features(conn, run_id, outdir):
    """
    Phase B: Parse trace_json from all sim results, build a fixed-width
    feature matrix CSV with two passes (discover keys, then write rows).
    """
    t0 = time.time()
    logger.info("Phase B: Extracting features from trace_json...")

    with conn.cursor() as cur:
        cur.execute(TRACE_QUERY, (run_id,))
        raw_rows = cur.fetchall()

    # Parse all trace_json
    parsed = []  # list of (id, flat_dict)
    all_keys = set()
    traces_found = 0
    traces_missing = 0

    for row_id, trace_json in raw_rows:
        trace = None
        if trace_json is not None:
            if isinstance(trace_json, dict):
                trace = trace_json
            elif isinstance(trace_json, str):
                try:
                    trace = json.loads(trace_json)
                except (json.JSONDecodeError, TypeError):
                    trace = None

        if trace:
            traces_found += 1
            flat = _flatten_trace(trace)
        else:
            traces_missing += 1
            flat = {}

        all_keys.update(flat.keys())
        parsed.append((str(row_id), flat))

    # Sort columns for deterministic output
    sorted_keys = sorted(all_keys)

    # Write CSV
    csv_path = os.path.join(outdir, "data", "features.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id"] + sorted_keys)
        for row_id, flat in parsed:
            row = [row_id] + [flat.get(k, "") for k in sorted_keys]
            writer.writerow(row)

    # Coverage gap table
    key_coverage = Counter()
    for _, flat in parsed:
        for k in flat:
            if flat[k] != "":
                key_coverage[k] += 1

    logger.info(f"Phase B complete: {len(parsed)} rows × {len(sorted_keys)} features → {csv_path}")
    logger.info(f"  trace_json present: {traces_found}, missing: {traces_missing}")

    # Print coverage gap table
    if sorted_keys:
        logger.info("Feature coverage:")
        for ns in ["ind", "state", "score", "cond", "exit", "exit_ind", "meta"]:
            ns_keys = [k for k in sorted_keys if k.startswith(f"{ns}.")]
            if ns_keys:
                logger.info(f"  {ns}.*:")
                for k in ns_keys:
                    count = key_coverage.get(k, 0)
                    pct = 100 * count / len(parsed) if parsed else 0
                    logger.info(f"    {k:40s}  {count:>6d}/{len(parsed)}  ({pct:5.1f}%)")

    elapsed = time.time() - t0
    logger.info(f"Phase B elapsed: {elapsed:.1f}s")
    return parsed, sorted_keys


# ─── Phase C: Generate profile.md ───────────────────────────────────────────

def _compute_metrics(trades):
    """
    Compute standard metrics from a list of trade dicts.
    Returns dict with: count, wins, losses, win_rate, total_pnl, avg_pnl,
    profit_factor, avg_r, median_r, max_dd_trade, avg_bars_held.
    """
    if not trades:
        return {
            "count": 0, "wins": 0, "losses": 0, "win_rate": 0,
            "total_pnl": 0, "avg_pnl": 0, "profit_factor": 0,
            "avg_r": 0, "median_r": 0, "max_dd_trade": 0,
            "avg_bars_held": 0, "sum_wins": 0, "sum_losses": 0,
        }

    pnls = [t["pnl_net"] for t in trades if t["pnl_net"] is not None]
    r_mults = [t["r_multiple"] for t in trades if t["r_multiple"] is not None]
    bars = [t["bars_held"] for t in trades if t["bars_held"] is not None]

    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    sum_wins = sum(wins) if wins else 0
    sum_losses = abs(sum(losses)) if losses else 0

    sorted_r = sorted(r_mults) if r_mults else [0]
    n = len(sorted_r)
    median_r = sorted_r[n // 2] if n % 2 == 1 else (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2

    return {
        "count": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(pnls) if pnls else 0,
        "total_pnl": sum(pnls) if pnls else 0,
        "avg_pnl": sum(pnls) / len(pnls) if pnls else 0,
        "profit_factor": _safe_div(sum_wins, sum_losses),
        "avg_r": sum(r_mults) / len(r_mults) if r_mults else 0,
        "median_r": median_r,
        "max_dd_trade": min(pnls) if pnls else 0,
        "avg_bars_held": sum(bars) / len(bars) if bars else 0,
        "sum_wins": sum_wins,
        "sum_losses": sum_losses,
    }


def _metrics_row(label, m):
    """Format a metrics dict as a markdown table row."""
    return (
        f"| {label:24s} "
        f"| {m['count']:>6d} "
        f"| {_pct(m['wins'], m['count']):>7s} "
        f"| {_fmt(m['total_pnl'], 2):>12s} "
        f"| {_fmt(m['avg_pnl'], 2):>10s} "
        f"| {_fmt(m['profit_factor'], 2):>7s} "
        f"| {_fmt(m['avg_r'], 2):>7s} "
        f"| {_fmt(m['median_r'], 2):>9s} "
        f"| {_fmt(m['max_dd_trade'], 2):>12s} "
        f"| {_fmt(m['avg_bars_held'], 1):>9s} |"
    )


METRICS_HEADER = (
    "| Segment                  "
    "| Trades "
    "| WinRate "
    "| Total PnL    "
    "| Avg PnL    "
    "| PF      "
    "| Avg R   "
    "| Median R  "
    "| Max DD Trade "
    "| Avg Bars  |"
)
METRICS_SEP = (
    "|:-------------------------|"
    "-------:|"
    "--------:|"
    "-------------:|"
    "-----------:|"
    "--------:|"
    "--------:|"
    "----------:|"
    "-------------:|"
    "----------:|"
)


def _segment_table(title, groups):
    """Build a markdown table with metrics for each group."""
    lines = [f"### {title}\n", METRICS_HEADER, METRICS_SEP]
    for label, trades in sorted(groups.items()):
        m = _compute_metrics(trades)
        lines.append(_metrics_row(label, m))
    lines.append("")
    return "\n".join(lines)


def generate_profile(conn, run_id, trade_rows, outdir):
    """
    Phase C: Generate profile.md from trade data.
    trade_rows: list of tuples from Phase A query.
    """
    t0 = time.time()
    logger.info("Phase C: Generating profile...")

    # Get run metadata
    with conn.cursor() as cur:
        cur.execute(
            """SELECT id, run_type, status, name, engine_version,
                      strategy_json, summary_json,
                      created_at, started_at, completed_at, duration_seconds
               FROM runs WHERE id = %s""",
            (run_id,),
        )
        run_row = cur.fetchone()

    # Build dicts from trade_rows for easier access
    trades = []
    for row in trade_rows:
        t = dict(zip(TRADE_COLUMNS, row))
        # Coerce types
        for fld in ["pnl_net", "pnl_gross", "fees", "r_multiple", "r_value",
                     "entry_price", "exit_price", "stop_price",
                     "tp1_price", "tp2_price", "position_size",
                     "entry_price_with_slippage", "exit_price_with_slippage",
                     "opportunity_score"]:
            t[fld] = _to_float(t.get(fld))
        for fld in ["bars_held"]:
            t[fld] = int(t[fld]) if t[fld] is not None else None
        t["data_available"] = str(t.get("data_available", "")).lower() in ("true", "t", "1")
        trades.append(t)

    # Separate valid trades from data-unavailable / errored
    valid_trades = [t for t in trades if t["data_available"] and t["exit_reason"] is not None]
    no_data = [t for t in trades if not t["data_available"]]
    errored = [t for t in trades if t["error_message"] and str(t["error_message"]).strip()]

    lines = []
    lines.append(f"# Simulation Profile: {run_id[:8]}\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")

    # ── A. Run metadata ──────────────────────────────────────────────────
    lines.append("## A. Run Metadata\n")
    if run_row:
        (rid, rtype, rstatus, rname, rengine,
         strategy_json, summary_json,
         created, started, completed, duration) = run_row
        lines.append(f"- **Run ID:** `{rid}`")
        lines.append(f"- **Type:** {rtype}")
        lines.append(f"- **Status:** {rstatus}")
        lines.append(f"- **Name:** {rname or '—'}")
        lines.append(f"- **Engine:** {rengine or '—'}")
        lines.append(f"- **Created:** {created}")
        lines.append(f"- **Started:** {started}")
        lines.append(f"- **Completed:** {completed}")
        lines.append(f"- **Duration:** {_fmt(duration, 1)}s")

        # Parse summary_json if available
        summary = None
        if summary_json:
            if isinstance(summary_json, str):
                try:
                    summary = json.loads(summary_json)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(summary_json, dict):
                summary = summary_json
        if summary:
            lines.append(f"- **Summary:** `{json.dumps(summary, indent=2)[:500]}`")
    else:
        lines.append("- Run metadata not found in DB")
    lines.append("")

    # ── B. Data Quality ──────────────────────────────────────────────────
    lines.append("## B. Data Quality\n")
    lines.append(f"- **Total rows:** {len(trades)}")
    lines.append(f"- **Data available (valid):** {len(valid_trades)}")
    lines.append(f"- **Data unavailable:** {len(no_data)}")
    lines.append(f"- **With error messages:** {len(errored)}")
    lines.append("")

    # data_available breakdown by setup
    da_by_setup = defaultdict(lambda: {"available": 0, "unavailable": 0})
    for t in trades:
        sid = t.get("setup_id", "?")
        if t["data_available"]:
            da_by_setup[sid]["available"] += 1
        else:
            da_by_setup[sid]["unavailable"] += 1

    lines.append("### Data availability by setup\n")
    lines.append("| Setup | Available | Unavailable | % Available |")
    lines.append("|:------|----------:|------------:|------------:|")
    for sid in sorted(da_by_setup.keys()):
        a = da_by_setup[sid]["available"]
        u = da_by_setup[sid]["unavailable"]
        lines.append(f"| {sid:5s} | {a:>9d} | {u:>11d} | {_pct(a, a + u):>11s} |")
    lines.append("")

    # Error taxonomy
    if errored:
        err_counts = Counter(t.get("error_message", "?") for t in errored)
        lines.append("### Error taxonomy\n")
        lines.append("| Error | Count |")
        lines.append("|:------|------:|")
        for err, cnt in err_counts.most_common(20):
            # Truncate long error messages
            err_short = str(err)[:80]
            lines.append(f"| {err_short} | {cnt:>5d} |")
        lines.append("")

    # ── C. Gate Impact ────────────────────────────────────────────────────
    lines.append("## C. Gate Impact\n")

    # Parse payload_json to find rejection reasons / failed gates
    rejected = []
    for t in trades:
        pj = t.get("opportunity_payload_json")
        if not pj:
            continue
        payload = None
        if isinstance(pj, str):
            try:
                payload = json.loads(pj)
            except (json.JSONDecodeError, TypeError):
                continue
        elif isinstance(pj, dict):
            payload = pj
        if not payload:
            continue

        rr = payload.get("rejection_reason")
        fg = payload.get("failed_gates")
        if rr or fg:
            rejected.append({
                "symbol": t["symbol"],
                "setup_id": t["setup_id"],
                "rejection_reason": rr,
                "failed_gates": fg,
                "r_multiple": t["r_multiple"],
                "pnl_net": t["pnl_net"],
            })

    if rejected:
        # Rejection reason breakdown
        reason_counts = Counter(r.get("rejection_reason", "unknown") for r in rejected)
        lines.append("### Rejection reasons\n")
        lines.append("| Reason | Count |")
        lines.append("|:-------|------:|")
        for reason, cnt in reason_counts.most_common():
            lines.append(f"| {reason} | {cnt:>5d} |")
        lines.append("")

        # Hypothetical R from rejected trades
        rejected_with_r = [r for r in rejected if r["r_multiple"] is not None]
        if rejected_with_r:
            hyp_r = [r["r_multiple"] for r in rejected_with_r]
            lines.append("### Hypothetical R from rejected trades\n")
            lines.append(f"- **Count:** {len(hyp_r)}")
            lines.append(f"- **Avg R:** {_fmt(sum(hyp_r) / len(hyp_r), 2)}")
            lines.append(f"- **Sum R:** {_fmt(sum(hyp_r), 2)}")
            winners = [r for r in hyp_r if r >= 0]
            lines.append(f"- **Win rate:** {_pct(len(winners), len(hyp_r))}")
            lines.append("")
    else:
        lines.append("No rejection/gate data found in opportunity payloads.\n")

    # ── D. Segmentation ──────────────────────────────────────────────────
    lines.append("## D. Segmentation\n")

    # Aggregate metrics for valid trades
    all_m = _compute_metrics(valid_trades)
    lines.append("### Overall\n")
    lines.append(METRICS_HEADER)
    lines.append(METRICS_SEP)
    lines.append(_metrics_row("ALL", all_m))
    lines.append("")

    # D1. By setup_id
    by_setup = defaultdict(list)
    for t in valid_trades:
        by_setup[t.get("setup_id", "?")].append(t)
    lines.append(_segment_table("D1. By Setup", by_setup))

    # D2. By direction
    by_dir = defaultdict(list)
    for t in valid_trades:
        by_dir[t.get("direction", "?")].append(t)
    lines.append(_segment_table("D2. By Direction", by_dir))

    # D3. By timeframe
    by_tf = defaultdict(list)
    for t in valid_trades:
        by_tf[t.get("timeframe", "?")].append(t)
    lines.append(_segment_table("D3. By Timeframe", by_tf))

    # D4. By exit_reason
    by_exit = defaultdict(list)
    for t in valid_trades:
        by_exit[t.get("exit_reason", "?")].append(t)
    lines.append(_segment_table("D4. By Exit Reason", by_exit))

    # D5. By setup × direction
    by_setup_dir = defaultdict(list)
    for t in valid_trades:
        key = f"{t.get('setup_id', '?')} / {t.get('direction', '?')}"
        by_setup_dir[key].append(t)
    lines.append(_segment_table("D5. By Setup × Direction", by_setup_dir))

    # D6. By setup × timeframe
    by_setup_tf = defaultdict(list)
    for t in valid_trades:
        key = f"{t.get('setup_id', '?')} / {t.get('timeframe', '?')}"
        by_setup_tf[key].append(t)
    lines.append(_segment_table("D6. By Setup × Timeframe", by_setup_tf))

    # D7. By R-multiple bucket
    by_r_bucket = defaultdict(list)
    for t in valid_trades:
        r = t["r_multiple"]
        if r is None:
            bucket = "N/A"
        elif r <= -1:
            bucket = "R ≤ -1"
        elif r < 0:
            bucket = "-1 < R < 0"
        elif r == 0:
            bucket = "R = 0"
        elif r <= 1:
            bucket = "0 < R ≤ 1"
        elif r <= 3:
            bucket = "1 < R ≤ 3"
        elif r <= 5:
            bucket = "3 < R ≤ 5"
        else:
            bucket = "R > 5"
        by_r_bucket[bucket].append(t)
    lines.append(_segment_table("D7. By R-Multiple Bucket", by_r_bucket))

    # ── E. Exit Mechanics ────────────────────────────────────────────────
    lines.append("## E. Exit Mechanics\n")

    # E1. Exit reason distribution
    exit_counts = Counter(t.get("exit_reason", "?") for t in valid_trades)
    lines.append("### E1. Exit Reason Distribution\n")
    lines.append("| Exit Reason  | Count | % of Total |")
    lines.append("|:-------------|------:|-----------:|")
    for reason, cnt in exit_counts.most_common():
        lines.append(f"| {reason:12s} | {cnt:>5d} | {_pct(cnt, len(valid_trades)):>10s} |")
    lines.append("")

    # E2. Bars held distribution (percentiles)
    bars_list = sorted([t["bars_held"] for t in valid_trades if t["bars_held"] is not None])
    if bars_list:
        def _percentile(arr, p):
            idx = int(len(arr) * p / 100)
            idx = min(idx, len(arr) - 1)
            return arr[idx]

        lines.append("### E2. Bars Held Distribution\n")
        lines.append("| Percentile | Bars |")
        lines.append("|-----------:|-----:|")
        for p in [5, 10, 25, 50, 75, 90, 95]:
            lines.append(f"| {p}th | {_percentile(bars_list, p):>4d} |")
        lines.append(f"| Mean | {_fmt(sum(bars_list) / len(bars_list), 1):>4s} |")
        lines.append("")

    # E3. Bars held by exit reason
    bars_by_exit = defaultdict(list)
    for t in valid_trades:
        if t["bars_held"] is not None:
            bars_by_exit[t.get("exit_reason", "?")].append(t["bars_held"])
    if bars_by_exit:
        lines.append("### E3. Bars Held by Exit Reason\n")
        lines.append("| Exit Reason  | Count | Mean  | Median | Min | Max |")
        lines.append("|:-------------|------:|------:|-------:|----:|----:|")
        for reason in sorted(bars_by_exit.keys()):
            b = sorted(bars_by_exit[reason])
            n = len(b)
            mean = sum(b) / n
            med = b[n // 2] if n % 2 == 1 else (b[n // 2 - 1] + b[n // 2]) / 2
            lines.append(
                f"| {reason:12s} | {n:>5d} | {mean:>5.1f} | {med:>6.1f} | {min(b):>3d} | {max(b):>3d} |"
            )
        lines.append("")

    # E4. TP invalidation
    # Load trace_json for TP invalidation stats
    tp_inv_count = 0
    tp_inv_reasons = Counter()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT trace_json FROM simulation_results WHERE run_id = %s AND data_available = TRUE",
            (run_id,),
        )
        for (tj,) in cur.fetchall():
            trace = None
            if isinstance(tj, str):
                try:
                    trace = json.loads(tj)
                except (json.JSONDecodeError, TypeError):
                    pass
            elif isinstance(tj, dict):
                trace = tj
            if trace and trace.get("tp_invalidated"):
                tp_inv_count += 1
                reason = trace.get("tp_invalidation_reason", "unknown")
                tp_inv_reasons[reason] += 1

    lines.append("### E4. TP Invalidation\n")
    lines.append(f"- **TP invalidated:** {tp_inv_count} / {len(valid_trades)} ({_pct(tp_inv_count, len(valid_trades))})")
    if tp_inv_reasons:
        lines.append("\n| Reason | Count |")
        lines.append("|:-------|------:|")
        for reason, cnt in tp_inv_reasons.most_common():
            lines.append(f"| {reason} | {cnt:>5d} |")
    lines.append("")

    # E5. Time-of-day analysis (by hour of opportunity_created_ts)
    by_hour = defaultdict(list)
    for t in valid_trades:
        ts = t.get("opportunity_created_ts")
        if ts:
            if isinstance(ts, str):
                # Parse ISO format
                try:
                    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    by_hour[dt.hour].append(t)
                except (ValueError, TypeError):
                    pass
            elif isinstance(ts, datetime):
                by_hour[ts.hour].append(t)
    if by_hour:
        lines.append("### E5. Performance by Hour (UTC)\n")
        lines.append("| Hour | Trades | Win Rate | Avg R   | Total PnL   |")
        lines.append("|-----:|-------:|---------:|--------:|------------:|")
        for h in range(24):
            if h in by_hour:
                hm = _compute_metrics(by_hour[h])
                lines.append(
                    f"| {h:>4d} | {hm['count']:>6d} | {_pct(hm['wins'], hm['count']):>8s} "
                    f"| {_fmt(hm['avg_r'], 2):>7s} | {_fmt(hm['total_pnl'], 2):>11s} |"
                )
        lines.append("")

    # ── F. Outlier Flags ─────────────────────────────────────────────────
    lines.append("## F. Outlier Flags\n")

    # F1. Top 10 winners
    winners_sorted = sorted(
        [t for t in valid_trades if t["r_multiple"] is not None],
        key=lambda t: t["r_multiple"],
        reverse=True,
    )
    lines.append("### F1. Top 10 Winners\n")
    lines.append("| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |")
    lines.append("|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|")
    for i, t in enumerate(winners_sorted[:10], 1):
        lines.append(
            f"| {i} | {t['symbol']:10s} | {t['timeframe']:3s} | {t['setup_id']:5s} "
            f"| {t['direction']:5s} | {_fmt(t['r_multiple'], 2):>9s} | {_fmt(t['pnl_net'], 2):>9s} "
            f"| {t.get('exit_reason', '?'):12s} | {t.get('bars_held', 0):>4} |"
        )
    lines.append("")

    # F2. Top 10 losers
    losers_sorted = sorted(
        [t for t in valid_trades if t["r_multiple"] is not None],
        key=lambda t: t["r_multiple"],
    )
    lines.append("### F2. Top 10 Losers\n")
    lines.append("| # | Symbol     | TF  | Setup | Dir   | R Multiple | PnL Net   | Exit Reason  | Bars |")
    lines.append("|--:|:-----------|:----|:------|:------|----------:|----------:|:-------------|-----:|")
    for i, t in enumerate(losers_sorted[:10], 1):
        lines.append(
            f"| {i} | {t['symbol']:10s} | {t['timeframe']:3s} | {t['setup_id']:5s} "
            f"| {t['direction']:5s} | {_fmt(t['r_multiple'], 2):>9s} | {_fmt(t['pnl_net'], 2):>9s} "
            f"| {t.get('exit_reason', '?'):12s} | {t.get('bars_held', 0):>4} |"
        )
    lines.append("")

    # F3. Highest bars held
    long_holds = sorted(
        [t for t in valid_trades if t["bars_held"] is not None],
        key=lambda t: t["bars_held"],
        reverse=True,
    )
    lines.append("### F3. Top 10 Longest Held\n")
    lines.append("| # | Symbol     | TF  | Setup | Dir   | Bars | R Multiple | Exit Reason  |")
    lines.append("|--:|:-----------|:----|:------|:------|-----:|----------:|:-------------|")
    for i, t in enumerate(long_holds[:10], 1):
        lines.append(
            f"| {i} | {t['symbol']:10s} | {t['timeframe']:3s} | {t['setup_id']:5s} "
            f"| {t['direction']:5s} | {t['bars_held']:>4d} | {_fmt(t['r_multiple'], 2):>9s} "
            f"| {t.get('exit_reason', '?'):12s} |"
        )
    lines.append("")

    # F4. Score vs outcome (high score losers / low score winners)
    scored = [t for t in valid_trades if t["opportunity_score"] is not None and t["r_multiple"] is not None]
    if scored:
        # High score (top quartile) losers
        scores = sorted([t["opportunity_score"] for t in scored])
        q75 = scores[int(len(scores) * 0.75)] if scores else 0
        q25 = scores[int(len(scores) * 0.25)] if scores else 0

        high_score_losers = sorted(
            [t for t in scored if t["opportunity_score"] >= q75 and t["r_multiple"] < 0],
            key=lambda t: t["r_multiple"],
        )
        low_score_winners = sorted(
            [t for t in scored if t["opportunity_score"] <= q25 and t["r_multiple"] > 0],
            key=lambda t: t["r_multiple"],
            reverse=True,
        )

        lines.append("### F4. Score Anomalies\n")
        lines.append(f"Score Q25={_fmt(q25, 3)}, Q75={_fmt(q75, 3)}\n")

        lines.append(f"**High-score losers (score ≥ Q75, R < 0): {len(high_score_losers)}**\n")
        if high_score_losers:
            lines.append("| Symbol     | Setup | Score  | R Multiple | Exit Reason  |")
            lines.append("|:-----------|:------|-------:|----------:|:-------------|")
            for t in high_score_losers[:10]:
                lines.append(
                    f"| {t['symbol']:10s} | {t['setup_id']:5s} | {_fmt(t['opportunity_score'], 3):>6s} "
                    f"| {_fmt(t['r_multiple'], 2):>9s} | {t.get('exit_reason', '?'):12s} |"
                )
            lines.append("")

        lines.append(f"**Low-score winners (score ≤ Q25, R > 0): {len(low_score_winners)}**\n")
        if low_score_winners:
            lines.append("| Symbol     | Setup | Score  | R Multiple | Exit Reason  |")
            lines.append("|:-----------|:------|-------:|----------:|:-------------|")
            for t in low_score_winners[:10]:
                lines.append(
                    f"| {t['symbol']:10s} | {t['setup_id']:5s} | {_fmt(t['opportunity_score'], 3):>6s} "
                    f"| {_fmt(t['r_multiple'], 2):>9s} | {t.get('exit_reason', '?'):12s} |"
                )
            lines.append("")

    # Write profile
    md_path = os.path.join(outdir, "profile.md")
    with open(md_path, "w") as f:
        f.write("\n".join(lines))

    elapsed = time.time() - t0
    logger.info(f"Phase C complete: profile → {md_path} ({elapsed:.1f}s)")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract simulation results, build feature matrix, and generate statistical profile.",
    )
    parser.add_argument(
        "--run-id", required=True,
        help="Run UUID or unique prefix (e.g., d0987846)",
    )
    parser.add_argument(
        "--outdir", required=True,
        help="Output directory (created if missing)",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="PostgreSQL connection URL (default: DATABASE_URL env var)",
    )
    args = parser.parse_args()

    if not args.database_url:
        logger.error("DATABASE_URL not set. Pass --database-url or set env var.")
        sys.exit(1)

    # Create output dirs
    data_dir = os.path.join(args.outdir, "data")
    os.makedirs(data_dir, exist_ok=True)
    logger.info(f"Output directory: {args.outdir}")

    t_start = time.time()

    # Connect (import deferred so --help works without psycopg installed)
    import psycopg
    conn = psycopg.connect(args.database_url)
    try:
        # Resolve run ID
        run_id = _resolve_run_id(conn, args.run_id)
        logger.info(f"Resolved run ID: {run_id}")

        # Phase A: trades.csv
        trade_rows = extract_trades(conn, run_id, args.outdir)

        # Phase B: features.csv
        extract_features(conn, run_id, args.outdir)

        # Phase C: profile.md
        generate_profile(conn, run_id, trade_rows, args.outdir)

    finally:
        conn.close()

    elapsed = time.time() - t_start
    logger.info(f"All phases complete in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
