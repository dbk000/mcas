"""
Paper trading telemetry — Phase A (immediately available fields)
================================================================

Builds the sections of ``paper_positions.telemetry_json`` that can be
populated from data already on hand: the signal payload, the OHLCV table,
and a handful of cheap queries against ``paper_positions`` itself.

Schema v1 sections
------------------
- ``signal_context``       — S9 rank/score/breadth/FnG + same-bar crowding +
  concurrent-at-entry + FIFO position
- ``regime_context``       — BTC price/return, liquidation cascade heuristic,
  day/hour/session, days since last S9 loss, rolling PF
- ``entry_snapshot``       — signal price vs fill, slippage in bps, ms-precision
  submit/fill timestamps, latency
- ``lifetime``             — MAE/MFE (price/bps/USD/bar_idx/ts), HWM,
  time-in-profit vs drawdown (bar counts), breakeven-then-reverse flag,
  bars_held, seconds_held, funding intervals crossed
- ``exit_snapshot``        — fill, slippage, exit_reason, gap-through detection
- ``microstructure_entry``/``microstructure_exit`` — placeholders (follow-up)
- ``post_exit``            — placeholder (48h background job)
- ``operational``          — placeholder (follow-up)

All functions here are pure (no side effects) except the compute_* helpers
that take a DB ``conn`` for the few cheap lookups that need it. Callers pass
``None`` for conn if they want to skip the DB-dependent subsections — the
helpers gracefully degrade to partial telemetry rather than crash.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

TELEMETRY_SCHEMA_VERSION = "1"


# =============================================================================
# Signal context
# =============================================================================

def compute_signal_context(
    signal: dict,
    conn,
    run_id: str,
) -> dict:
    """Extract S9 signal context from the payload + cheap DB counts.

    Gracefully degrades on missing fields. Never raises.
    """
    payload = signal.get("payload") or {}
    state = payload.get("state") or {}
    score_components = payload.get("score_components") or {}

    # Signal time (the bar the signal fired on). For S9 we use payload.bar_start_ts
    # which is a ms epoch string set by the signal worker.
    signal_bar_ts = payload.get("bar_start_ts") or signal.get("bar_start_ts")
    if isinstance(signal_bar_ts, str) and signal_bar_ts.isdigit():
        signal_bar_dt: Optional[datetime] = datetime.fromtimestamp(
            int(signal_bar_ts) / 1000, tz=timezone.utc,
        )
    elif isinstance(signal_bar_ts, datetime):
        signal_bar_dt = signal_bar_ts
        if signal_bar_dt.tzinfo is None:
            signal_bar_dt = signal_bar_dt.replace(tzinfo=timezone.utc)
    else:
        signal_bar_dt = None

    ctx: dict[str, Any] = {
        "setup_id": signal.get("setup_id"),
        "symbol": signal.get("symbol"),
        "timeframe": signal.get("timeframe"),
        "bar_start_ts": signal_bar_dt.isoformat() if signal_bar_dt else None,
        "rank": state.get("rank"),
        "decile_size": state.get("decile_size"),
        "universe_size": state.get("universe_size"),
        "score_total": payload.get("setup_score"),
        "score_raw": payload.get("raw_score"),
        "score_magnitude": score_components.get("magnitude_score"),
        "score_breadth": score_components.get("breadth_score"),
        "score_rank": score_components.get("rank_score"),
        "ret_4h": state.get("ret_4h"),
        "ret_24h": state.get("ret_24h"),
        "breadth": state.get("breadth"),
        "fng_value": payload.get("fng_value"),
        "fng_classification": payload.get("fng_classification"),
    }

    # Cheap DB-derived fields. Skip if no conn available.
    if conn is not None:
        try:
            with conn.cursor() as cur:
                # Same-bar crowding: how many S9 opportunities fired on this bar?
                if signal_bar_dt is not None:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM opportunities
                        WHERE setup_id = 'S9' AND created_ts = %s;
                        """,
                        (signal_bar_dt,),
                    )
                    row = cur.fetchone()
                    ctx["signals_same_bar"] = int(row[0]) if row else None
                else:
                    ctx["signals_same_bar"] = None

                # Concurrent-at-entry: open S9 positions in this run right now.
                cur.execute(
                    """
                    SELECT COUNT(*) FROM paper_positions
                    WHERE run_id = %s AND status = 'OPEN';
                    """,
                    (run_id,),
                )
                row = cur.fetchone()
                ctx["concurrent_at_entry"] = int(row[0]) if row else 0

                # FIFO position: how many S9 positions have already been
                # accepted on this signal bar (for this run)?
                if signal_bar_dt is not None:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM paper_positions
                        WHERE run_id = %s AND setup_id = 'S9'
                          AND signal_ts = %s;
                        """,
                        (run_id, signal_bar_dt),
                    )
                    row = cur.fetchone()
                    # 0-indexed fifo: 0 means first accepted on this bar
                    ctx["fifo_on_bar"] = int(row[0]) if row else 0
                else:
                    ctx["fifo_on_bar"] = None
        except Exception as e:
            logger.warning("signal_context DB lookups failed: %s", e)
            ctx.setdefault("signals_same_bar", None)
            ctx.setdefault("concurrent_at_entry", None)
            ctx.setdefault("fifo_on_bar", None)
    else:
        ctx["signals_same_bar"] = None
        ctx["concurrent_at_entry"] = None
        ctx["fifo_on_bar"] = None

    return ctx


# =============================================================================
# Regime context
# =============================================================================

def _session_from_hour(hour_utc: int) -> str:
    """Crude crypto-market session bucketing. Hours are in UTC.

    - 00-07 : ASIA (Asia Pacific)
    - 07-13 : EU  (Europe)
    - 13-20 : US  (North America)
    - 20-24 : ASIA rollover
    """
    if 7 <= hour_utc < 13:
        return "EU"
    if 13 <= hour_utc < 20:
        return "US"
    return "ASIA"


def compute_regime_context(
    entry_ts: datetime,
    conn,
    run_id: str,
    setup_id: str = "S9",
) -> dict:
    """Market regime snapshot at entry. Degrades gracefully on DB errors."""
    if entry_ts.tzinfo is None:
        entry_ts = entry_ts.replace(tzinfo=timezone.utc)

    ctx: dict[str, Any] = {
        "entry_ts": entry_ts.isoformat(),
        "dow_utc": entry_ts.weekday(),           # 0=Mon .. 6=Sun
        "hour_utc": entry_ts.hour,
        "session": _session_from_hour(entry_ts.hour),
        "btc_price_at_entry": None,
        "btc_return_1h": None,
        "btc_return_24h": None,
        "liquidation_cascade_active": None,
        "days_since_last_loss": None,
        "rolling_pf_10": None,
        "rolling_win_rate_10": None,
    }

    if conn is None:
        return ctx

    try:
        with conn.cursor() as cur:
            # BTC 1h bar at entry_ts (used for price + 1h return)
            cur.execute(
                """
                SELECT DISTINCT ON (bar_start_ts)
                       bar_start_ts, open, close
                FROM ohlcv
                WHERE symbol = 'BTCUSDT' AND timeframe = '1h'
                  AND bar_start_ts <= %s
                  AND is_final = TRUE
                ORDER BY bar_start_ts DESC,
                         CASE source WHEN 'ws' THEN 0
                                     WHEN 'binance' THEN 1
                                     ELSE 2 END
                LIMIT 1;
                """,
                (entry_ts,),
            )
            row = cur.fetchone()
            if row:
                btc_close = float(row[2])
                btc_open = float(row[1])
                ctx["btc_price_at_entry"] = btc_close
                if btc_open > 0:
                    ctx["btc_return_1h"] = (btc_close - btc_open) / btc_open

            # BTC 24h return: compare against bar 24h before entry
            cur.execute(
                """
                SELECT DISTINCT ON (bar_start_ts) close
                FROM ohlcv
                WHERE symbol = 'BTCUSDT' AND timeframe = '1h'
                  AND bar_start_ts <= %s - INTERVAL '24 hours'
                  AND is_final = TRUE
                ORDER BY bar_start_ts DESC,
                         CASE source WHEN 'ws' THEN 0
                                     WHEN 'binance' THEN 1
                                     ELSE 2 END
                LIMIT 1;
                """,
                (entry_ts,),
            )
            row_24 = cur.fetchone()
            if row_24 and ctx["btc_price_at_entry"]:
                close_24h_ago = float(row_24[0])
                if close_24h_ago > 0:
                    ctx["btc_return_24h"] = (
                        ctx["btc_price_at_entry"] - close_24h_ago
                    ) / close_24h_ago

            # Liquidation cascade heuristic: BTC 1h return < -3% at entry bar
            if ctx["btc_return_1h"] is not None:
                ctx["liquidation_cascade_active"] = ctx["btc_return_1h"] < -0.03

            # Days since last S9 loss (this run)
            cur.execute(
                """
                SELECT exit_ts FROM paper_positions
                WHERE run_id = %s AND setup_id = %s
                  AND status = 'CLOSED' AND realized_pnl < 0
                ORDER BY exit_ts DESC LIMIT 1;
                """,
                (run_id, setup_id),
            )
            row_loss = cur.fetchone()
            if row_loss and row_loss[0]:
                last_loss = row_loss[0]
                if last_loss.tzinfo is None:
                    last_loss = last_loss.replace(tzinfo=timezone.utc)
                delta = entry_ts - last_loss
                ctx["days_since_last_loss"] = round(
                    delta.total_seconds() / 86400, 3,
                )

            # Rolling 10-trade PF at entry (closed trades in this run)
            cur.execute(
                """
                SELECT realized_pnl FROM paper_positions
                WHERE run_id = %s AND setup_id = %s
                  AND status = 'CLOSED'
                ORDER BY exit_ts DESC LIMIT 10;
                """,
                (run_id, setup_id),
            )
            rows = cur.fetchall()
            if rows:
                pnls = [float(r[0] or 0) for r in rows]
                wins = sum(p for p in pnls if p > 0)
                losses = abs(sum(p for p in pnls if p < 0))
                if losses > 0:
                    ctx["rolling_pf_10"] = round(wins / losses, 4)
                elif wins > 0:
                    ctx["rolling_pf_10"] = 999.0  # sentinel for all-win
                ctx["rolling_win_rate_10"] = (
                    sum(1 for p in pnls if p > 0) / len(pnls)
                )
    except Exception as e:
        logger.warning("regime_context DB lookups failed: %s", e)

    return ctx


# =============================================================================
# Entry / exit snapshots
# =============================================================================

def compute_entry_snapshot(
    signal: dict,
    fill_price: float,
    fill_price_with_slippage: float,
    submit_ts_ms: int,
    fill_ts_ms: int,
) -> dict:
    """Entry fill + slippage snapshot. Paper trading models submit/fill as
    back-to-back (no real broker), so latency is ~0. The fields are still
    recorded for schema consistency when the same blob travels into live.
    """
    signal_price = signal.get("entry_price")
    slippage_bps: Optional[float] = None
    if signal_price and signal_price > 0:
        diff = fill_price_with_slippage - signal_price
        slippage_bps = (diff / signal_price) * 10_000

    return {
        "signal_price": signal_price,
        "fill_price": fill_price,
        "fill_price_with_slippage": fill_price_with_slippage,
        "slippage_bps": round(slippage_bps, 4) if slippage_bps is not None else None,
        "submit_ts_ms": submit_ts_ms,
        "fill_ts_ms": fill_ts_ms,
        "execution_latency_ms": fill_ts_ms - submit_ts_ms,
        "partial_fill_count": 1,  # paper trading = single atomic fill
    }


def compute_exit_snapshot(
    exit_price: float,
    exit_price_with_slippage: float,
    exit_reason: str,
    stop_price: Optional[float],
    tp1_price: Optional[float],
    tp2_price: Optional[float],
    exit_ts: datetime,
    side: str,
    bar_open: Optional[float] = None,
) -> dict:
    """Exit fill + gap-through detection.

    For STOP_LOSS, a gap-through happens when the bar opened past the stop
    level (for shorts: bar_open > stop_price, for longs: bar_open < stop).
    The fill price in that case is bar_open (not stop_price) — identical to
    opportunity_simulator's logic.
    """
    is_gap_through = False
    gap_magnitude_bps: Optional[float] = None

    if exit_reason == "STOP_LOSS" and stop_price is not None:
        is_gap_through = abs(exit_price - stop_price) > 1e-12
        if is_gap_through and stop_price > 0:
            gap_magnitude_bps = abs(exit_price - stop_price) / stop_price * 10_000
    elif exit_reason in ("TP1", "TP2"):
        ref_price = tp1_price if exit_reason == "TP1" else tp2_price
        if ref_price is not None:
            # Windfall gap = bar opened past the TP level (favourable)
            if side == "long":
                is_gap_through = exit_price > ref_price
            else:
                is_gap_through = exit_price < ref_price
            if is_gap_through and ref_price > 0:
                gap_magnitude_bps = abs(exit_price - ref_price) / ref_price * 10_000

    return {
        "exit_price": exit_price,
        "exit_price_with_slippage": exit_price_with_slippage,
        "exit_reason": exit_reason,
        "exit_ts": exit_ts.isoformat() if isinstance(exit_ts, datetime) else exit_ts,
        "is_gap_through": is_gap_through,
        "gap_magnitude_bps": round(gap_magnitude_bps, 4) if gap_magnitude_bps is not None else None,
        "bar_open_at_exit": bar_open,
    }


# =============================================================================
# Lifetime tracking (MAE/MFE/HWM/time-in-profit)
# =============================================================================

def initialize_lifetime(
    side: str,
    entry_price_with_slip: float,
    entry_ts: datetime,
    position_size: float,
    r_value_slip: float,
) -> dict:
    """Initialize the lifetime section at position open.

    MAE/MFE start at the entry price (zero excursion). They get updated on
    each bar via ``update_mae_mfe_in_telemetry``.
    """
    if entry_ts.tzinfo is None:
        entry_ts = entry_ts.replace(tzinfo=timezone.utc)

    return {
        "side": side,
        "entry_price_with_slip": entry_price_with_slip,
        "entry_ts": entry_ts.isoformat(),
        "position_size": position_size,
        "r_value_slip": r_value_slip,
        # MAE = Maximum Adverse Excursion
        "mae_price": entry_price_with_slip,
        "mae_bps": 0.0,
        "mae_usd": 0.0,
        "mae_bar_idx": 0,
        "mae_ts": entry_ts.isoformat(),
        # MFE = Maximum Favourable Excursion
        "mfe_price": entry_price_with_slip,
        "mfe_bps": 0.0,
        "mfe_usd": 0.0,
        "mfe_bar_idx": 0,
        "mfe_ts": entry_ts.isoformat(),
        # HWM tracks the best price achieved (low for shorts, high for longs)
        "hwm_price": entry_price_with_slip,
        "hwm_ts": entry_ts.isoformat(),
        # Breakeven-then-reverse detection (the "almost winner" pattern)
        "touched_breakeven": False,
        "touched_breakeven_then_stopped": False,
        "half_r_touched": False,
        # Bar-level time-in-profit/drawdown counts (set on each update)
        "bars_in_profit": 0,
        "bars_in_drawdown": 0,
        "bars_walked": 0,
        # Finalized at close
        "bars_held": None,
        "seconds_held": None,
        "funding_intervals_crossed": None,
    }


def update_mae_mfe_in_telemetry(
    telemetry: dict,
    bar: dict,
    bar_ts: datetime,
    bar_idx: int,
) -> dict:
    """Update the lifetime section with a fresh bar. Returns a NEW dict
    (does not mutate the input).

    Tracks:
      - MAE: worst intrabar excursion against the position
      - MFE: best intrabar excursion for the position
      - HWM: price of the best moment
      - touched_breakeven: True once price revisits entry after moving in
        favour (only for positions that first moved favourable by >= 0 bps
        at some point — we proxy this by checking "has MFE drifted past
        entry?")
      - bars_in_profit / bars_in_drawdown: classified by the BAR's CLOSE
        relative to entry
    """
    if telemetry is None:
        return telemetry
    lifetime = (telemetry.get("lifetime") or {}).copy()
    if not lifetime:
        return telemetry

    side = lifetime.get("side")
    entry = lifetime.get("entry_price_with_slip")
    size = lifetime.get("position_size") or 0.0
    if entry is None or entry <= 0 or side is None:
        return telemetry

    bar_high = float(bar.get("high", 0) or 0)
    bar_low = float(bar.get("low", 0) or 0)
    bar_close = float(bar.get("close", 0) or 0)
    if bar_high <= 0 or bar_low <= 0:
        return telemetry

    if bar_ts.tzinfo is None:
        bar_ts = bar_ts.replace(tzinfo=timezone.utc)

    # Classify the CLOSE for time-in-profit bucketing
    if side == "long":
        in_profit = bar_close > entry
        adverse_price = bar_low
        favourable_price = bar_high
    else:  # short
        in_profit = bar_close < entry
        adverse_price = bar_high
        favourable_price = bar_low

    if in_profit:
        lifetime["bars_in_profit"] = int(lifetime.get("bars_in_profit", 0) or 0) + 1
    else:
        lifetime["bars_in_drawdown"] = int(lifetime.get("bars_in_drawdown", 0) or 0) + 1
    lifetime["bars_walked"] = int(lifetime.get("bars_walked", 0) or 0) + 1

    # MAE update: compare adverse_price to current mae_price
    current_mae = float(lifetime.get("mae_price") or entry)
    is_new_mae = (adverse_price > current_mae) if side == "short" else (adverse_price < current_mae)
    if is_new_mae:
        lifetime["mae_price"] = adverse_price
        if side == "long":
            mae_bps = (adverse_price - entry) / entry * 10_000
            mae_usd = (adverse_price - entry) * size
        else:
            mae_bps = (entry - adverse_price) / entry * 10_000
            mae_usd = (entry - adverse_price) * size
        lifetime["mae_bps"] = round(mae_bps, 4)
        lifetime["mae_usd"] = round(mae_usd, 6)
        lifetime["mae_bar_idx"] = bar_idx
        lifetime["mae_ts"] = bar_ts.isoformat()

    # MFE update: compare favourable_price to current mfe_price
    current_mfe = float(lifetime.get("mfe_price") or entry)
    is_new_mfe = (favourable_price < current_mfe) if side == "short" else (favourable_price > current_mfe)
    if is_new_mfe:
        lifetime["mfe_price"] = favourable_price
        if side == "long":
            mfe_bps = (favourable_price - entry) / entry * 10_000
            mfe_usd = (favourable_price - entry) * size
        else:
            mfe_bps = (entry - favourable_price) / entry * 10_000
            mfe_usd = (entry - favourable_price) * size
        lifetime["mfe_bps"] = round(mfe_bps, 4)
        lifetime["mfe_usd"] = round(mfe_usd, 6)
        lifetime["mfe_bar_idx"] = bar_idx
        lifetime["mfe_ts"] = bar_ts.isoformat()
        lifetime["hwm_price"] = favourable_price
        lifetime["hwm_ts"] = bar_ts.isoformat()

    # Touched-breakeven-after-profit detection (the "almost winner").
    # Rule: once MFE is at least 25 bps in the favourable direction, check
    # if a later bar brings price BACK to entry. If so, set touched_breakeven.
    mfe_bps_now = float(lifetime.get("mfe_bps") or 0)
    if mfe_bps_now >= 25 and not lifetime.get("touched_breakeven"):
        if side == "long":
            revisited = bar_low <= entry
        else:
            revisited = bar_high >= entry
        if revisited:
            lifetime["touched_breakeven"] = True

    new_tele = telemetry.copy()
    new_tele["lifetime"] = lifetime
    return new_tele


def finalize_lifetime(
    telemetry: dict,
    exit_ts: datetime,
    bars_held: int,
    exit_reason: Optional[str] = None,
) -> dict:
    """Finalize lifetime stats at close. Returns a NEW dict."""
    if telemetry is None:
        return telemetry
    lifetime = (telemetry.get("lifetime") or {}).copy()
    if not lifetime:
        return telemetry

    if exit_ts.tzinfo is None:
        exit_ts = exit_ts.replace(tzinfo=timezone.utc)

    entry_iso = lifetime.get("entry_ts")
    if entry_iso:
        try:
            entry_ts = datetime.fromisoformat(entry_iso)
            if entry_ts.tzinfo is None:
                entry_ts = entry_ts.replace(tzinfo=timezone.utc)
            seconds_held = int((exit_ts - entry_ts).total_seconds())
            lifetime["seconds_held"] = seconds_held
            # Binance perp funding intervals fire every 8h (00:00, 08:00, 16:00 UTC)
            lifetime["funding_intervals_crossed"] = _count_funding_intervals(
                entry_ts, exit_ts,
            )
        except Exception as e:
            logger.warning("finalize_lifetime: entry_ts parse failed: %s", e)
            lifetime["seconds_held"] = None
            lifetime["funding_intervals_crossed"] = None

    lifetime["bars_held"] = bars_held
    lifetime["exit_ts"] = exit_ts.isoformat()

    # "Almost winner" finalization: touched breakeven after profit AND then
    # stopped out.
    if (
        lifetime.get("touched_breakeven")
        and exit_reason
        and "STOP_LOSS" in exit_reason
    ):
        lifetime["touched_breakeven_then_stopped"] = True

    new_tele = telemetry.copy()
    new_tele["lifetime"] = lifetime
    return new_tele


def _count_funding_intervals(entry_ts: datetime, exit_ts: datetime) -> int:
    """Count the number of Binance perp funding intervals (8h boundaries at
    00:00, 08:00, 16:00 UTC) crossed between entry and exit (exclusive of
    entry, inclusive of exit).
    """
    if entry_ts >= exit_ts:
        return 0
    # Normalize to full hours for easier math
    entry_hour = entry_ts.replace(minute=0, second=0, microsecond=0)
    exit_hour = exit_ts.replace(minute=0, second=0, microsecond=0)
    count = 0
    current = entry_hour
    # Walk hour-by-hour; cheap enough for typical S9 holds (~48 hours)
    step = (exit_hour - entry_hour).total_seconds() // 3600
    for i in range(int(step) + 1):
        h = (entry_hour.timestamp() + (i + 1) * 3600)
        h_dt = datetime.fromtimestamp(h, tz=timezone.utc)
        if h_dt > exit_ts:
            break
        if h_dt.hour % 8 == 0:
            count += 1
    return count


# =============================================================================
# Blob builders (entry insert / exit close)
# =============================================================================

def build_entry_telemetry_blob(
    signal: dict,
    run_id: str,
    conn,
    fill_price: float,
    fill_price_with_slippage: float,
    submit_ts_ms: int,
    fill_ts_ms: int,
    position_size: float,
    r_value_slip: float,
    entry_ts: datetime,
) -> dict:
    """Build the complete telemetry JSON blob at position open.

    The ``lifetime`` section is initialized for bar-by-bar tracking; the
    follow-up sections (``microstructure_entry``, ``microstructure_exit``,
    ``post_exit``, ``operational``) are stubbed as ``None`` and will be
    populated by follow-up work within 24h.
    """
    side = signal.get("direction", "short")
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "signal_context": compute_signal_context(signal, conn, run_id),
        "regime_context": compute_regime_context(
            entry_ts, conn, run_id, setup_id=signal.get("setup_id", "S9"),
        ),
        "entry_snapshot": compute_entry_snapshot(
            signal, fill_price, fill_price_with_slippage,
            submit_ts_ms, fill_ts_ms,
        ),
        "exit_snapshot": None,
        "lifetime": initialize_lifetime(
            side=side,
            entry_price_with_slip=fill_price_with_slippage,
            entry_ts=entry_ts,
            position_size=position_size,
            r_value_slip=r_value_slip,
        ),
        "microstructure_entry": None,
        "microstructure_exit": None,
        "post_exit": None,
        "operational": None,
    }
