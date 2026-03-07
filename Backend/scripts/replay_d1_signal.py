#!/usr/bin/env python3
"""
replay_d1_signal.py — Ground-truth D1 state-machine replay tool.

Zero dependencies on backfill or sim infrastructure. Loads OHLCV from Postgres,
instantiates D1Evaluator + FeatureFactory directly, walks the state machine
bar-by-bar, and prints trigger results.

Modes:
  Spot-check:  --bar-ts <epoch_seconds>  → state + trigger at one bar
  Full replay: no --bar-ts               → all triggers for the symbol

Usage:
    # Spot-check a specific bar
    python scripts/replay_d1_signal.py \\
        --symbol ETHUSDT --timeframe 1h --bar-ts 1709251200

    # Full replay with custom thresholds
    python scripts/replay_d1_signal.py \\
        --symbol TOKENUSDT --timeframe 1h \\
        --dormancy-threshold 0.60 --rvol-min 2.0

    # In Docker:
    docker compose -f docker-compose.yml run --rm -T worker \\
        python3 /scripts/replay_d1_signal.py \\
        --symbol TOKENUSDT --timeframe 1h --bar-ts 1709251200

Requires DATABASE_URL env var (reads from .env if available).
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------
env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

# ---------------------------------------------------------------------------
# Path setup — same pattern as backfill_d1_opportunities.py lines 49-55
# ---------------------------------------------------------------------------
worker_app_dir = os.path.join(os.path.dirname(__file__), '..', 'worker', 'app')
if os.path.isdir('/app') and os.path.exists('/app/indicators.py'):
    worker_app_dir = '/app'
sys.path.insert(0, os.path.abspath(worker_app_dir))
setups_dir = os.path.join(worker_app_dir, 'setups')
sys.path.insert(0, os.path.abspath(setups_dir))

import psycopg
from indicators import FeatureFactory, OHLCV
from setups.d1_dormancy_ignition import (
    D1Evaluator, D1Signal, D1State, D1Phase, D1Config, is_d1_excluded,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get('DATABASE_URL')

# Execution context
IN_DOCKER = os.path.isdir("/app")

# D1 needs FeatureFactory minimum of 201 candles
D1_MIN_BARS = 201

# Volume band for D1 universe
MIN_VOL_USD = D1Config.MIN_VOLUME_USD   # 75K
MAX_VOL_USD = D1Config.MAX_VOLUME_USD   # 3M


# =========================================================================
# Data Loading
# =========================================================================

def load_symbol_ohlcv(symbol: str, timeframe: str) -> list[OHLCV]:
    """Load all final OHLCV bars for one symbol/timeframe from Postgres."""
    logger.info(f"Loading OHLCV for {symbol} / {timeframe} ...")
    t0 = time.time()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = %s AND timeframe = %s AND is_final = TRUE
                ORDER BY bar_start_ts ASC
            """, (symbol, timeframe))
            rows = cur.fetchall()

    candles = []
    for row in rows:
        bar_start_ts, o, h, l, c, v = row
        ts_ms = int(bar_start_ts.timestamp() * 1000)
        candles.append(OHLCV(
            timestamp=ts_ms,
            open=o, high=h, low=l, close=c, volume=v,
        ))

    elapsed = time.time() - t0
    logger.info(f"  Loaded {len(candles):,} bars in {elapsed:.1f}s")
    return candles


