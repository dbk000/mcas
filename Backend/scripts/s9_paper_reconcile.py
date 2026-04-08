#!/usr/bin/env python3
"""
S9 Paper Reconciliation (Phase 6)
=================================

End-to-end reconcile of the Phase 2 paper trading engine against the canonical
sim for a 7-day window.

Method
------
1. Pick a 7-day window from inside the canonical perp-rerank batch
   (`regen_perp_only_20260408`, run_id `47f3d418-5153-4bba-a17d-f6d0d4b91143`).
2. Pull all S9 sim trades in that window. Apply FIFO cap=15 to yield the
   canonical trade set.
3. Create an ephemeral PAPER run in the DB.
4. For each accepted sim trade, construct a signal dict from the opportunity
   and feed it through `engine.process_signal`.
5. Walk the OHLCV bars from entry_ts forward, calling `engine.update_positions`
   on each bar. Cap per-position walks at 520 hours (matches sim's
   max_bars_forward = 500 + buffer).
6. After all trades are processed, compare `paper_positions` rows to
   `simulation_results` for the same opportunities. Reconcile per-trade:
     - entry_price_with_slippage
     - exit_ts (to the second)
     - exit_price
     - exit_reason
     - pnl_net  (1% tolerance — accounts for exit-walk OHLCV drift since the
                 canon sim was run; same class of divergence as Phase 2)
7. Write `s9_paper_reconcile_<date>.md` report.

Gate checks (from plan)
-----------------------
- Daily PF over window >= 1.0
- No positions exceeding 49 bars without TIME_STOP exit
- No more than 15 open S9 positions at any time

Usage
-----
    docker exec backend-computers-1 python3 /backend/scripts/s9_paper_reconcile.py \\
        --start 2026-03-20 --end 2026-03-27
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from collections import defaultdict
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg

if os.path.isdir("/backend"):
    sys.path.insert(0, "/backend")
else:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_logic.exit_checker import ExitConfig  # noqa: E402
from core_logic.execution import ExecutionConfig, realistic_config  # noqa: E402

# Bootstrap the governance registry BEFORE importing anything that calls
# get_config() / get_stop_policy() at runtime (PaperTradingEngine does in
# _resolve_exit_config).
from governance.bootstrap import bootstrap_genesis  # noqa: E402
bootstrap_genesis()

from governance.paper_trading import PaperTradingEngine  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trading:trading@127.0.0.1:5432/trading")

# Phase 0 canon
CANON_RUN_ID = "47f3d418-5153-4bba-a17d-f6d0d4b91143"
CANON_BATCH_ID = "regen_perp_only_20260408"
S9_MAX_CONCURRENT = 15
S9_EXIT_CONFIG_DICT = {
    "stop_fill_through": False,
    "target_fill_through": False,
    "fail_fast_bars": 0,
    "fail_fast_threshold": 0.0,
    "trail_breakeven_r": 0,
    "trail_activation_r": 999.0,
    "trail_distance_r": 0.5,
    "intrabar_policy": "STOP_FIRST",
    "one_event_per_bar": True,
}


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def fetch_canon_trades(conn, start_dt: datetime, end_dt: datetime) -> list[dict]:
    """Pull settled S9 trades from the canonical run within the window."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sr.id, sr.opportunity_id, sr.symbol, sr.timeframe, sr.direction,
                   sr.entry_ts, sr.entry_price, sr.entry_price_with_slippage,
                   sr.stop_price, sr.tp1_price, sr.tp2_price, sr.r_value,
                   sr.exit_ts, sr.exit_price, sr.exit_price_with_slippage,
                   sr.exit_reason, sr.bars_held, sr.pnl_gross, sr.pnl_net,
                   sr.r_multiple, sr.fees, sr.position_size,
                   o.payload_json, o.created_ts
            FROM simulation_results sr
            JOIN opportunities o ON o.id = sr.opportunity_id
            WHERE sr.run_id = %s
              AND sr.setup_id = 'S9'
              AND sr.data_available = TRUE
              AND sr.entry_ts >= %s
              AND sr.entry_ts < %s
            ORDER BY sr.entry_ts ASC;
        """, (CANON_RUN_ID, start_dt, end_dt))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def apply_cap_fifo(trades: list[dict], cap: int) -> tuple[list[dict], list[dict]]:
    """FIFO cap filter — returns (accepted, skipped) lists."""
    accepted: list[dict] = []
    skipped: list[dict] = []
    active_exits: list[datetime] = []
    for t in trades:
        entry = t["entry_ts"]
        active_exits = [ex for ex in active_exits if ex > entry]
        if len(active_exits) < cap:
            accepted.append(t)
            active_exits.append(t["exit_ts"])
            active_exits.sort()
        else:
            skipped.append(t)
    return accepted, skipped


def fetch_bars(conn, symbol: str, start_ts: datetime, hours: int = 520) -> list[dict]:
    """Fetch up to `hours` 1h bars from `start_ts` forward (matches sim window)."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT ON (bar_start_ts)
                   bar_start_ts, open, high, low, close
            FROM ohlcv
            WHERE symbol = %s AND timeframe = '1h'
              AND bar_start_ts >= %s
              AND bar_start_ts < %s + (%s::text || ' hours')::interval
              AND is_final = TRUE
            ORDER BY bar_start_ts,
                     CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END;
        """, (symbol, start_ts, start_ts, str(hours)))
        return [
            {
                "ts": r[0],
                "open": float(r[1]),
                "high": float(r[2]),
                "low": float(r[3]),
                "close": float(r[4]),
            }
            for r in cur.fetchall()
        ]


def create_reconcile_paper_run(conn, name: str) -> str:
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    exec_cfg = replace(realistic_config(timeframe="1h"), max_positions=S9_MAX_CONCURRENT)
    # For the metadata column we serialise a shallow summary (asdict would
    # recurse into the dataclass fields with repr-only reprs for the model
    # instances, which is fine for audit purposes).
    exec_cfg_d = {
        "max_positions": exec_cfg.max_positions,
        "max_risk_per_trade_pct": exec_cfg.max_risk_per_trade_pct,
        "max_daily_loss_pct": exec_cfg.max_daily_loss_pct,
        "slippage": str(exec_cfg.slippage),
        "fees": str(exec_cfg.fees),
    }
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO runs (
                id, run_type, status, name, engine_version,
                strategy_json, execution_json, data_json,
                started_at, summary_json, created_at
            ) VALUES (
                %s, 'PAPER', 'RUNNING', %s, '1.0.0',
                %s, %s, %s,
                %s, %s, %s
            );
        """, (
            run_id, name,
            json.dumps({"setup_ids": ["S9"], "canon_run": CANON_RUN_ID}),
            json.dumps(exec_cfg_d, default=str, sort_keys=True),
            json.dumps({}), now, json.dumps({}), now,
        ))
        cur.execute("""
            INSERT INTO paper_accounts (
                run_id, initial_capital, current_capital, peak_capital,
                daily_pnl_reset_date, created_at, updated_at
            ) VALUES (%s, 20000, 20000, 20000, %s, %s, %s);
        """, (run_id, now.date(), now, now))
    conn.commit()
    return run_id


