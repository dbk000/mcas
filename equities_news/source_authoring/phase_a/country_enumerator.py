"""Phase A.1 — country enumeration.

Reads ground_truth/bible_v1.xlsx, extracts unique Country values across all
sector sheets, maps to ISO 3166-1 alpha-2 codes via iso_3166_codes.yaml,
and emits registry/countries_in_universe.yaml with per-country symbol count.

Any Country value not found in iso_3166_codes.yaml is a hard error — do NOT
silently drop. Add the mapping to iso_3166_codes.yaml and re-run.
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import openpyxl

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import GROUND_TRUTH_DIR, REGISTRY_DIR, dump_yaml, load_yaml, utc_now_iso


def enumerate_countries() -> dict:
    bible = GROUND_TRUTH_DIR / "bible_v1.xlsx"
    if not bible.exists():
        raise SystemExit(f"bible missing: {bible}")

    iso = load_yaml(REGISTRY_DIR / "iso_3166_codes.yaml")
    name_to_code: dict[str, str] = iso["country_to_code"]

    wb = openpyxl.load_workbook(bible, read_only=True, data_only=True)
    sector_counts: dict[str, Counter] = {}
    overall: Counter = Counter()
    unknown: Counter = Counter()

    for sheet_name in wb.sheetnames:
        if sheet_name == "Overview":
            continue
        ws = wb[sheet_name]
        sc: Counter = Counter()
        # Header is row 4 (index 3), data starts row 5.
        for row in ws.iter_rows(min_row=5, values_only=True):
            if not row or row[1] is None:
                continue
            ticker = row[1]
            if not isinstance(ticker, str):
                continue
            country = row[4]
            if country is None:
                continue
            sc[country] += 1
            overall[country] += 1
        sector_counts[sheet_name] = sc

    # Map to ISO codes.
    country_records: dict[str, dict] = {}
    for country_name, n in sorted(overall.items(), key=lambda kv: -kv[1]):
        code = name_to_code.get(country_name)
        if code is None:
            unknown[country_name] = n
            continue
        country_records[code] = {
            "country_name": country_name,
            "iso_alpha_2": code,
            "symbol_count": int(n),
            "sector_distribution": {
                sector: int(sector_counts[sector][country_name])
                for sector in sector_counts
                if sector_counts[sector][country_name] > 0
            },
        }

    if unknown:
        msg = "Unknown country names (add to iso_3166_codes.yaml):\n  " + "\n  ".join(
            f"{k!r}: {v} symbols" for k, v in unknown.items()
        )
        raise SystemExit(msg)

    doc = {
        "version": 1,
        "generated_at": utc_now_iso(),
        "source": "ground_truth/bible_v1.xlsx",
        "total_symbols": int(sum(overall.values())),
        "total_countries": len(country_records),
        "countries": country_records,
    }
    return doc


def main() -> int:
    doc = enumerate_countries()
    out = REGISTRY_DIR / "countries_in_universe.yaml"
    dump_yaml(out, doc)
    print(f"wrote {out}")
    print(f"  total_symbols={doc['total_symbols']}  total_countries={doc['total_countries']}")
    for code, rec in sorted(doc["countries"].items(), key=lambda kv: -kv[1]["symbol_count"]):
        print(f"  {code}  {rec['symbol_count']:>5}  {rec['country_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