def load_btc_ohlcv(timeframe: str) -> list[OHLCV]:
    """Load BTCUSDT OHLCV for beta computation."""
    logger.info(f"Loading BTCUSDT OHLCV for {timeframe} (beta computation) ...")

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = 'BTCUSDT' AND timeframe = %s AND is_final = TRUE
                ORDER BY bar_start_ts ASC
            """, (timeframe,))
            rows = cur.fetchall()

    candles = []
    for row in rows:
        bar_start_ts, o, h, l, c, v = row
        ts_ms = int(bar_start_ts.timestamp() * 1000)
        candles.append(OHLCV(
            timestamp=ts_ms,
            open=o, high=h, low=l, close=c, volume=v,
        ))

    logger.info(f"  Loaded {len(candles):,} BTC bars")
    return candles


def compute_rolling_24h_volume(candles: list[OHLCV], timeframe: str) -> np.ndarray:
    """Compute rolling 24h quote volume for a single series."""
    bars_24h = {"1m": 1440, "5m": 288, "15m": 96, "1h": 24, "4h": 6, "1d": 1}
    n = len(candles)
    window = bars_24h.get(timeframe, 24)

    if n < window:
        return np.zeros(n)

    volumes = np.array([c.volume for c in candles])
    closes = np.array([c.close for c in candles])
    qvol = volumes * closes

    cumsum = np.cumsum(qvol)
    rolling = np.zeros(n)
    rolling[window - 1:] = cumsum[window - 1:] - np.concatenate([[0], cumsum[:n - window]])
    for i in range(min(window - 1, n)):
        rolling[i] = cumsum[i]

    return rolling


# =========================================================================
# Replay Engine
# =========================================================================

def replay_symbol(
    symbol: str,
    timeframe: str,
    candles: list[OHLCV],
    vol_24h: np.ndarray,
    btc_candles: list[OHLCV],
    target_bar_ts_s: int | None = None,
    cooldown_hours: int = 24,
) -> list[dict]:
    """
    Walk the D1 state machine bar-by-bar for a single symbol.

    Returns list of trigger dicts. In spot-check mode (target_bar_ts_s set),
    also prints state at the target bar even if no trigger fired.
    """
    if len(candles) < D1_MIN_BARS:
        logger.error(f"Insufficient bars: {len(candles)} < {D1_MIN_BARS}")
        return []

    evaluator = D1Evaluator()
    ff = FeatureFactory()

    # Precompute BTC returns
    btc_rets = None
    if len(btc_candles) >= 2:
        btc_closes = np.array([c.close for c in btc_candles])
        btc_rets = np.diff(btc_closes) / btc_closes[:-1]

    # Build timestamp lookup for BTC alignment
    # BTC candles index by timestamp so we can align with symbol candles
    btc_ts_to_idx = {}
    for idx, bc in enumerate(btc_candles):
        btc_ts_to_idx[bc.timestamp] = idx

    cooldown_bars = int(cooldown_hours * (1 if timeframe == "1h" else 12))

    d1_state = None
    cooldown_until_idx = -1
    triggers = []
    target_bar_ts_ms = target_bar_ts_s * 1000 if target_bar_ts_s is not None else None
    target_found = False

    for i in range(D1_MIN_BARS, len(candles)):
        current_bar = candles[i]

        # Spot-check: if we've passed the target bar, stop
        if target_bar_ts_ms is not None and current_bar.timestamp > target_bar_ts_ms and target_found:
            break

        # Cooldown check
        in_cooldown = i < cooldown_until_idx
        if in_cooldown and (target_bar_ts_ms is None or current_bar.timestamp != target_bar_ts_ms):
            continue

        # Volume band check
        current_vol = vol_24h[i]
        vol_in_band = MIN_VOL_USD <= current_vol <= MAX_VOL_USD

        if not vol_in_band and not in_cooldown:
            if d1_state and d1_state.phase != D1Phase.COOLDOWN:
                d1_state = None
            # In spot-check mode, still show state at target bar even if out of band
            if target_bar_ts_ms is not None and current_bar.timestamp == target_bar_ts_ms:
                _print_spot_check(
                    current_bar, i, d1_state, current_vol,
                    triggered=False,
                    reason=f"OUTSIDE volume band (${current_vol:,.0f} not in "
                           f"[${MIN_VOL_USD:,.0f}, ${MAX_VOL_USD:,.0f}])",
                )
                target_found = True
            continue

        # Build candle window for FeatureFactory (needs 201 bars)
        window = candles[max(0, i - D1_MIN_BARS + 1):i + 1]
        if len(window) < D1_MIN_BARS:
            continue

        # Compute indicators
        try:
            snapshot = ff.compute(window)
        except Exception as e:
            if target_bar_ts_ms is not None and current_bar.timestamp == target_bar_ts_ms:
                _print_spot_check(
                    current_bar, i, d1_state, current_vol,
                    triggered=False,
                    reason=f"FeatureFactory.compute() failed: {e}",
                )
                target_found = True
            continue

        # BTC returns for beta computation (align by bar index in BTC series)
        btc_rets_window = None
        if btc_rets is not None:
            # Find BTC index for the current bar's timestamp
            btc_idx = btc_ts_to_idx.get(current_bar.timestamp)
            if btc_idx is not None and btc_idx > 0:
                start_idx = max(0, btc_idx - D1Config.BTC_BETA_WINDOW)
                # btc_rets has one fewer element than btc_candles (diff)
                btc_rets_window = btc_rets[start_idx:btc_idx]

        # Compute D1 state
        try:
            d1_state = evaluator.compute_state(
                snapshot, prev_state=d1_state,
                btc_returns=btc_rets_window,
            )
        except Exception as e:
            if target_bar_ts_ms is not None and current_bar.timestamp == target_bar_ts_ms:
                _print_spot_check(
                    current_bar, i, d1_state, current_vol,
                    triggered=False,
                    reason=f"compute_state() failed: {e}",
                )
                target_found = True
            continue

        # Evaluate trigger (from DORMANT_CONFIRMED or ACCUMULATING)
        signal = None
        if d1_state.phase in (D1Phase.DORMANT_CONFIRMED, D1Phase.ACCUMULATING):
            try:
                signal = evaluator.evaluate_trigger(
                    snapshot, d1_state, symbol, timeframe,
                )
            except Exception:
                pass

        # Spot-check mode: print state at target bar
        if target_bar_ts_ms is not None and current_bar.timestamp == target_bar_ts_ms:
            _print_spot_check(
                current_bar, i, d1_state, current_vol,
                triggered=signal is not None,
                signal=signal,
            )
            target_found = True

        if signal:
            trigger_info = _build_trigger_info(signal, current_bar, i, d1_state, current_vol)
            triggers.append(trigger_info)

            # Print trigger in full-replay mode
            if target_bar_ts_ms is None:
                _print_trigger(trigger_info)

            # Reset state + enter cooldown
            d1_state = None
            cooldown_until_idx = i + cooldown_bars

    # Spot-check: warn if target bar was never found
    if target_bar_ts_ms is not None and not target_found:
        logger.warning(
            f"Target bar ts={target_bar_ts_s} "
            f"({datetime.fromtimestamp(target_bar_ts_s, tz=timezone.utc).isoformat()}) "
            f"not found in OHLCV data. "
            f"Data range: {datetime.fromtimestamp(candles[0].timestamp / 1000, tz=timezone.utc).isoformat()} "
            f"to {datetime.fromtimestamp(candles[-1].timestamp / 1000, tz=timezone.utc).isoformat()}"
        )

    return triggers


# =========================================================================
# Output Formatting
# =========================================================================

def _ts_to_iso(ts_ms: int) -> str:
    """Convert millisecond timestamp to ISO string."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _print_spot_check(
    bar: OHLCV,
    bar_idx: int,
    state: D1State | None,
    vol_24h: float,
    triggered: bool,
    reason: str | None = None,
    signal: D1Signal | None = None,
):
    """Print detailed state at a specific bar for spot-checking."""
    print()
    print("=" * 72)
    print(f"  SPOT CHECK — bar_ts={bar.timestamp // 1000} "
          f"({_ts_to_iso(bar.timestamp)})")
    print("=" * 72)
    print()
    print(f"  Bar index:       {bar_idx}")
    print(f"  OHLCV:           O={bar.open:.8g}  H={bar.high:.8g}  "
          f"L={bar.low:.8g}  C={bar.close:.8g}  V={bar.volume:.2f}")
    print(f"  24h quote vol:   ${vol_24h:,.0f}")
    print()

    if state is None:
        print(f"  D1 State:        None (not initialized)")
    else:
        print(f"  Phase:           {state.phase.value}")
        print(f"  Dormancy score:  {state.dormancy_score:.4f}  "
              f"(threshold: {D1Config.DORMANCY_THRESHOLD:.4f})")
        print(f"  BB width:        {state.bb_width:.6f}  "
              f"(max: {D1Config.BB_WIDTH_COMPRESS:.6f})")
        print(f"  BB width prev:   {state.bb_width_prev:.6f}  "
              f"(expanding: {'YES' if state.bb_width > state.bb_width_prev else 'NO'})")
        print(f"  Volume Z:        {state.volume_z:.4f}  "
              f"(threshold: {D1Config.VOLUME_Z_SPIKE:.4f})")
        print(f"  RVOL:            {state.rvol:.4f}  "
              f"(threshold: {D1Config.RVOL_MIN:.4f})")
        print(f"  Accum diverge:   {state.accum_divergence:.6f}")
        print(f"  Compress high:   {state.compression_box_high:.8g}")
        print(f"  Compress low:    {state.compression_box_low:.8g}")
        print(f"  Prev comp high:  {state.prev_compression_box_high:.8g}")
        print(f"  Prev comp low:   {state.prev_compression_box_low:.8g}")
        print(f"  BTC beta:        {state.btc_beta:.4f}")
        print(f"  Bar index (D1):  {state.bar_index}")

    print()
    if triggered and signal:
        print(f"  >>> TRIGGERED <<<")
        print(f"  Entry:           {signal.entry_price:.8g}")
        print(f"  Stop:            {signal.stop_price:.8g}")
        print(f"  TP1:             {signal.tp1_price:.8g}")
        print(f"  TP2:             {signal.tp2_price:.8g}")
        print(f"  R-value:         {signal.r_value:.2f}")
        print(f"  Score:           {signal.setup_score:.4f}")
        print(f"  Score components: {json.dumps(signal.score_components, indent=2)}")
        if signal.explain:
            print(f"  Why:")
            for item in signal.explain.why_items:
                print(f"    - {item}")
    else:
        print(f"  >>> NO TRIGGER <<<")
        if reason:
            print(f"  Reason: {reason}")
        elif state:
            # Diagnose why it didn't trigger
            reasons = _diagnose_no_trigger(state, bar)
            if reasons:
                print(f"  Gate failures:")
                for r in reasons:
                    print(f"    - {r}")

    print()
    print("=" * 72)


