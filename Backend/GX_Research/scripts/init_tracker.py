"""Initialise GX_Research_Tracker.xlsx and populate universe + slippage tables."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent.parent))  # so `from GX_Research.lib import ...` works

from GX_Research.lib import tracker, db, slippage
from GX_Research.scripts._common import UNIVERSE_PATH, RESEARCH_START, RESEARCH_END, log


def main():
    log(f"Initialising tracker at {tracker.TRACKER_PATH}")
    tracker.initialise()
    universe = db.load_universe(UNIVERSE_PATH)
    log(f"Universe loaded: {len(universe)} symbols")

    # Populate universe snapshot
    rows = []
    for sym in universe:
        quote_ccy = "USDC" if sym.endswith("USDC") else ("USDT" if sym.endswith("USDT") else "OTHER")
        data_sym = db.logical_to_data_symbol(sym)
        rows.append({
            "utc_ts": tracker.utc_now_iso(),
            "symbol": sym, "quote_ccy": quote_ccy,
            "data_symbol": data_sym, "tier": "",
        })
    tracker.append_rows("11_Universe_Snapshot", rows)
    log(f"Appended {len(rows)} rows to 11_Universe_Snapshot")

    # Populate slippage table
    slip_rows = []
    for (tf, tier), bps in slippage.SLIP_TABLE_BPS_RT.items():
        slip_rows.append({
            "utc_ts": tracker.utc_now_iso(),
            "symbol": "<tier>", "tier": tier, "median_24h_quote_vol_usd": "",
            "tf": tf, "slip_bps_roundtrip": bps,
        })
    tracker.append_rows("10_Slippage_Table", slip_rows)

    # Plan history entry
    tracker.append_rows("01_Plan_History", [{
        "version": "v4", "utc_ts": tracker.utc_now_iso(),
        "author": "gx-build",
        "change_summary": "Initial build. 288 USDC universe; research window 2025-01-01→2026-04-14; holdout 15%; USDT data substitution for USDC (deferred transportability check).",
        "git_sha": "",
    }])

    log("Tracker initialised successfully.")


if __name__ == "__main__":
    main()