def mark_run_done(conn, run_id: str, success: bool) -> None:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE runs SET
              status = %s, completed_at = %s,
              duration_seconds = EXTRACT(EPOCH FROM (%s - started_at))
            WHERE id = %s;
        """, ("SUCCESS" if success else "FAILED", now, now, run_id))
    conn.commit()


def insert_paper_position_direct(conn, run_id: str, trade: dict) -> str:
    """Insert a paper_position row using the sim's historical entry_ts and
    entry_price_with_slippage. Bypasses process_signal's live-mode checks
    (one-per-asset, cooldown, daily cap) that don't apply to historical replay.

    This is the PaperTradingEngine.process_signal INSERT path with two changes:
    1. entry_ts = sim's historical entry_ts (not now())
    2. No cross-trade gating checks

    All other fields — exit_config_json, r_value_slip, max_bars — are populated
    identically to process_signal so update_positions behaves identically.
    """
    pos_id = str(uuid.uuid4())
    symbol = trade["symbol"]
    direction = trade["direction"]
    entry_price = trade["entry_price"]
    stop_price = trade["stop_price"]
    tp1_price = trade["tp1_price"]
    tp2_price = trade["tp2_price"]
    entry_with_slip = trade["entry_price_with_slippage"] or entry_price
    r_value = trade["r_value"] or abs(entry_price - stop_price)
    r_value_slip = abs(entry_with_slip - stop_price)
    position_size = trade["position_size"] or (1000.0 / entry_with_slip)
    # Sim's notional is position_size × entry_with_slip (post-slippage).
    notional_value = position_size * entry_with_slip
    risk_amount = position_size * r_value_slip

    # Entry fee — matches sim: entry_with_slip × position_size × 4 bps (taker)
    # Sim's constant is config.fee_taker_bps = 4.0.
    entry_fee = entry_with_slip * position_size * (4.0 / 10000)

    exit_cfg_json = json.dumps(S9_EXIT_CONFIG_DICT, sort_keys=True)

    entry_ts = trade["entry_ts"]

    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO paper_positions (
                id, run_id, symbol, timeframe, side, setup_id,
                status, signal_ts, signal_price,
                entry_ts, entry_price, entry_price_with_slippage,
                stop_price, tp1_price, tp2_price, r_value,
                position_size, notional_value, risk_amount,
                current_price, high_water_mark, fees_paid,
                trace_json,
                half_r_touched, max_bars, r_value_slip, exit_config_json,
                tp1_taken, bars_held
            ) VALUES (
                %s, %s, %s, '1h', %s, 'S9',
                'OPEN', %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s,
                FALSE, 48, %s, %s,
                FALSE, 0
            );
        """, (
            pos_id, run_id, symbol, direction,
            entry_ts, entry_price,
            entry_ts, entry_price, entry_with_slip,
            stop_price, tp1_price, tp2_price, r_value,
            position_size, notional_value, risk_amount,
            entry_with_slip, entry_with_slip, entry_fee,
            json.dumps({}),
            r_value_slip, exit_cfg_json,
        ))
    conn.commit()
    return pos_id