def _diagnose_no_trigger(state: D1State, bar: OHLCV) -> list[str]:
    """Diagnose which trigger gates would fail at this state."""
    reasons = []

    if state.phase not in (D1Phase.DORMANT_CONFIRMED, D1Phase.ACCUMULATING):
        reasons.append(f"Phase is {state.phase.value} (need DORMANT_CONFIRMED or ACCUMULATING)")
        return reasons  # No point checking gates if phase is wrong

    close = bar.close

    if state.dormancy_confirmed_at_bar == 0:
        reasons.append("Dormancy never confirmed (dormancy_confirmed_at_bar == 0)")

    if D1Config.TRIGGER_REQUIRE_DORMANCY and state.dormancy_score < D1Config.DORMANCY_THRESHOLD:
        reasons.append(
            f"Gate 1 FAIL: dormancy_score {state.dormancy_score:.4f} "
            f"< threshold {D1Config.DORMANCY_THRESHOLD:.4f}"
        )

    if D1Config.TRIGGER_REQUIRE_COMPRESSION and state.bb_width >= D1Config.BB_WIDTH_COMPRESS:
        reasons.append(
            f"Gate 2 FAIL: bb_width {state.bb_width:.6f} "
            f">= compress threshold {D1Config.BB_WIDTH_COMPRESS:.6f}"
        )

    if state.volume_z < D1Config.VOLUME_Z_SPIKE:
        reasons.append(
            f"Gate 3a FAIL: volume_z {state.volume_z:.4f} "
            f"< threshold {D1Config.VOLUME_Z_SPIKE:.4f}"
        )

    if state.rvol < D1Config.RVOL_MIN:
        reasons.append(
            f"Gate 3b FAIL: rvol {state.rvol:.4f} "
            f"< threshold {D1Config.RVOL_MIN:.4f}"
        )

    if (
        D1Config.REQUIRE_ACCUM_DIVERGENCE_GATE
        and state.accum_divergence < D1Config.ACCUM_DIVERGENCE_MIN
    ):
        reasons.append(
            f"Gate (optional) FAIL: accum_divergence {state.accum_divergence:.6f} "
            f"< min {D1Config.ACCUM_DIVERGENCE_MIN:.6f}"
        )

    if state.prev_compression_box_high <= 0:
        reasons.append("Gate 4 FAIL: prev_compression_box_high <= 0")
    elif close <= state.prev_compression_box_high:
        reasons.append(
            f"Gate 4 FAIL: close {close:.8g} "
            f"<= prev_compression_box_high {state.prev_compression_box_high:.8g}"
        )

    if state.bb_width <= state.bb_width_prev:
        reasons.append(
            f"Gate 5 FAIL: bb_width {state.bb_width:.6f} "
            f"<= bb_width_prev {state.bb_width_prev:.6f} (not expanding)"
        )

    if not reasons:
        reasons.append("All gates pass — trigger should have fired (check evaluate_trigger)")

    return reasons


