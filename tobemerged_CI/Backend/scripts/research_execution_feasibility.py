#!/usr/bin/env python3
"""
Execution Feasibility & Portfolio Stress Test
==============================================

Workstream 1c: Re-run holdout with realistic costs (roundtrip + funding)
Workstream 3:  Portfolio stress test (correlated shorts, worst drawdowns)

Strategy parameters (LOCKED — do not modify):
  Signal:  ret_4h (4-hour return)
  Entry:   Short top decile (top 10%)
  Hold:    48 hours
  Filter:  Market breadth > 0.3
  Cost:    18 bps roundtrip base

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker python3 -u /scripts/research_execution_feasibility.py
"""

import os
import sys
import time
import math
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

import psycopg
import numpy as np

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DB_URL = os.environ.get("DATABASE_URL")


# ── Helpers ──────────────────────────────────────────────────────────────────

def _rankdata(arr):
    sorter = np.argsort(arr)
    ranks = np.empty_like(sorter, dtype=np.float64)
    ranks[sorter] = np.arange(1, len(arr) + 1, dtype=np.float64)
    sorted_arr = arr[sorter]
    i = 0
    while i < len(sorted_arr):
        j = i
        while j < len(sorted_arr) and sorted_arr[j] == sorted_arr[i]:
            j += 1
        if j > i + 1:
            avg_rank = np.mean(np.arange(i + 1, j + 1, dtype=np.float64))
            for k in range(i, j):
                ranks[sorter[k]] = avg_rank
        i = j
    return ranks


def _spearmanr(x, y):
    n = len(x)
    if n < 3:
        return np.nan, 1.0
    rx = _rankdata(x)
    ry = _rankdata(y)
    mx, my = rx.mean(), ry.mean()
    dx, dy = rx - mx, ry - my
    denom = np.sqrt((dx ** 2).sum() * (dy ** 2).sum())
    if denom == 0:
        return 0.0, 1.0
    rho = (dx * dy).sum() / denom
    if abs(rho) >= 1.0:
        return float(rho), 0.0
    t_stat = rho * np.sqrt((n - 2) / (1 - rho ** 2))
    if n > 30:
        p_val = 2 * (1 - 0.5 * (1 + math.erf(abs(t_stat) / math.sqrt(2))))
    else:
        p_val = 2 * np.exp(-0.5 * t_stat ** 2) / np.sqrt(2 * np.pi) if abs(t_stat) < 30 else 0.0
    return float(rho), float(p_val)


# ── Data loading ─────────────────────────────────────────────────────────────

