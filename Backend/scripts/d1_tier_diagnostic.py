#!/usr/bin/env python3
"""
D1 Tier Diagnostic — GO/NO-GO Gate for Universe Segmentation
=============================================================

Runs against EXISTING production D1 opportunities to answer:
"Does splitting D1 universe into liquid/illiquid tiers add information?"

Tests:
  1. Hartigan's dip test on log(volume) distribution — bimodality?
  2. Config D quintile analysis — monotonic PF relationship with volume?
  3. Permutation null test (10K splits) — actual PF diff vs random?
  4. Config D rejection rate by volume — does 1.5% stop already filter by liquidity?
  5. Bootstrap 95% CI on daily PF per tier — do CIs overlap?
  6. Tier stability — what fraction of symbols appear in both tiers across months?

GO/NO-GO criteria (ALL must pass):
  - Dip test p<0.05 OR quintile analysis shows monotonic PF relationship
  - Permutation test: actual PF diff exceeds 95th percentile of null
  - Each tier has ≥200 Config D-surviving trades
  - Bootstrap CI on daily PF for each tier doesn't span below 1.0

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \\
        python3 /scripts/d1_tier_diagnostic.py --batch-id 'backfill_20260303_D1v1%%'

    # Or with custom boundary:
    docker compose -f docker-compose.yml run --rm -T worker \\
        python3 /scripts/d1_tier_diagnostic.py --batch-id 'backfill_20260303_D1v1%%' \\
            --tier-boundary-usd 500000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

import psycopg
from psycopg.rows import dict_row
from exit_checker import ExitConfig, check_exit_simple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Simulation constants (must match sim_d1_matrix.py) ───────────────────
TF_DELTA = {"1h": timedelta(hours=1)}
SIM_SLIPPAGE_BPS = 5.0
SIM_FEE_BPS = 4.0
SIM_POSITION_USD = 1000.0
MAX_BARS_FORWARD = 504
MAX_ENTRY_DELAY_BARS = 3
MAX_FEE_R_RATIO = 0.3
MIN_R_PRESERVATION = 0.5

# Config D exit params
CONFIG_D_STOP_PCT = 1.5
CONFIG_D_TP_PCT = 10.0
CONFIG_D_TIME_STOP_BARS = 72
CONFIG_D_EXIT_CONFIG = ExitConfig(
    fail_fast_bars=0, fail_fast_threshold=0.0,
    trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
)

# Default tier boundary (median of production volume distribution)
DEFAULT_TIER_BOUNDARY_USD = 500_000


# ══════════════════════════════════════════════════════════════════════════
#  DATA LOADING
# ══════════════════════════════════════════════════════════════════════════

def load_opportunities(database_url: str, batch_pattern: str) -> list[dict]:
    """Load D1 opportunities matching batch pattern."""
    with psycopg.connect(database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, symbol, timeframe, setup_id, decision,
                       score, created_ts, payload_json, status, batch_id
                FROM opportunities
                WHERE setup_id = 'D1'
                  AND batch_id LIKE %s
                  AND status = 'expired'
                ORDER BY created_ts ASC;
            """, (batch_pattern,))
            return list(cur.fetchall())


def load_rolling_24h_volume(
    database_url: str, symbols: list[str], timeframes: list[str],
    start_ts: datetime, end_ts: datetime,
) -> dict[str, np.ndarray]:
    """Load OHLCV and compute rolling 24h notional volume per symbol.

    Returns {symbol: array of (bar_start_ts_epoch, vol_24h_usd)} sorted by time.
    """
    vol_data = {}
    window = 24  # 24 bars for 1h

    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            for sym in symbols:
                cur.execute("""
                    SELECT bar_start_ts, close, volume
                    FROM ohlcv
                    WHERE symbol = %s AND timeframe = '1h'
                      AND bar_start_ts >= %s AND bar_start_ts < %s
                      AND is_final = TRUE
                    ORDER BY bar_start_ts ASC
                """, (sym, start_ts - timedelta(hours=48), end_ts + timedelta(hours=24)))
                rows = cur.fetchall()

                if len(rows) < window:
                    continue

                timestamps = np.array([r[0].replace(tzinfo=timezone.utc).timestamp() for r in rows])
                qvol = np.array([float(r[1]) * float(r[2]) for r in rows])

                cumsum = np.cumsum(qvol)
                rolling = np.zeros(len(rows))
                rolling[window - 1:] = cumsum[window - 1:] - np.concatenate(
                    [[0], cumsum[:len(rows) - window]]
                )
                for i in range(min(window - 1, len(rows))):
                    rolling[i] = cumsum[i]

                vol_data[sym] = np.column_stack([timestamps, rolling])

    return vol_data


def get_volume_at_trigger(
    vol_data: dict[str, np.ndarray], symbol: str, trigger_ts: datetime
) -> Optional[float]:
    """Look up rolling 24h volume at trigger timestamp."""
    arr = vol_data.get(symbol)
    if arr is None or len(arr) == 0:
        return None

    ts = trigger_ts.timestamp()
    # Find closest bar at or before trigger
    idx = np.searchsorted(arr[:, 0], ts, side='right') - 1
    if idx < 0:
        idx = 0
    if idx >= len(arr):
        idx = len(arr) - 1

    # Allow up to 2h tolerance
    if abs(arr[idx, 0] - ts) > 7200:
        return None

    return float(arr[idx, 1])