def _build_trigger_info(
    signal: D1Signal,
    bar: OHLCV,
    bar_idx: int,
    state: D1State,
    vol_24h: float,
) -> dict:
    """Build a serialisable dict from a trigger event."""
    return {
        "bar_ts": bar.timestamp // 1000,
        "bar_ts_iso": _ts_to_iso(bar.timestamp),
        "bar_idx": bar_idx,
        "close": bar.close,
        "vol_24h_usd": vol_24h,
        "entry": signal.entry_price,
        "stop": signal.stop_price,
        "tp1": signal.tp1_price,
        "tp2": signal.tp2_price,
        "r_value": signal.r_value,
        "score": signal.setup_score,
        "dormancy_score": signal.dormancy_score,
        "volume_z": signal.volume_z_at_trigger,
        "rvol": signal.rvol_at_trigger,
        "bb_width": signal.bb_width_at_trigger,
        "accum_divergence": signal.accum_divergence,
        "btc_beta": signal.btc_beta,
        "compression_box_high": signal.compression_box_high,
        "compression_box_low": signal.compression_box_low,
        "score_components": signal.score_components,
        "why": signal.explain.why_items if signal.explain else [],
    }


def _print_trigger(info: dict):
    """Print a single trigger in full-replay mode."""
    print(
        f"  TRIGGER  {info['bar_ts_iso']}  ts={info['bar_ts']}  "
        f"close={info['close']:.8g}  "
        f"score={info['score']:.3f}  "
        f"dorm={info['dormancy_score']:.2f}  "
        f"vol_z={info['volume_z']:.1f}  "
        f"rvol={info['rvol']:.1f}  "
        f"bb_w={info['bb_width']:.4f}  "
        f"R={info['r_value']:.1f}"
    )