def load_ohlcv_matrix(db_url, timeframe, lookback_days, min_bar_pct=0.74):
    logger.info("Loading OHLCV %s, lookback=%dd...", timeframe, lookback_days)
    t0 = time.time()
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, COUNT(*) as cnt FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s
                GROUP BY symbol ORDER BY cnt DESC
            """, (timeframe, cutoff))
            sym_counts = cur.fetchall()

    max_bars = sym_counts[0][1] if sym_counts else 0
    min_bars = int(max_bars * min_bar_pct)
    symbols = [s for s, c in sym_counts if c >= min_bars]
    logger.info("  %d symbols with >= %d bars", len(symbols), min_bars)

    tf_hours = {'1h': 1, '4h': 4, '1d': 24}
    hours = tf_hours.get(timeframe, 1)
    n_bars = int(lookback_days * 24 / hours)

    ts_end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    ts_grid = [ts_end - timedelta(hours=hours * (n_bars - 1 - i)) for i in range(n_bars)]

    sym_to_idx = {s: i for i, s in enumerate(symbols)}
    ts_to_idx = {ts: i for i, ts in enumerate(ts_grid)}
    n_sym = len(symbols)
    cube = np.full((n_bars, n_sym, 5), np.nan, dtype=np.float64)

    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE timeframe = %s AND bar_start_ts >= %s AND symbol = ANY(%s)
                ORDER BY bar_start_ts
            """, (timeframe, cutoff, symbols))
            loaded = 0
            for row in cur:
                sym, ts, o, h, l, c, v = row
                if sym not in sym_to_idx:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                ts_h = ts.replace(minute=0, second=0, microsecond=0)
                if ts_h not in ts_to_idx:
                    continue
                cube[ts_to_idx[ts_h], sym_to_idx[sym]] = [float(o), float(h), float(l), float(c), float(v)]
                loaded += 1
                if loaded % 1_000_000 == 0:
                    logger.info("    Loaded %dM rows...", loaded // 1_000_000)

    logger.info("  Loaded %d rows in %.1fs -> cube shape %s", loaded, time.time() - t0, cube.shape)
    return ts_grid, symbols, cube


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    if not DB_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    np.random.seed(42)

    logger.info("=" * 70)
    logger.info("EXECUTION FEASIBILITY & PORTFOLIO STRESS TEST")
    logger.info("=" * 70)

    # ── Load data ─────────────────────────────────────────────────────────
    ts_grid, symbols, cube = load_ohlcv_matrix(DB_URL, '1h', 180)
    n_bars, n_sym = cube.shape[0], cube.shape[1]
    C = cube[:, :, 3]

    # ── Compute features ──────────────────────────────────────────────────
    with np.errstate(divide='ignore', invalid='ignore'):
        ret_4h = np.full((n_bars, n_sym), np.nan)
        ret_4h[4:] = np.where(C[:-4] > 0, C[4:] / C[:-4] - 1, np.nan)

        fwd_48h = np.full((n_bars, n_sym), np.nan)
        fwd_48h[:-48] = np.where(C[:-48] > 0, C[48:] / C[:-48] - 1, np.nan)

        ret_24h = np.full((n_bars, n_sym), np.nan)
        ret_24h[24:] = np.where(C[:-24] > 0, C[24:] / C[:-24] - 1, np.nan)

    # Market breadth (fraction with positive 24h return)
    mkt_breadth = np.full(n_bars, np.nan)
    for t in range(24, n_bars):
        valid = ~np.isnan(ret_24h[t])
        if valid.sum() > 10:
            mkt_breadth[t] = np.nanmean(ret_24h[t] > 0)

    # Holdout split
    holdout_size = n_bars // 6
    holdout_start = n_bars - holdout_size
    train_end = holdout_start - 24

    logger.info("Holdout: [%d:%d] = %s to %s",
                holdout_start, n_bars,
                ts_grid[holdout_start].strftime('%Y-%m-%d %H:%M'),
                ts_grid[-1].strftime('%Y-%m-%d %H:%M'))

    # Train IC to confirm direction
    feat_train = ret_4h[:train_end]
    fwd_train = fwd_48h[:train_end]
    tmask = ~np.isnan(feat_train) & ~np.isnan(fwd_train)
    x = feat_train[tmask]
    y = fwd_train[tmask]
    if len(x) > 50_000:
        idx = np.random.choice(len(x), 50_000, replace=False)
        x, y = x[idx], y[idx]
    ic, _ = _spearmanr(x, y)
    short_top = (ic < 0)
    logger.info("Train IC: %.4f (short_top=%s)", ic, short_top)

    # ═══════════════════════════════════════════════════════════════════════
    # WORKSTREAM 1c: HOLDOUT WITH REALISTIC COSTS
    # ═══════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("WORKSTREAM 1c: HOLDOUT WITH REALISTIC COSTS")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Funding rate findings (from 1b):")
    logger.info("  Mean funding per 48h:        -9.6 bps (shorts RECEIVE)")
    logger.info("  Conservative (top quartile):  +5.0 bps cost")
    logger.info("  Adversarial (top decile):    ~+10.0 bps cost")
    logger.info("")

    # Cost scenarios:
    # "roundtrip" = entry slippage + exit slippage + 2x taker fee
    # "funding"   = net funding over 48h hold (6 intervals)
    cost_scenarios = [
        ("Base: 18 bps RT, no funding",      18,   0),
        ("Mean funding: 18 - 9.6 bps",       18,  -9.6),
        ("Conservative: 18 + 5 bps",         18,   5.0),
        ("Adversarial: 18 + 10 bps",         18,  10.0),
        ("1.5x slippage: 27 bps, no funding", 27,  0),
        ("1.5x slip + conservative funding",  27,   5.0),
        ("2x slippage: 36 bps, no funding",   36,   0),
        ("2x slip + adversarial funding",     36,  10.0),
    ]

    rebalance_h = 48

    logger.info("%-45s %6s %6s %8s %8s %8s", "Scenario", "Trades", "WR%", "AggPF", "AvgBps", "TotalBps")
    logger.info("-" * 90)

    scenario_results = {}

    for scenario_name, rt_bps, funding_bps in cost_scenarios:
        total_cost = (rt_bps + funding_bps) / 10000
        trades = []

        for t in range(holdout_start, n_bars - 48, rebalance_h):
            # Breadth > 0.3 filter
            if np.isnan(mkt_breadth[t]) or mkt_breadth[t] <= 0.3:
                continue

            feat_row = ret_4h[t]
            fwd_row = fwd_48h[t]
            valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
            n_valid = int(valid.sum())
            if n_valid < 20:
                continue

            ranks = np.full(n_sym, np.nan)
            ranks[valid] = _rankdata(feat_row[valid]) / n_valid

            if short_top:
                short_mask = valid & (ranks >= 0.9)
            else:
                short_mask = valid & (ranks <= 0.1)

            ts_bar = ts_grid[t]
            for si in np.where(short_mask)[0]:
                trade_ret = -fwd_row[si] - total_cost
                trades.append({
                    'ts': ts_bar,
                    'bar_idx': t,
                    'symbol_idx': si,
                    'symbol': symbols[si],
                    'return': float(trade_ret),
                    'raw_return': float(-fwd_row[si]),
                })

        if not trades:
            logger.info("%-45s   No trades", scenario_name)
            continue

        rets = np.array([t['return'] for t in trades])
        wins = rets[rets > 0]
        losses = rets[rets < 0]
        pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float('inf')
        wr = len(wins) / len(rets)
        total_bps = rets.sum() * 10000

        logger.info("%-45s %6d %6.1f %8.3f %8.1f %8.0f",
                     scenario_name, len(rets), wr * 100, pf,
                     rets.mean() * 10000, total_bps)

        scenario_results[scenario_name] = {
            'trades': trades, 'n_trades': len(rets),
            'pf': pf, 'wr': wr, 'avg_bps': rets.mean() * 10000,
        }

    # ── Detailed analysis of base scenario ────────────────────────────────
    base_key = "Base: 18 bps RT, no funding"
    if base_key in scenario_results:
        trades = scenario_results[base_key]['trades']
        logger.info("")
        logger.info("─" * 70)
        logger.info("DETAILED ANALYSIS: %s", base_key)
        logger.info("─" * 70)

        # Per-day PF (the §4.5 gate metric)
        daily_groups = defaultdict(list)
        for t in trades:
            daily_groups[t['ts'].date()].append(t['return'])

        daily_pfs = []
        daily_rets = []
        for d in sorted(daily_groups):
            day_rets = np.array(daily_groups[d])
            daily_rets.append(day_rets.mean())
            dw = day_rets[day_rets > 0].sum()
            dl = abs(day_rets[day_rets < 0].sum())
            if dl > 0:
                daily_pfs.append(dw / dl)
            elif dw > 0:
                daily_pfs.append(3.0)

        avg_daily_pf = np.mean(daily_pfs) if daily_pfs else 0
        daily_rets = np.array(daily_rets)

        # Sharpe
        if len(daily_rets) > 1 and daily_rets.std() > 0:
            sr = daily_rets.mean() / daily_rets.std() * np.sqrt(365)
        else:
            sr = 0

        logger.info("  Avg daily PF (§4.5 gate): %.3f", avg_daily_pf)
        logger.info("  Annualised Sharpe:        %.2f", sr)
        logger.info("  Trading days:             %d", len(daily_groups))
        logger.info("  Profitable days:          %d/%d (%.0f%%)",
                     sum(1 for r in daily_rets if r > 0), len(daily_rets),
                     100 * sum(1 for r in daily_rets if r > 0) / len(daily_rets))

    # ═══════════════════════════════════════════════════════════════════════
    # WORKSTREAM 3: PORTFOLIO STRESS TEST
    # ═══════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("WORKSTREAM 3: PORTFOLIO STRESS TEST")
    logger.info("=" * 70)

    # Use base scenario trades for stress test
    if base_key not in scenario_results:
        logger.error("No base scenario results for stress test")
        return

    trades = scenario_results[base_key]['trades']

    # ── 3a: Position sizing context ──────────────────────────────────────
    logger.info("")
    logger.info("3a) POSITION SIZING CONTEXT")
    logger.info("─" * 50)

    # Count positions per rebalance period
    rebalance_groups = defaultdict(list)
    for t in trades:
        rebalance_groups[t['bar_idx']].append(t)

    positions_per_period = [len(v) for v in rebalance_groups.values()]
    logger.info("  Rebalance periods:      %d", len(rebalance_groups))
    logger.info("  Positions per period:   min=%d, max=%d, mean=%.1f, median=%.0f",
                min(positions_per_period), max(positions_per_period),
                np.mean(positions_per_period), np.median(positions_per_period))

    # Per-period returns (equal weight across all shorts in that period)
    period_rets = []
    period_meta = []
    for bar_idx in sorted(rebalance_groups):
        group_trades = rebalance_groups[bar_idx]
        rets = np.array([t['return'] for t in group_trades])
        raw_rets = np.array([t['raw_return'] for t in group_trades])
        period_rets.append({
            'bar_idx': bar_idx,
            'ts': group_trades[0]['ts'],
            'n_positions': len(rets),
            'portfolio_ret': rets.mean(),       # Equal-weight portfolio return
            'raw_portfolio_ret': raw_rets.mean(),
            'worst_position': rets.min(),
            'best_position': rets.max(),
            'std_across_positions': rets.std(),
            'symbols': [t['symbol'] for t in group_trades],
        })

    # ── 3b: Worst periods ────────────────────────────────────────────────
    logger.info("")
    logger.info("3b) WORST 48-HOUR PERIODS (equal-weight portfolio)")
    logger.info("─" * 50)

    sorted_by_ret = sorted(period_rets, key=lambda x: x['portfolio_ret'])

    logger.info("  %-20s %5s %10s %10s %10s %10s",
                "Date", "N", "PortRet", "RawRet", "Worst", "Best")
    logger.info("  " + "-" * 70)

    for p in sorted_by_ret[:10]:
        logger.info("  %-20s %5d %9.1f%% %9.1f%% %9.1f%% %9.1f%%",
                     p['ts'].strftime('%Y-%m-%d %H:%M'), p['n_positions'],
                     p['portfolio_ret'] * 100, p['raw_portfolio_ret'] * 100,
                     p['worst_position'] * 100, p['best_position'] * 100)

    # ── 3c: Maximum drawdown (sequential portfolio equity curve) ─────────
    logger.info("")
    logger.info("3c) PORTFOLIO EQUITY CURVE & DRAWDOWN")
    logger.info("─" * 50)

    sorted_periods = sorted(period_rets, key=lambda x: x['bar_idx'])
    port_rets = np.array([p['portfolio_ret'] for p in sorted_periods])

    # Compound equity (start at 1.0)
    equity = np.cumprod(1 + port_rets)
    running_max = np.maximum.accumulate(equity)
    drawdowns = (equity - running_max) / running_max

    max_dd = drawdowns.min()
    max_dd_idx = np.argmin(drawdowns)
    max_dd_date = sorted_periods[max_dd_idx]['ts']

    # Find drawdown duration (from peak to trough)
    peak_idx = np.argmax(equity[:max_dd_idx + 1])
    peak_date = sorted_periods[peak_idx]['ts']

    # Recovery (if any)
    recovery_idx = None
    for i in range(max_dd_idx + 1, len(equity)):
        if equity[i] >= running_max[max_dd_idx]:
            recovery_idx = i
            break

    logger.info("  Final equity (starting 1.0): %.4f", equity[-1])
    logger.info("  Total return: %.2f%%", (equity[-1] - 1) * 100)
    logger.info("  Max drawdown: %.2f%%", max_dd * 100)
    logger.info("  DD peak:      %s (equity=%.4f)",
                peak_date.strftime('%Y-%m-%d'), equity[peak_idx])
    logger.info("  DD trough:    %s (equity=%.4f)",
                max_dd_date.strftime('%Y-%m-%d'), equity[max_dd_idx])
    if recovery_idx is not None:
        logger.info("  DD recovery:  %s (%d periods = %d days)",
                     sorted_periods[recovery_idx]['ts'].strftime('%Y-%m-%d'),
                     recovery_idx - peak_idx,
                     (recovery_idx - peak_idx) * 2)  # 48h periods
    else:
        logger.info("  DD recovery:  Not yet recovered")

    # ── 3d: Correlation risk (cross-position correlation) ────────────────
    logger.info("")
    logger.info("3d) CORRELATION RISK")
    logger.info("─" * 50)

    # For each rebalance period, compute the dispersion of returns across positions
    # High dispersion = low correlation = diversification benefit
    # Low dispersion  = high correlation = concentrated risk
    dispersions = [p['std_across_positions'] for p in sorted_periods if p['n_positions'] > 5]
    logger.info("  Cross-position return dispersion:")
    logger.info("    Mean:   %.2f%%", np.mean(dispersions) * 100)
    logger.info("    Median: %.2f%%", np.median(dispersions) * 100)
    logger.info("    Min:    %.2f%% (highest correlation)", np.min(dispersions) * 100)
    logger.info("    Max:    %.2f%% (most diversified)", np.max(dispersions) * 100)

    # Fraction of positions that move in same direction
    same_dir_fracs = []
    for p in sorted_periods:
        if p['n_positions'] < 5:
            continue
        pos_rets = np.array([t['return'] for t in rebalance_groups[p['bar_idx']]])
        frac_neg = (pos_rets < 0).mean()  # Fraction losing (short squeeze)
        same_dir_fracs.append(max(frac_neg, 1 - frac_neg))  # Majority direction

    logger.info("  Fraction of positions moving together (majority direction):")
    logger.info("    Mean:   %.1f%%", np.mean(same_dir_fracs) * 100)
    logger.info("    P90:    %.1f%% (worst 10%% of periods)", np.percentile(same_dir_fracs, 90) * 100)
    logger.info("    Max:    %.1f%% (most correlated period)", np.max(same_dir_fracs) * 100)

    # ── 3e: Worst individual position ────────────────────────────────────
    logger.info("")
    logger.info("3e) WORST INDIVIDUAL POSITIONS")
    logger.info("─" * 50)

    all_trades_sorted = sorted(trades, key=lambda x: x['return'])
    logger.info("  %-12s %-20s %10s %10s",
                "Symbol", "Date", "Return", "RawReturn")
    logger.info("  " + "-" * 55)
    for t in all_trades_sorted[:15]:
        logger.info("  %-12s %-20s %9.2f%% %9.2f%%",
                     t['symbol'], t['ts'].strftime('%Y-%m-%d %H:%M'),
                     t['return'] * 100, t['raw_return'] * 100)

    # ── 3f: BTC crash overlay ────────────────────────────────────────────
    logger.info("")
    logger.info("3f) BTC CRASH OVERLAY")
    logger.info("─" * 50)

    btc_idx = None
    for i, s in enumerate(symbols):
        if s == 'BTCUSDT':
            btc_idx = i
            break

    if btc_idx is not None:
        for p in sorted_by_ret[:5]:
            t = p['bar_idx']
            if t + 48 < n_bars:
                btc_ret = C[t + 48, btc_idx] / C[t, btc_idx] - 1 if C[t, btc_idx] > 0 else np.nan
                logger.info("  %s: portfolio=%.2f%%, BTC 48h=%.2f%%, n=%d",
                             p['ts'].strftime('%Y-%m-%d %H:%M'),
                             p['portfolio_ret'] * 100,
                             btc_ret * 100 if not np.isnan(btc_ret) else 0,
                             p['n_positions'])

    # ── 3g: Concentration risk (symbol frequency) ────────────────────────
    logger.info("")
    logger.info("3g) SYMBOL CONCENTRATION")
    logger.info("─" * 50)

    sym_count = defaultdict(int)
    sym_total_ret = defaultdict(float)
    for t in trades:
        sym_count[t['symbol']] += 1
        sym_total_ret[t['symbol']] += t['return']

    logger.info("  Unique symbols shorted: %d", len(sym_count))
    logger.info("  Top 10 most-shorted symbols:")
    for sym, cnt in sorted(sym_count.items(), key=lambda x: -x[1])[:10]:
        avg_ret = sym_total_ret[sym] / cnt
        logger.info("    %-12s %3d trades  avg=%.1f bps", sym, cnt, avg_ret * 10000)

    # ── 3h: Tail risk metrics ────────────────────────────────────────────
    logger.info("")
    logger.info("3h) TAIL RISK METRICS")
    logger.info("─" * 50)

    all_rets = np.array([t['return'] for t in trades])
    logger.info("  Portfolio-level (per-period equal-weight):")
    logger.info("    VaR  5%%: %.2f%%", np.percentile(port_rets, 5) * 100)
    logger.info("    VaR  1%%: %.2f%%", np.percentile(port_rets, 1) * 100)
    logger.info("    CVaR 5%%: %.2f%%", port_rets[port_rets <= np.percentile(port_rets, 5)].mean() * 100)

    logger.info("  Per-trade level:")
    logger.info("    VaR  5%%: %.2f%%", np.percentile(all_rets, 5) * 100)
    logger.info("    VaR  1%%: %.2f%%", np.percentile(all_rets, 1) * 100)
    logger.info("    CVaR 5%%: %.2f%%", all_rets[all_rets <= np.percentile(all_rets, 5)].mean() * 100)
    logger.info("    Max loss: %.2f%%", all_rets.min() * 100)

    # ── 3i: Capital efficiency ───────────────────────────────────────────
    logger.info("")
    logger.info("3i) CAPITAL EFFICIENCY")
    logger.info("─" * 50)

    # With N positions of equal weight, each position = 1/N of capital
    # If using 2x leverage on perps, each position is 1/(N/2) margin
    avg_n = np.mean(positions_per_period)
    logger.info("  Average positions per rebalance: %.0f", avg_n)
    logger.info("")
    logger.info("  If $100K portfolio, equal-weight:")
    per_pos_100k = 100_000 / avg_n
    logger.info("    Per-position size: $%.0f", per_pos_100k)
    logger.info("    With 2x leverage: $%.0f margin per position", per_pos_100k / 2)
    logger.info("")

    # Annual return estimate
    holdout_days = (ts_grid[-1] - ts_grid[holdout_start]).days
    total_return_pct = (equity[-1] - 1) * 100
    annualised = ((equity[-1]) ** (365 / max(holdout_days, 1)) - 1) * 100

    logger.info("  Holdout period: %d days", holdout_days)
    logger.info("  Total return: %.2f%%", total_return_pct)
    logger.info("  Annualised return: %.1f%%", annualised)
    logger.info("  Max drawdown: %.2f%%", max_dd * 100)
    logger.info("  Return/MaxDD ratio: %.2f", abs(total_return_pct / (max_dd * 100)) if max_dd != 0 else float('inf'))

    # ═══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════════════════
    logger.info("")
    logger.info("=" * 70)
    logger.info("SUMMARY")
    logger.info("=" * 70)
    logger.info("")

    base = scenario_results.get(base_key, {})
    conservative = scenario_results.get("Conservative: 18 + 5 bps", {})
    adversarial = scenario_results.get("Adversarial: 18 + 10 bps", {})
    worst = scenario_results.get("2x slip + adversarial funding", {})

    logger.info("  COST SENSITIVITY:")
    logger.info("    Base (18 bps):                PF=%.3f", base.get('pf', 0))
    logger.info("    Conservative (23 bps):        PF=%.3f", conservative.get('pf', 0))
    logger.info("    Adversarial (28 bps):         PF=%.3f", adversarial.get('pf', 0))
    logger.info("    Worst case (46 bps):          PF=%.3f", worst.get('pf', 0))
    logger.info("")
    logger.info("  STRESS TEST:")
    logger.info("    Max portfolio drawdown:       %.2f%%", max_dd * 100)
    logger.info("    Worst single period:          %.2f%%", sorted_by_ret[0]['portfolio_ret'] * 100)
    logger.info("    Portfolio VaR 5%%:             %.2f%%", np.percentile(port_rets, 5) * 100)
    logger.info("")

    if base.get('pf', 0) > 1.5:
        logger.info("  VERDICT: PF > 1.5 at base cost. Strategy is EXECUTABLE.")
    elif base.get('pf', 0) > 1.0:
        logger.info("  VERDICT: PF > 1.0 but < 1.5 at base cost. Strategy is MARGINAL.")
    else:
        logger.info("  VERDICT: PF < 1.0. Strategy does NOT survive realistic costs.")

    logger.info("")
    logger.info("COMPLETE")


if __name__ == "__main__":
    main()