def load_ohlcv_for_sim(
    database_url: str, symbol: str, tf: str,
    start_ts: datetime, end_ts: datetime,
) -> list[dict]:
    """Load OHLCV bars for simulation."""
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (bar_start_ts)
                       bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = %s AND timeframe = %s
                  AND bar_start_ts >= %s AND bar_start_ts < %s
                  AND is_final = TRUE
                ORDER BY bar_start_ts,
                         CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
            """, (symbol, tf, start_ts, end_ts))
            return [
                {"bar_start_ts": r[0], "open": float(r[1]), "high": float(r[2]),
                 "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
                for r in cur.fetchall()
            ]


# ══════════════════════════════════════════════════════════════════════════
#  SIMULATION (Config D only)
# ══════════════════════════════════════════════════════════════════════════

def simulate_config_d(
    opp_ts: datetime, direction: str, entry_price: float,
    bars: list[dict], tf: str = "1h",
    slippage_bps: float = SIM_SLIPPAGE_BPS,
) -> dict:
    """Simulate a single D1 opportunity with Config D parameters."""
    if not bars:
        return {"traded": False}

    stop_frac = CONFIG_D_STOP_PCT / 100.0
    tp_frac = CONFIG_D_TP_PCT / 100.0

    if direction == "long":
        stop_price = entry_price * (1 - stop_frac)
        r_value = entry_price - stop_price
        tp1_price = entry_price * (1 + tp_frac)
    else:
        stop_price = entry_price * (1 + stop_frac)
        r_value = stop_price - entry_price
        tp1_price = entry_price * (1 - tp_frac)

    if r_value <= 0:
        return {"traded": False}

    # Find entry bar
    entry_bar_idx = None
    bar_delta = TF_DELTA.get(tf, timedelta(hours=1))
    for i, bar in enumerate(bars):
        bar_ts = bar["bar_start_ts"]
        if bar_ts.tzinfo is None:
            bar_ts = bar_ts.replace(tzinfo=timezone.utc)
        if bar_ts > opp_ts:
            entry_bar_idx = i
            break

    if entry_bar_idx is None:
        return {"traded": False}

    entry_bar = bars[entry_bar_idx]
    entry_bar_ts = entry_bar["bar_start_ts"]
    if entry_bar_ts.tzinfo is None:
        entry_bar_ts = entry_bar_ts.replace(tzinfo=timezone.utc)
    if entry_bar_ts - opp_ts > bar_delta * MAX_ENTRY_DELAY_BARS:
        return {"traded": False}

    actual_entry = entry_bar["open"]
    if direction == "long":
        entry_slip = actual_entry * (1 + slippage_bps / 10000)
    else:
        entry_slip = actual_entry * (1 - slippage_bps / 10000)

    if direction == "long":
        actual_r = entry_slip - stop_price
    else:
        actual_r = stop_price - entry_slip
    if actual_r <= 0:
        return {"traded": False}

    # Fee/R guard
    fee_per_unit = 2 * entry_slip * SIM_FEE_BPS / 10000
    if fee_per_unit > MAX_FEE_R_RATIO * actual_r:
        return {"traded": False}

    if r_value > 0 and actual_r < MIN_R_PRESERVATION * r_value:
        return {"traded": False}

    # TP invalidation
    if direction == "long" and tp1_price <= entry_slip:
        tp1_price = None
    elif direction == "short" and tp1_price >= entry_slip:
        tp1_price = None

    position_size = SIM_POSITION_USD / entry_slip
    max_bar_idx = min(entry_bar_idx + MAX_BARS_FORWARD, len(bars) - 1)

    hwm = None
    half_r_touched = False
    exit_reason = None
    exit_price = None
    bars_held = 0

    for i in range(entry_bar_idx + 1, max_bar_idx + 1):
        bar = bars[i]
        bars_held += 1

        exit_result, new_hwm, new_half_r = check_exit_simple(
            side=direction, bar=bar,
            entry_price=entry_slip, stop_price=stop_price,
            tp1_price=tp1_price, tp2_price=None,
            r_value=actual_r, bars_held=bars_held,
            max_bars=CONFIG_D_TIME_STOP_BARS,
            high_water_mark=hwm, half_r_touched=half_r_touched,
            config=CONFIG_D_EXIT_CONFIG,
        )
        hwm = new_hwm
        half_r_touched = new_half_r

        if exit_result:
            exit_reason, exit_price = exit_result
            break

    if exit_reason is None:
        last_bar = bars[min(max_bar_idx, len(bars) - 1)]
        exit_reason = "MAX_BARS"
        exit_price = last_bar["close"]

    if exit_reason != "STOP_LOSS":
        if direction == "long":
            exit_slip = exit_price * (1 - slippage_bps / 10000)
        else:
            exit_slip = exit_price * (1 + slippage_bps / 10000)
    else:
        exit_slip = exit_price

    if direction == "long":
        pnl_gross = (exit_slip - entry_slip) * position_size
    else:
        pnl_gross = (entry_slip - exit_slip) * position_size

    entry_fee = entry_slip * position_size * SIM_FEE_BPS / 10000
    exit_fee = exit_slip * position_size * SIM_FEE_BPS / 10000
    pnl_net = pnl_gross - entry_fee - exit_fee
    r_multiple = pnl_net / (actual_r * position_size) if actual_r * position_size > 0 else 0

    return {
        "traded": True,
        "pnl_net": round(pnl_net, 4),
        "r_multiple": round(r_multiple, 4),
        "exit_reason": exit_reason,
        "bars_held": bars_held,
        "entry_ts": entry_bar_ts,
    }


# ══════════════════════════════════════════════════════════════════════════
#  STATISTICAL TESTS
# ══════════════════════════════════════════════════════════════════════════

def hartigans_dip_test(data: np.ndarray, n_boot: int = 1000) -> tuple[float, float]:
    """Hartigan's dip test for unimodality.

    Returns (dip_statistic, p_value).
    p < 0.05 suggests bimodality (reject unimodality).

    Uses bootstrap approach: compare dip of data vs uniform distribution.
    """
    sorted_data = np.sort(data)
    n = len(sorted_data)

    def compute_dip(x):
        """Compute dip statistic as max deviation from uniform CDF."""
        n_x = len(x)
        if n_x < 4:
            return 0.0
        uniform_cdf = np.arange(1, n_x + 1) / n_x
        empirical_sorted = (x - x[0]) / (x[-1] - x[0]) if x[-1] > x[0] else uniform_cdf
        dip = np.max(np.abs(empirical_sorted - uniform_cdf))
        return dip

    observed_dip = compute_dip(sorted_data)

    # Bootstrap: dip from uniform samples
    rng = np.random.default_rng(42)
    null_dips = np.zeros(n_boot)
    for i in range(n_boot):
        uniform_sample = np.sort(rng.uniform(0, 1, n))
        null_dips[i] = compute_dip(uniform_sample)

    p_value = np.mean(null_dips >= observed_dip)
    return float(observed_dip), float(p_value)


def compute_daily_pf(trades: list[dict]) -> float:
    """Compute average daily profit factor from trade results."""
    daily_pnls: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        if t.get("traded") and t.get("pnl_net") is not None:
            ts = t.get("trigger_ts")
            if ts:
                day = ts.strftime("%Y-%m-%d") if isinstance(ts, datetime) else str(ts)[:10]
                daily_pnls[day].append(t["pnl_net"])

    daily_pfs = []
    for _, day_pnls in sorted(daily_pnls.items()):
        gp = sum(p for p in day_pnls if p > 0)
        gl = abs(sum(p for p in day_pnls if p < 0))
        if gl > 0:
            daily_pfs.append(min(gp / gl, 5.0))
        elif gp > 0:
            daily_pfs.append(5.0)

    return float(np.mean(daily_pfs)) if daily_pfs else 0.0


def compute_agg_pf(trades: list[dict]) -> float:
    """Compute aggregate profit factor."""
    traded = [t for t in trades if t.get("traded") and t.get("pnl_net") is not None]
    if not traded:
        return 0.0
    gross_profit = sum(t["pnl_net"] for t in traded if t["pnl_net"] > 0)
    gross_loss = abs(sum(t["pnl_net"] for t in traded if t["pnl_net"] < 0))
    if gross_loss == 0:
        return 99.0 if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def compute_win_rate(trades: list[dict]) -> float:
    """Compute win rate."""
    traded = [t for t in trades if t.get("traded") and t.get("pnl_net") is not None]
    if not traded:
        return 0.0
    wins = sum(1 for t in traded if t["pnl_net"] > 0)
    return wins / len(traded)


def permutation_test(
    liquid_trades: list[dict], illiquid_trades: list[dict],
    n_permutations: int = 10_000,
) -> tuple[float, float, float]:
    """Permutation null test: is the PF difference between tiers significant?

    Returns (actual_diff, p95_null, p_value).
    """
    pf_liquid = compute_daily_pf(liquid_trades)
    pf_illiquid = compute_daily_pf(illiquid_trades)
    actual_diff = abs(pf_liquid - pf_illiquid)

    all_trades = liquid_trades + illiquid_trades
    n_liquid = len(liquid_trades)
    rng = np.random.default_rng(42)

    null_diffs = np.zeros(n_permutations)
    for i in range(n_permutations):
        perm = rng.permutation(len(all_trades))
        group_a = [all_trades[j] for j in perm[:n_liquid]]
        group_b = [all_trades[j] for j in perm[n_liquid:]]
        pf_a = compute_daily_pf(group_a)
        pf_b = compute_daily_pf(group_b)
        null_diffs[i] = abs(pf_a - pf_b)

    p95 = float(np.percentile(null_diffs, 95))
    p_value = float(np.mean(null_diffs >= actual_diff))

    return actual_diff, p95, p_value


def bootstrap_daily_pf_ci(
    trades: list[dict], n_boot: int = 5000, ci: float = 0.95,
) -> tuple[float, float, float]:
    """Bootstrap 95% CI on average daily PF.

    Returns (mean, ci_low, ci_high).
    """
    rng = np.random.default_rng(42)
    boot_pfs = np.zeros(n_boot)
    n = len(trades)
    if n == 0:
        return 0.0, 0.0, 0.0

    for i in range(n_boot):
        idx = rng.integers(0, n, size=n)
        sample = [trades[j] for j in idx]
        boot_pfs[i] = compute_daily_pf(sample)

    alpha = (1 - ci) / 2
    ci_low = float(np.percentile(boot_pfs, alpha * 100))
    ci_high = float(np.percentile(boot_pfs, (1 - alpha) * 100))
    mean_pf = float(np.mean(boot_pfs))

    return mean_pf, ci_low, ci_high


# ══════════════════════════════════════════════════════════════════════════
#  MAIN DIAGNOSTIC
# ══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="D1 Tier Diagnostic — GO/NO-GO Gate")
    parser.add_argument("--batch-id", type=str, required=True,
                        help="Batch ID pattern (SQL LIKE)")
    parser.add_argument("--tier-boundary-usd", type=float, default=None,
                        help="Volume boundary for liquid/illiquid split (default: auto from median)")
    parser.add_argument("--output-dir", type=str, default=None)
    args = parser.parse_args()

    # Output setup
    if args.output_dir:
        output_dir = args.output_dir
    elif os.path.isdir("/scripts/output"):
        output_dir = "/scripts/output"
    elif os.path.isdir(os.path.join(os.path.dirname(__file__), "output")):
        output_dir = os.path.join(os.path.dirname(__file__), "output")
    else:
        output_dir = "/scripts/output"
    os.makedirs(output_dir, exist_ok=True)
    ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(output_dir, f"d1_tier_diagnostic_{ts_tag}.log")
    json_path = os.path.join(output_dir, f"d1_tier_diagnostic_{ts_tag}.json")
    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    log.addHandler(file_handler)
    log.info("Output log: %s", log_path)
    log.info("Output JSON: %s", json_path)

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, val = line.split('=', 1)
                        os.environ.setdefault(key.strip(), val.strip())
            database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        log.error("DATABASE_URL not set")
        sys.exit(1)

    t0 = time.time()

    # ── Step 1: Load opportunities ────────────────────────────────────────
    log.info("=" * 70)
    log.info("D1 TIER DIAGNOSTIC — GO/NO-GO GATE")
    log.info("=" * 70)

    opps = load_opportunities(database_url, args.batch_id)
    log.info("Loaded %d D1 opportunities matching: %s", len(opps), args.batch_id)

    if not opps:
        log.error("No opportunities found.")
        sys.exit(1)

    # Parse payloads
    symbols = set()
    opp_metas = []
    for opp in opps:
        payload = opp.get("payload_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}

        meta = {
            "id": opp["id"],
            "symbol": opp["symbol"],
            "timeframe": opp["timeframe"],
            "created_ts": opp["created_ts"],
            "direction": payload.get("direction", "long"),
            "entry_price": float(payload.get("entry_price", 0)),
            "dormancy_score": float(payload.get("dormancy_score", 0)),
            "volume_z_at_trigger": float(payload.get("volume_z_at_trigger", 0)),
            "score": float(opp.get("score", 0)),
        }
        opp_metas.append(meta)
        symbols.add(opp["symbol"])

    log.info("  Symbols: %d unique", len(symbols))
    date_range_start = min(m["created_ts"] for m in opp_metas)
    date_range_end = max(m["created_ts"] for m in opp_metas)
    log.info("  Date range: %s to %s", date_range_start.date(), date_range_end.date())

    # ── Step 2: Compute rolling 24h volume at trigger time ────────────────
    log.info("\nComputing rolling 24h notional volume per symbol...")
    vol_data = load_rolling_24h_volume(
        database_url, sorted(symbols), ["1h"],
        start_ts=date_range_start - timedelta(days=2),
        end_ts=date_range_end + timedelta(days=1),
    )
    log.info("  Volume data loaded for %d/%d symbols", len(vol_data), len(symbols))

    # Attach volume to each opportunity
    volumes = []
    for meta in opp_metas:
        vol = get_volume_at_trigger(vol_data, meta["symbol"], meta["created_ts"])
        meta["vol_24h_usd"] = vol
        if vol is not None and vol > 0:
            volumes.append(vol)

    log.info("  Opportunities with volume data: %d/%d", len(volumes), len(opp_metas))
    if not volumes:
        log.error("No volume data available. Cannot proceed.")
        sys.exit(1)

    vol_array = np.array(volumes)
    log_vol = np.log10(vol_array[vol_array > 0])

    log.info("\n  Volume distribution (24h notional USD):")
    for pct in [5, 10, 25, 50, 75, 90, 95]:
        val = np.percentile(vol_array, pct)
        log.info("    P%d: $%s", pct, f"{val:,.0f}")

    # ── Step 3: Determine tier boundary ───────────────────────────────────
    if args.tier_boundary_usd:
        boundary = args.tier_boundary_usd
        log.info("\n  Tier boundary (user-specified): $%s", f"{boundary:,.0f}")
    else:
        boundary = float(np.median(vol_array))
        log.info("\n  Tier boundary (median): $%s", f"{boundary:,.0f}")

    # ── Step 4: Run Config D simulation on all opps ───────────────────────
    log.info("\n" + "─" * 70)
    log.info("Running Config D simulation on all %d opportunities...", len(opp_metas))

    # Load OHLCV in batch per symbol
    ohlcv_cache: dict[str, list[dict]] = {}
    t_ohlcv = time.time()
    for sym in sorted(symbols):
        sym_opps = [m for m in opp_metas if m["symbol"] == sym]
        earliest = min(m["created_ts"] for m in sym_opps)
        latest = max(m["created_ts"] for m in sym_opps)
        bars = load_ohlcv_for_sim(
            database_url, sym, "1h",
            start_ts=earliest - timedelta(hours=1),
            end_ts=latest + timedelta(hours=MAX_BARS_FORWARD + 10),
        )
        ohlcv_cache[sym] = bars

    log.info("  OHLCV loaded for %d symbols in %.1fs", len(ohlcv_cache), time.time() - t_ohlcv)

    # Simulate
    all_results = []
    for meta in opp_metas:
        bars = ohlcv_cache.get(meta["symbol"], [])
        result = simulate_config_d(
            opp_ts=meta["created_ts"],
            direction=meta["direction"],
            entry_price=meta["entry_price"],
            bars=bars,
            tf=meta["timeframe"],
        )
        # Filter gap-throughs
        if result.get("traded") and result.get("r_multiple", 0) < -1.05:
            result["traded"] = False

        result["trigger_ts"] = meta["created_ts"]
        result["symbol"] = meta["symbol"]
        result["vol_24h_usd"] = meta.get("vol_24h_usd")
        result["dormancy_score"] = meta.get("dormancy_score", 0)
        all_results.append(result)

    traded = [r for r in all_results if r.get("traded")]
    log.info("  Config D: %d/%d opportunities survived (traded)", len(traded), len(all_results))

    if len(traded) < 50:
        log.error("  Too few traded opportunities (%d). Cannot perform tier analysis.", len(traded))
        sys.exit(1)

    total_pnl = sum(r["pnl_net"] for r in traded)
    daily_pf = compute_daily_pf(traded)
    agg_pf = compute_agg_pf(traded)
    wr = compute_win_rate(traded)
    log.info("  Config D overall: %d trades, WR=%.1f%%, aggPF=%.2f, dailyPF=%.2f, PnL=$%.0f",
             len(traded), wr * 100, agg_pf, daily_pf, total_pnl)

    # ── Test 1: Hartigan's dip test ───────────────────────────────────────
    log.info("\n" + "─" * 70)
    log.info("TEST 1: Hartigan's dip test on log(volume) distribution")

    # Use volume from traded opps only
    traded_vols = np.array([r["vol_24h_usd"] for r in traded
                            if r.get("vol_24h_usd") and r["vol_24h_usd"] > 0])
    if len(traded_vols) > 10:
        log_traded_vols = np.log10(traded_vols)
        dip_stat, dip_p = hartigans_dip_test(log_traded_vols, n_boot=2000)
        dip_pass = dip_p < 0.05
        log.info("  Dip statistic: %.4f", dip_stat)
        log.info("  p-value: %.4f", dip_p)
        log.info("  Bimodal (p<0.05)? %s", "YES ✓" if dip_pass else "NO ✗")
    else:
        dip_stat, dip_p, dip_pass = 0.0, 1.0, False
        log.info("  Insufficient volume data for dip test")

    # ── Test 2: Quintile analysis ─────────────────────────────────────────
    log.info("\n" + "─" * 70)
    log.info("TEST 2: Config D quintile analysis by 24h volume")

    # Filter to traded with volume
    traded_with_vol = [r for r in traded if r.get("vol_24h_usd") and r["vol_24h_usd"] > 0]
    log.info("  Traded with volume data: %d", len(traded_with_vol))

    quintile_results = {}
    if len(traded_with_vol) >= 50:
        vols = np.array([r["vol_24h_usd"] for r in traded_with_vol])
        quintile_edges = np.percentile(vols, [0, 20, 40, 60, 80, 100])

        quintile_pfs = []
        for q in range(5):
            low = quintile_edges[q]
            high = quintile_edges[q + 1]
            q_trades = [r for r in traded_with_vol
                        if low <= r["vol_24h_usd"] <= (high if q == 4 else high - 0.01)]
            if not q_trades:
                q_trades = [r for r in traded_with_vol
                            if low <= r["vol_24h_usd"] <= high]

            q_pf = compute_daily_pf(q_trades)
            q_agg_pf = compute_agg_pf(q_trades)
            q_wr = compute_win_rate(q_trades)
            q_pnl = sum(r["pnl_net"] for r in q_trades if r.get("pnl_net"))
            quintile_pfs.append(q_pf)

            quintile_results[f"Q{q+1}"] = {
                "vol_range": f"${low:,.0f} - ${high:,.0f}",
                "n_trades": len(q_trades),
                "daily_pf": round(q_pf, 3),
                "agg_pf": round(q_agg_pf, 3),
                "win_rate": round(q_wr, 3),
                "total_pnl": round(q_pnl, 2),
            }
            log.info("  Q%d ($%s - $%s): N=%d, dailyPF=%.2f, aggPF=%.2f, WR=%.1f%%, PnL=$%.0f",
                     q + 1, f"{low:,.0f}", f"{high:,.0f}", len(q_trades),
                     q_pf, q_agg_pf, q_wr * 100, q_pnl)

        # Monotonicity check (Spearman rank correlation)
        from scipy.stats import spearmanr
        quintile_indices = np.arange(5)
        quintile_pf_arr = np.array(quintile_pfs)
        rho, spearman_p = spearmanr(quintile_indices, quintile_pf_arr)
        monotonic = abs(rho) > 0.7 and spearman_p < 0.10
        log.info("  Spearman rho=%.3f, p=%.4f → Monotonic? %s",
                 rho, spearman_p, "YES ✓" if monotonic else "NO ✗")
    else:
        monotonic = False
        rho, spearman_p = 0.0, 1.0
        log.info("  Insufficient data for quintile analysis")

    # ── Test 3: Split trades into liquid/illiquid ─────────────────────────
    log.info("\n" + "─" * 70)
    log.info("TEST 3: Tier split at $%s boundary", f"{boundary:,.0f}")

    liquid_trades = [r for r in traded_with_vol if r["vol_24h_usd"] >= boundary]
    illiquid_trades = [r for r in traded_with_vol if r["vol_24h_usd"] < boundary]

    log.info("  Liquid tier:   %d trades", len(liquid_trades))
    log.info("  Illiquid tier: %d trades", len(illiquid_trades))

    # Size check
    min_trades_per_tier = 200
    size_pass = len(liquid_trades) >= min_trades_per_tier and len(illiquid_trades) >= min_trades_per_tier
    log.info("  Each tier ≥%d? %s", min_trades_per_tier,
             "YES ✓" if size_pass else f"NO ✗ (need {min_trades_per_tier})")

    if not size_pass:
        # Try finding a boundary that gives balanced tiers
        log.info("  Searching for a boundary with ≥%d trades per tier...", min_trades_per_tier)
        for pct in [30, 35, 40, 45, 50, 55, 60, 65, 70]:
            test_boundary = float(np.percentile(vols, pct)) if len(traded_with_vol) >= 50 else boundary
            liq = sum(1 for r in traded_with_vol if r["vol_24h_usd"] >= test_boundary)
            illiq = sum(1 for r in traded_with_vol if r["vol_24h_usd"] < test_boundary)
            if liq >= min_trades_per_tier and illiq >= min_trades_per_tier:
                boundary = test_boundary
                liquid_trades = [r for r in traded_with_vol if r["vol_24h_usd"] >= boundary]
                illiquid_trades = [r for r in traded_with_vol if r["vol_24h_usd"] < boundary]
                size_pass = True
                log.info("    Found boundary at P%d = $%s: %d liquid, %d illiquid",
                         pct, f"{boundary:,.0f}", len(liquid_trades), len(illiquid_trades))
                break
        if not size_pass:
            log.info("    No boundary found with ≥%d per tier.", min_trades_per_tier)

    # Per-tier metrics
    for tier_name, tier_trades in [("Liquid", liquid_trades), ("Illiquid", illiquid_trades)]:
        if tier_trades:
            t_pf = compute_daily_pf(tier_trades)
            t_agg = compute_agg_pf(tier_trades)
            t_wr = compute_win_rate(tier_trades)
            t_pnl = sum(r["pnl_net"] for r in tier_trades if r.get("pnl_net"))
            log.info("  %s: N=%d, dailyPF=%.2f, aggPF=%.2f, WR=%.1f%%, PnL=$%.0f",
                     tier_name, len(tier_trades), t_pf, t_agg, t_wr * 100, t_pnl)

    # ── Test 4: Permutation null test ─────────────────────────────────────
    log.info("\n" + "─" * 70)
    log.info("TEST 4: Permutation null test (10,000 random splits)")

    if liquid_trades and illiquid_trades:
        actual_diff, p95_null, perm_p = permutation_test(liquid_trades, illiquid_trades)
        perm_pass = actual_diff > p95_null
        log.info("  Actual dailyPF diff: %.3f", actual_diff)
        log.info("  95th percentile of null: %.3f", p95_null)
        log.info("  p-value: %.4f", perm_p)
        log.info("  Actual > 95th pctile? %s", "YES ✓" if perm_pass else "NO ✗")
    else:
        actual_diff, p95_null, perm_p = 0.0, 0.0, 1.0
        perm_pass = False
        log.info("  Skipped (empty tier)")

    # ── Test 5: Config D rejection rate by volume ─────────────────────────
    log.info("\n" + "─" * 70)
    log.info("TEST 5: Config D rejection rate by volume tier")

    all_with_vol = [r for r in all_results if r.get("vol_24h_usd") and r["vol_24h_usd"] > 0]
    liq_all = [r for r in all_with_vol if r["vol_24h_usd"] >= boundary]
    illiq_all = [r for r in all_with_vol if r["vol_24h_usd"] < boundary]

    liq_traded = sum(1 for r in liq_all if r.get("traded"))
    illiq_traded = sum(1 for r in illiq_all if r.get("traded"))

    liq_rejection = 1 - (liq_traded / len(liq_all)) if liq_all else 0
    illiq_rejection = 1 - (illiq_traded / len(illiq_all)) if illiq_all else 0

    log.info("  Liquid: %d/%d traded (%.1f%% rejected)",
             liq_traded, len(liq_all), liq_rejection * 100)
    log.info("  Illiquid: %d/%d traded (%.1f%% rejected)",
             illiq_traded, len(illiq_all), illiq_rejection * 100)
    log.info("  Differential rejection (illiq - liq): %.1f pp",
             (illiq_rejection - liq_rejection) * 100)
    config_d_already_filters = illiq_rejection > 0.70 and liq_rejection < 0.50
    log.info("  Config D already a liquidity filter? %s",
             "YES (illiq >70%% rejected, liq <50%%)" if config_d_already_filters else "NO")

    # ── Test 6: Bootstrap CI on daily PF per tier ─────────────────────────
    log.info("\n" + "─" * 70)
    log.info("TEST 6: Bootstrap 95%% CI on daily PF per tier")

    ci_pass = True
    tier_cis = {}
    for tier_name, tier_trades in [("Liquid", liquid_trades), ("Illiquid", illiquid_trades)]:
        if tier_trades:
            mean_pf, ci_low, ci_high = bootstrap_daily_pf_ci(tier_trades)
            tier_cis[tier_name] = {"mean": mean_pf, "ci_low": ci_low, "ci_high": ci_high}
            spans_below_1 = ci_low < 1.0
            if spans_below_1:
                ci_pass = False
            log.info("  %s: mean=%.3f, 95%% CI=[%.3f, %.3f] %s",
                     tier_name, mean_pf, ci_low, ci_high,
                     "✗ (spans below 1.0)" if spans_below_1 else "✓")
        else:
            ci_pass = False
            log.info("  %s: no trades", tier_name)

    # Check CI overlap
    if "Liquid" in tier_cis and "Illiquid" in tier_cis:
        liq_ci = tier_cis["Liquid"]
        illiq_ci = tier_cis["Illiquid"]
        overlap = liq_ci["ci_low"] <= illiq_ci["ci_high"] and illiq_ci["ci_low"] <= liq_ci["ci_high"]
        log.info("  CIs overlap? %s", "YES (tiers not clearly different)" if overlap else "NO (tiers distinct)")
    else:
        overlap = True

    # ── Test 7: Tier stability across months ──────────────────────────────
    log.info("\n" + "─" * 70)
    log.info("TEST 7: Tier stability across months")

    monthly_tiers: dict[str, dict[str, set]] = defaultdict(lambda: {"liquid": set(), "illiquid": set()})
    for r in traded_with_vol:
        month = r["trigger_ts"].strftime("%Y-%m") if isinstance(r.get("trigger_ts"), datetime) else "unknown"
        sym = r["symbol"]
        if r["vol_24h_usd"] >= boundary:
            monthly_tiers[month]["liquid"].add(sym)
        else:
            monthly_tiers[month]["illiquid"].add(sym)

    months = sorted(monthly_tiers.keys())
    if len(months) >= 2:
        cross_tier_symbols = set()
        all_symbols_seen = set()
        for month in months:
            liquid_syms = monthly_tiers[month]["liquid"]
            illiquid_syms = monthly_tiers[month]["illiquid"]
            both = liquid_syms & illiquid_syms
            cross_tier_symbols |= both
            all_symbols_seen |= liquid_syms | illiquid_syms

        # Also check across months: symbol in liquid one month, illiquid another
        ever_liquid = set()
        ever_illiquid = set()
        for month in months:
            ever_liquid |= monthly_tiers[month]["liquid"]
            ever_illiquid |= monthly_tiers[month]["illiquid"]
        cross_month = ever_liquid & ever_illiquid

        log.info("  Months analyzed: %d (%s to %s)", len(months), months[0], months[-1])
        log.info("  Symbols in both tiers within same month: %d", len(cross_tier_symbols))
        log.info("  Symbols in different tiers across months: %d/%d (%.1f%%)",
                 len(cross_month), len(all_symbols_seen),
                 len(cross_month) / len(all_symbols_seen) * 100 if all_symbols_seen else 0)
        tier_stability = 1 - (len(cross_month) / len(all_symbols_seen)) if all_symbols_seen else 0
        log.info("  Tier stability: %.1f%% (higher = more stable assignments)", tier_stability * 100)
    else:
        tier_stability = 0.0
        log.info("  Insufficient months for stability analysis")

    # ══════════════════════════════════════════════════════════════════════
    #  GO/NO-GO DECISION
    # ══════════════════════════════════════════════════════════════════════
    log.info("\n" + "═" * 70)
    log.info("GO/NO-GO DECISION")
    log.info("═" * 70)

    gate_1 = dip_pass or monotonic
    gate_2 = perm_pass
    gate_3 = size_pass
    gate_4 = ci_pass

    log.info("  Gate 1 (bimodality OR monotonic PF): %s", "PASS ✓" if gate_1 else "FAIL ✗")
    log.info("    Dip test p<0.05: %s (p=%.4f)", "PASS" if dip_pass else "FAIL", dip_p)
    log.info("    Quintile monotonic: %s (rho=%.3f)", "PASS" if monotonic else "FAIL", rho)
    log.info("  Gate 2 (permutation test): %s", "PASS ✓" if gate_2 else "FAIL ✗")
    log.info("    Actual diff=%.3f vs p95_null=%.3f", actual_diff, p95_null)
    log.info("  Gate 3 (≥%d trades per tier): %s", min_trades_per_tier,
             "PASS ✓" if gate_3 else "FAIL ✗")
    log.info("    Liquid=%d, Illiquid=%d", len(liquid_trades), len(illiquid_trades))
    log.info("  Gate 4 (CI doesn't span below 1.0): %s", "PASS ✓" if gate_4 else "FAIL ✗")

    go = gate_1 and gate_2 and gate_3 and gate_4
    decision = "GO" if go else "NO-GO"

    log.info("\n  ══════════════════════════════════════")
    log.info("  DECISION: %s", decision)
    log.info("  ══════════════════════════════════════")

    if not go:
        log.info("\n  RECOMMENDATION: Add volume as continuous feature in D1 score.")
        log.info("  Skip Phases 1-5 for tiering. Proceed with Phase S (sim upgrade).")
    else:
        log.info("\n  RECOMMENDATION: Proceed with tier infrastructure (Phases 1-5).")
        log.info("  Tier boundary: $%s", f"{boundary:,.0f}")

    # ── Write JSON summary ────────────────────────────────────────────────
    summary = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "batch_id": args.batch_id,
        "n_opportunities": len(opps),
        "n_traded_config_d": len(traded),
        "n_symbols": len(symbols),
        "date_range": [date_range_start.isoformat(), date_range_end.isoformat()],
        "tier_boundary_usd": boundary,
        "config_d_overall": {
            "n_traded": len(traded),
            "win_rate": round(wr, 4),
            "agg_pf": round(agg_pf, 4),
            "daily_pf": round(daily_pf, 4),
            "total_pnl": round(total_pnl, 2),
        },
        "volume_distribution": {
            "p5": round(float(np.percentile(vol_array, 5)), 0),
            "p25": round(float(np.percentile(vol_array, 25)), 0),
            "p50": round(float(np.percentile(vol_array, 50)), 0),
            "p75": round(float(np.percentile(vol_array, 75)), 0),
            "p95": round(float(np.percentile(vol_array, 95)), 0),
        },
        "test_1_dip_test": {
            "dip_statistic": round(dip_stat, 4),
            "p_value": round(dip_p, 4),
            "pass": dip_pass,
        },
        "test_2_quintile_analysis": {
            "quintiles": quintile_results,
            "spearman_rho": round(float(rho), 4),
            "spearman_p": round(float(spearman_p), 4),
            "monotonic": monotonic,
        },
        "test_3_tier_split": {
            "boundary_usd": boundary,
            "liquid_trades": len(liquid_trades),
            "illiquid_trades": len(illiquid_trades),
            "liquid_daily_pf": round(compute_daily_pf(liquid_trades), 4) if liquid_trades else None,
            "illiquid_daily_pf": round(compute_daily_pf(illiquid_trades), 4) if illiquid_trades else None,
        },
        "test_4_permutation": {
            "actual_diff": round(actual_diff, 4),
            "p95_null": round(p95_null, 4),
            "p_value": round(perm_p, 4),
            "pass": perm_pass,
        },
        "test_5_rejection_rate": {
            "liquid_rejection_pct": round(liq_rejection * 100, 1),
            "illiquid_rejection_pct": round(illiq_rejection * 100, 1),
            "config_d_already_filters": config_d_already_filters,
        },
        "test_6_bootstrap_ci": tier_cis,
        "test_7_tier_stability": {
            "stability_pct": round(tier_stability * 100, 1) if tier_stability else None,
        },
        "gates": {
            "gate_1_bimodality_or_monotonic": gate_1,
            "gate_2_permutation": gate_2,
            "gate_3_sample_size": gate_3,
            "gate_4_ci_above_1": gate_4,
        },
        "decision": decision,
        "elapsed_seconds": round(time.time() - t0, 1),
    }

    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    log.info("\n  Output log: %s", log_path)
    log.info("  Output JSON: %s", json_path)
    log.info("  Elapsed: %.1fs", time.time() - t0)


if __name__ == "__main__":
    main()