# =========================================================================
# Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="D1 ground-truth replay — verify backfill correctness"
    )
    parser.add_argument("--symbol", type=str, required=True,
                        help="Symbol (e.g. ETHUSDT)")
    parser.add_argument("--timeframe", type=str, default="1h",
                        help="Timeframe (default: 1h)")
    parser.add_argument("--bar-ts", type=int, default=None,
                        help="Spot-check: epoch seconds of the bar to inspect")
    parser.add_argument("--cooldown-hours", type=int, default=24,
                        help="Cooldown between signals (default: 24)")

    # D1Config threshold overrides (same pattern as backfill script)
    parser.add_argument("--dormancy-threshold", type=float, default=None,
                        help="Override D1Config.DORMANCY_THRESHOLD")
    parser.add_argument("--rvol-min", type=float, default=None,
                        help="Override D1Config.RVOL_MIN")
    parser.add_argument("--volume-z-min", type=float, default=None,
                        help="Override D1Config.VOLUME_Z_SPIKE")
    parser.add_argument("--bb-width-max", type=float, default=None,
                        help="Override D1Config.BB_WIDTH_COMPRESS")
    parser.add_argument("--dormancy-range-threshold", type=float, default=None,
                        help="Override D1Config.DORMANCY_RANGE_THRESHOLD")
    args = parser.parse_args()

    # Apply config overrides (same pattern as backfill_d1_opportunities.py lines 555-564)
    if args.dormancy_threshold is not None:
        D1Config.DORMANCY_THRESHOLD = args.dormancy_threshold
    if args.rvol_min is not None:
        D1Config.RVOL_MIN = args.rvol_min
    if args.volume_z_min is not None:
        D1Config.VOLUME_Z_SPIKE = args.volume_z_min
    if args.bb_width_max is not None:
        D1Config.BB_WIDTH_COMPRESS = args.bb_width_max
    if args.dormancy_range_threshold is not None:
        D1Config.DORMANCY_RANGE_THRESHOLD = args.dormancy_range_threshold

    # Persistent output file
    if os.path.isdir("/scripts/output"):
        output_dir = "/scripts/output"
    elif os.path.isdir(os.path.join(os.path.dirname(__file__), "output")):
        output_dir = os.path.join(os.path.dirname(__file__), "output")
    else:
        output_dir = "/scripts/output"
    os.makedirs(output_dir, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(
        output_dir,
        f"replay_d1_{args.symbol}_{args.timeframe}_{ts_tag}.json"
    )
    logger.info("Output file: %s", output_path)

    if not DATABASE_URL:
        logger.error("DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    # Header
    print()
    print("=" * 72)
    print("  D1 Ground-Truth Replay")
    print("=" * 72)
    print(f"  Symbol:              {args.symbol}")
    print(f"  Timeframe:           {args.timeframe}")
    print(f"  Mode:                {'Spot-check' if args.bar_ts else 'Full replay'}")
    if args.bar_ts:
        print(f"  Target bar:          {args.bar_ts} "
              f"({datetime.fromtimestamp(args.bar_ts, tz=timezone.utc).isoformat()})")
    print(f"  Cooldown:            {args.cooldown_hours}h")
    print(f"  D1 config:")
    print(f"    Dormancy threshold:      {D1Config.DORMANCY_THRESHOLD}")
    print(f"    Dormancy range thresh:   {D1Config.DORMANCY_RANGE_THRESHOLD}")
    print(f"    BB width compress:       {D1Config.BB_WIDTH_COMPRESS}")
    print(f"    Volume Z spike:          {D1Config.VOLUME_Z_SPIKE}")
    print(f"    RVOL min:                {D1Config.RVOL_MIN}")
    print(f"    Volume band:             ${MIN_VOL_USD:,.0f} - ${MAX_VOL_USD:,.0f}")
    print(f"    Stop pct:                {D1Config.STOP_PCT:.1%}")
    print(f"    TP1 / TP2:               {D1Config.TP1_PCT:.0%} / {D1Config.TP2_PCT:.0%}")
    print("=" * 72)
    print()

    # Check exclusion
    if is_d1_excluded(args.symbol):
        logger.warning(f"{args.symbol} is in D1 exclusion list (major/stablecoin/wrapped/leverage)")
        logger.warning("Proceeding anyway for diagnostic purposes...")

    # Load data
    t0 = time.time()
    candles = load_symbol_ohlcv(args.symbol, args.timeframe)
    if not candles:
        logger.error(f"No OHLCV data found for {args.symbol} / {args.timeframe}")
        sys.exit(1)

    btc_candles = load_btc_ohlcv(args.timeframe)
    vol_24h = compute_rolling_24h_volume(candles, args.timeframe)

    logger.info(f"Data loaded in {time.time() - t0:.1f}s")
    logger.info(
        f"Date range: "
        f"{_ts_to_iso(candles[0].timestamp)} to {_ts_to_iso(candles[-1].timestamp)}"
    )

    # Run replay
    t1 = time.time()
    triggers = replay_symbol(
        symbol=args.symbol,
        timeframe=args.timeframe,
        candles=candles,
        vol_24h=vol_24h,
        btc_candles=btc_candles,
        target_bar_ts_s=args.bar_ts,
        cooldown_hours=args.cooldown_hours,
    )
    elapsed = time.time() - t1

    # Summary
    print()
    print("-" * 72)
    print(f"  Replay complete: {len(triggers)} trigger(s) found "
          f"in {elapsed:.1f}s ({len(candles):,} bars)")
    print("-" * 72)

    # Write output
    output_data = {
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "mode": "spot_check" if args.bar_ts else "full_replay",
        "target_bar_ts": args.bar_ts,
        "config": {
            "dormancy_threshold": D1Config.DORMANCY_THRESHOLD,
            "dormancy_range_threshold": D1Config.DORMANCY_RANGE_THRESHOLD,
            "bb_width_compress": D1Config.BB_WIDTH_COMPRESS,
            "volume_z_spike": D1Config.VOLUME_Z_SPIKE,
            "rvol_min": D1Config.RVOL_MIN,
            "stop_pct": D1Config.STOP_PCT,
            "tp1_pct": D1Config.TP1_PCT,
            "tp2_pct": D1Config.TP2_PCT,
            "min_volume_usd": MIN_VOL_USD,
            "max_volume_usd": MAX_VOL_USD,
        },
        "total_bars": len(candles),
        "data_range": {
            "start": _ts_to_iso(candles[0].timestamp),
            "end": _ts_to_iso(candles[-1].timestamp),
        },
        "triggers": triggers,
        "elapsed_seconds": round(elapsed, 2),
    }

    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2, default=str)

    logger.info(f"Results written to {output_path}")


if __name__ == "__main__":
    main()