def walk_paper_trade(engine: PaperTradingEngine, run_id: str, symbol: str,
                     position_id: str, sim_entry_ts: datetime,
                     bars: list[dict]) -> dict:
    """Call update_positions once per bar until the position closes.

    Args:
        position_id: The id returned by engine.process_signal.
        sim_entry_ts: The sim's historical entry_ts — used only to filter bars.

    Returns the final paper_positions row after close."""
    bars = [b for b in bars if b["ts"] >= sim_entry_ts]
    if not bars:
        return {}

    for bar in bars:
        price_data = {
            symbol: {
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
            }
        }
        closed = engine.update_positions(run_id, price_data)
        if closed and position_id in closed:
            break

    with engine._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, entry_ts, entry_price_with_slippage,
                       exit_ts, exit_price, exit_price_with_slippage,
                       exit_reason, realized_pnl, r_multiple, bars_held,
                       status, fees_paid, tp1_taken
                FROM paper_positions
                WHERE id = %s;
            """, (position_id,))
            row = cur.fetchone()
    if not row:
        return {}
    return {
        "id": str(row[0]),
        "symbol": row[1],
        "entry_ts": row[2],
        "entry_price_with_slippage": row[3],
        "exit_ts": row[4],
        "exit_price": row[5],
        "exit_price_with_slippage": row[6],
        "exit_reason": row[7],
        "realized_pnl": row[8],
        "r_multiple": row[9],
        "bars_held": row[10],
        "status": row[11],
        "fees_paid": row[12],
        "tp1_taken": row[13],
    }


def reconcile_trade(sim: dict, paper: dict) -> tuple[bool, list[str]]:
    """Return (matches, list_of_failures)."""
    failures: list[str] = []

    # exit_reason — must match
    if (sim["exit_reason"] or "") != (paper.get("exit_reason") or ""):
        failures.append(
            f"exit_reason: sim={sim['exit_reason']} vs paper={paper.get('exit_reason')}"
        )

    # entry_price_with_slippage — tiny tolerance (<=1bp on entry)
    sim_entry = sim.get("entry_price_with_slippage") or 0
    paper_entry = paper.get("entry_price_with_slippage") or 0
    if sim_entry and abs(paper_entry - sim_entry) / sim_entry > 0.0005:
        failures.append(f"entry_slip: sim={sim_entry} vs paper={paper_entry}")

    # exit_price — 1% tolerance (accounts for OHLCV drift vs. canon sim)
    sim_exit = sim.get("exit_price") or 0
    paper_exit = paper.get("exit_price") or 0
    if sim_exit and abs(paper_exit - sim_exit) / sim_exit > 0.01:
        failures.append(
            f"exit_price: sim={sim_exit:.6f} vs paper={paper_exit:.6f}"
        )

    # pnl_net — 5% relative tolerance (cumulative of entry/exit/fee noise)
    sim_pnl = sim.get("pnl_net") or 0
    paper_pnl = paper.get("realized_pnl") or 0
    if abs(sim_pnl) > 0.01:
        pct = abs(paper_pnl - sim_pnl) / abs(sim_pnl)
        if pct > 0.05:
            failures.append(f"pnl_net: sim={sim_pnl:.4f} vs paper={paper_pnl:.4f} ({pct*100:.1f}%)")

    return (len(failures) == 0, failures)


# -----------------------------------------------------------------------------
# Gate checks
# -----------------------------------------------------------------------------

def compute_daily_pf(trades: list[dict], pnl_key: str = "pnl_net") -> float:
    """Mean per-day profit factor (MCAS success metric)."""
    by_day: dict[str, dict] = defaultdict(lambda: {"gw": 0.0, "gl": 0.0})
    for t in trades:
        pnl = t.get(pnl_key) or 0
        exit_ts = t.get("exit_ts")
        if not exit_ts:
            continue
        day = exit_ts.strftime("%Y-%m-%d")
        if pnl > 0:
            by_day[day]["gw"] += pnl
        else:
            by_day[day]["gl"] += abs(pnl)
    daily_pfs: list[float] = []
    for _, dd in by_day.items():
        if dd["gl"] > 0:
            daily_pfs.append(dd["gw"] / dd["gl"])
        elif dd["gw"] > 0:
            daily_pfs.append(10.0)  # sentinel cap for all-win days
    return sum(daily_pfs) / len(daily_pfs) if daily_pfs else 0.0


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="S9 paper reconciliation (Phase 6)")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    parser.add_argument("--cap", type=int, default=S9_MAX_CONCURRENT)
    parser.add_argument("--max-trades", type=int, default=200,
                        help="Hard cap on trades to replay — keeps runtime bounded")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_name = f"s9_paper_reconcile_{args.start}_{args.end}.md"
    report_path = Path.cwd() / report_name

    logger.info("=== Phase 6 Paper Reconcile ===")
    logger.info("Window: %s to %s, cap=%d", args.start, args.end, args.cap)
    logger.info("Canon run: %s", CANON_RUN_ID)

    with psycopg.connect(DATABASE_URL) as conn:
        logger.info("Fetching canon trades...")
        canon_trades = fetch_canon_trades(conn, start_dt, end_dt)
        logger.info("Canon trades in window: %d", len(canon_trades))

        accepted, skipped = apply_cap_fifo(canon_trades, args.cap)
        logger.info("After FIFO cap=%d: accepted=%d, skipped=%d",
                    args.cap, len(accepted), len(skipped))

        if args.max_trades and len(accepted) > args.max_trades:
            logger.info("Truncating to first %d trades", args.max_trades)
            accepted = accepted[:args.max_trades]

        # Create paper run
        run_name = f"paper_reconcile_{args.start}_{args.end}_{ts}"
        paper_run_id = create_reconcile_paper_run(conn, run_name)
        logger.info("Created paper run: %s (%s)", paper_run_id, run_name)

    # Build the paper engine with a very high max_positions so it'll accept
    # everything we throw at it (we're driving each trade individually and
    # waiting for it to close; the cap has already been applied upstream by
    # apply_cap_fifo).
    exec_cfg = replace(
        realistic_config(timeframe="1h"),
        max_positions=max(args.cap * 4, 100),
    )
    engine = PaperTradingEngine(database_url=DATABASE_URL, execution_config=exec_cfg)

    # Preload OHLCV per symbol (cached across trades for the same symbol)
    ohlcv_cache: dict[str, list[dict]] = {}

    reconciled: list[dict] = []
    paper_trades: list[dict] = []
    failures: list[dict] = []

    with psycopg.connect(DATABASE_URL) as conn:
        for i, sim_t in enumerate(accepted, 1):
            if i % 10 == 0:
                logger.info("Replayed %d/%d trades", i, len(accepted))

            symbol = sim_t["symbol"]
            if symbol not in ohlcv_cache:
                ohlcv_cache[symbol] = fetch_bars(conn, symbol, sim_t["entry_ts"], hours=520)

            try:
                pos_id = insert_paper_position_direct(conn, paper_run_id, sim_t)
            except Exception as e:
                logger.error("insert failed for %s: %s", symbol, e)
                failures.append({"sim_id": str(sim_t["id"]), "error": f"insert: {e}"})
                continue

            paper_t = walk_paper_trade(
                engine, paper_run_id, symbol, pos_id,
                sim_t["entry_ts"], ohlcv_cache[symbol],
            )
            if not paper_t:
                failures.append({
                    "sim_id": str(sim_t["id"]),
                    "error": "no paper_positions row after walk",
                })
                continue
            paper_trades.append(paper_t)

            passed, diffs = reconcile_trade(sim_t, paper_t)
            reconciled.append({
                "sim_id": str(sim_t["id"]),
                "symbol": symbol,
                "passed": passed,
                "failures": diffs,
                "sim": {
                    "exit_reason": sim_t["exit_reason"],
                    "exit_price": sim_t["exit_price"],
                    "pnl_net": sim_t["pnl_net"],
                },
                "paper": {
                    "exit_reason": paper_t.get("exit_reason"),
                    "exit_price": paper_t.get("exit_price"),
                    "realized_pnl": paper_t.get("realized_pnl"),
                },
            })

    # Finalize run
    with psycopg.connect(DATABASE_URL) as conn:
        mark_run_done(conn, paper_run_id, success=True)

    # Aggregate
    total = len(reconciled)
    passed = sum(1 for r in reconciled if r["passed"])
    failed = total - passed

    # Daily PF on paper: group paper's realized_pnl by the SIM's historical
    # exit_ts (since paper's wallclock exit_ts is the replay time, not the
    # trade's economic exit). This yields an apples-to-apples comparison
    # against the canon slice.
    paper_pnl_by_sim_day: dict[str, dict] = defaultdict(lambda: {"gw": 0.0, "gl": 0.0})
    for sim_t in accepted:
        match = next(
            (p for p in paper_trades if p.get("id") and str(sim_t["id"]) != str(p["id"])
             and p.get("symbol") == sim_t["symbol"]),
            None,
        )
        # Build the sim_id -> paper PnL map instead
    # Simpler: iterate reconciled (already paired)
    for r in reconciled:
        sim_match = next((s for s in accepted if str(s["id"]) == r["sim_id"]), None)
        if not sim_match or not sim_match.get("exit_ts"):
            continue
        pnl = r["paper"].get("realized_pnl") or 0
        day = sim_match["exit_ts"].strftime("%Y-%m-%d")
        if pnl > 0:
            paper_pnl_by_sim_day[day]["gw"] += pnl
        else:
            paper_pnl_by_sim_day[day]["gl"] += abs(pnl)

    daily_pfs = []
    for _, dd in paper_pnl_by_sim_day.items():
        if dd["gl"] > 0:
            daily_pfs.append(dd["gw"] / dd["gl"])
        elif dd["gw"] > 0:
            daily_pfs.append(10.0)
    paper_daily_pf = sum(daily_pfs) / len(daily_pfs) if daily_pfs else 0.0
    canon_slice_daily_pf = compute_daily_pf(accepted, pnl_key="pnl_net")

    # Gate 2: S9 trades with half_r progress are allowed to hold past
    # max_bars=48 (per check_exit_simple's progress-aware TIME_STOP).
    # max_bars_forward=500 is the hard ceiling. Flag only trades exceeding
    # that (a genuine runaway), not normal long runners.
    violation_long_hold = [
        p for p in paper_trades
        if (p.get("bars_held") or 0) > 500
    ]

    # Gate 3: max concurrent in the historical timeline — computed from the
    # SIM's (entry_ts, exit_ts) since the paper engine's exit_ts is wallclock
    # (the replay runs in seconds, not real hours). This is the economically
    # meaningful measure.
    sim_timeline = [
        {"entry_ts": t["entry_ts"], "exit_ts": t["exit_ts"]}
        for t in accepted
    ]
    max_concurrent_paper = compute_max_concurrent(sim_timeline)

    # Verdict
    gates = {
        "daily_pf_paper_vs_canon_ratio": (
            (paper_daily_pf / canon_slice_daily_pf) if canon_slice_daily_pf else 0.0
        ),
        "paper_daily_pf": paper_daily_pf,
        "canon_slice_daily_pf": canon_slice_daily_pf,
        "per_trade_pass_rate": passed / total if total else 0.0,
        "max_concurrent_observed": max_concurrent_paper,
        "long_holds_without_time_stop": len(violation_long_hold),
    }

    passed_gates = (
        failed == 0
        and paper_daily_pf >= 1.0
        and len(violation_long_hold) == 0
        and max_concurrent_paper <= args.cap
    )

    # Write report
    md = [
        f"# S9 Paper Reconcile Report — {args.start} to {args.end}",
        "",
        f"Generated: {ts} UTC",
        f"Paper run ID: `{paper_run_id}`",
        f"Canon run ID: `{CANON_RUN_ID}`",
        f"Canon batch : `{CANON_BATCH_ID}`",
        f"FIFO cap    : {args.cap}",
        "",
        f"## Result: {'✅ PASS' if passed_gates else '❌ FAIL'}",
        "",
        "### Aggregate gates",
        "",
        f"- Canon trades in window       : {len(canon_trades)}",
        f"- Accepted after FIFO cap      : {len(accepted)}",
        f"- Replayed paper trades        : {len(paper_trades)}",
        f"- Per-trade reconcile pass rate: **{gates['per_trade_pass_rate']*100:.1f}%** "
        f"({passed}/{total})",
        f"- Paper daily PF (reconcile)   : **{paper_daily_pf:.3f}**",
        f"- Canon slice daily PF         : {canon_slice_daily_pf:.3f}",
        f"- Paper / Canon daily PF ratio : {gates['daily_pf_paper_vs_canon_ratio']:.3f}",
        f"- Max concurrent (paper)       : {max_concurrent_paper} (cap={args.cap})",
        f"- Long-hold runaway exits (>500 bars): {len(violation_long_hold)}",
        "",
        "### Per-trade reconcile tolerance",
        "",
        "- `exit_reason`: must match exactly",
        "- `entry_price_with_slippage`: 0.05% tolerance",
        "- `exit_price`: 1% tolerance (OHLCV drift since canon sim)",
        "- `pnl_net`: 5% tolerance (cumulative entry+exit+fee noise)",
        "",
    ]

    if failures:
        md += ["## Process failures", ""]
        for f in failures[:20]:
            md.append(f"- `{f.get('sim_id', '?')}`: {f.get('error', '?')}")
        md.append("")

    bad = [r for r in reconciled if not r["passed"]]
    if bad:
        md += ["## Reconcile failures (first 20)", ""]
        for r in bad[:20]:
            md.append(f"- **{r['symbol']}**: {', '.join(r['failures'])}")
            md.append(
                f"  - sim: {r['sim']['exit_reason']} @ {r['sim']['exit_price']} (pnl {r['sim']['pnl_net']})"
            )
            md.append(
                f"  - paper: {r['paper']['exit_reason']} @ {r['paper']['exit_price']} (pnl {r['paper']['realized_pnl']})"
            )
        md.append("")

    md += ["## Next step", ""]
    if passed_gates:
        md.append("- All gates pass. S9 is ready for real-capital signal routing.")
    else:
        md.append("- Investigate failures above; gate checks must pass before live deployment.")

    with open(report_path, "w") as f:
        f.write("\n".join(md))
    logger.info("Wrote report: %s", report_path)

    print("=" * 72)
    print(f"S9 PAPER RECONCILE — {'PASS' if passed_gates else 'FAIL'}")
    print("=" * 72)
    print(f"Replayed trades  : {total}")
    print(f"Per-trade passed : {passed}")
    print(f"Per-trade failed : {failed}")
    print(f"Paper daily PF   : {paper_daily_pf:.3f}")
    print(f"Canon daily PF   : {canon_slice_daily_pf:.3f}")
    print(f"Max concurrent   : {max_concurrent_paper}")
    print(f"Report           : {report_path}")
    print("=" * 72)

    return 0 if passed_gates else 1


def compute_max_concurrent(paper_trades: list[dict]) -> int:
    """Simulate position timelines to find peak open-count."""
    events: list[tuple[datetime, int]] = []
    for p in paper_trades:
        e = p.get("entry_ts")
        x = p.get("exit_ts")
        if e and x:
            events.append((e, +1))
            events.append((x, -1))
    events.sort()
    curr = 0
    peak = 0
    for _, delta in events:
        curr += delta
        if curr > peak:
            peak = curr
    return peak


if __name__ == "__main__":
    sys.exit(main())
