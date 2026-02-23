#!/usr/bin/env python3
"""
Regime Filter Analysis for Short-Only Mean Reversion
=====================================================

The short-only mean-reversion strategy (short top-decile by ret_4h, hold 48h)
has PF=2.33 on holdout but failed in fold 4 (Dec 22 - Jan 21).

This script investigates:
1. What market conditions cause fold-4 type failures?
2. Can we build a regime filter to avoid those periods?
3. Does filtered walk-forward achieve 4/5 profitable folds?

Candidate regime signals:
- BTC trend (is BTC in a downtrend? Reversion fails in sustained drops)
- Cross-sectional dispersion (high dispersion = trending regime)
- Market-wide momentum (are most coins moving in the same direction?)
- Volatility regime (very high vol = cascade regime, reversion fails)

Usage:
    cd /srv/mcas/Backend
    docker compose -f docker-compose.yml run --rm -T worker python3 -u /scripts/research_regime_filter.py
"""

import os
import sys
import time
import math
import logging
import argparse
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


# ── Minimal helpers (same as other scripts) ─────────────────────────────────

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


def _rolling_mean(arr, window):
    if arr.ndim == 1:
        arr = arr[:, np.newaxis]
        squeeze = True
    else:
        squeeze = False
    out = np.full_like(arr, np.nan)
    if arr.shape[0] < window:
        return out[:, 0] if squeeze else out
    cs = np.nancumsum(arr, axis=0)
    cnt = np.nancumsum(~np.isnan(arr), axis=0).astype(np.float64)
    out[window - 1:] = cs[window - 1:]
    out[window - 1:] -= np.vstack([np.zeros((1, arr.shape[1])), cs[:-window]])
    c = cnt[window - 1:]
    c -= np.vstack([np.zeros((1, arr.shape[1])), cnt[:-window]])
    with np.errstate(divide='ignore', invalid='ignore'):
        out[window - 1:] = np.where(c > 0, out[window - 1:] / c, np.nan)
    return out[:, 0] if squeeze else out


def _rolling_std(arr, window):
    mean = _rolling_mean(arr, window)
    sq_mean = _rolling_mean(arr ** 2, window)
    with np.errstate(invalid='ignore'):
        var = sq_mean - mean ** 2
        var = np.where(var > 0, var, 0)
        return np.sqrt(var)


# ── Data loading ────────────────────────────────────────────────────────────

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

    logger.info("  Loaded %d rows in %.1fs → cube shape %s", loaded, time.time() - t0, cube.shape)
    return ts_grid, symbols, cube


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=180)
    parser.add_argument("--timeframe", type=str, default="1h")
    args = parser.parse_args()

    if not DB_URL:
        logger.error("DATABASE_URL not set")
        sys.exit(1)

    np.random.seed(42)

    logger.info("=" * 70)
    logger.info("REGIME FILTER ANALYSIS")
    logger.info("=" * 70)

    ts_grid, symbols, cube = load_ohlcv_matrix(DB_URL, args.timeframe, args.lookback_days)
    n_bars, n_sym = cube.shape[0], cube.shape[1]
    C = cube[:, :, 3]  # Close prices
    V = cube[:, :, 4]  # Volume

    # ── Compute base strategy returns (ret_4h, top 10%, short, 48h hold) ──
    with np.errstate(divide='ignore', invalid='ignore'):
        ret_4h = np.full((n_bars, n_sym), np.nan)
        ret_4h[4:] = np.where(C[:-4] > 0, C[4:] / C[:-4] - 1, np.nan)

        fwd_48h = np.full((n_bars, n_sym), np.nan)
        fwd_48h[:-48] = np.where(C[:-48] > 0, C[48:] / C[:-48] - 1, np.nan)

        ret_1h = np.full((n_bars, n_sym), np.nan)
        ret_1h[1:] = np.where(C[:-1] > 0, C[1:] / C[:-1] - 1, np.nan)

    cost = 18 / 10000

    # Simulate short-only strategy bar-by-bar (every 48 bars)
    logger.info("")
    logger.info("Computing bar-by-bar short-only returns...")

    bar_returns = []  # (bar_idx, ts, avg_trade_return, n_trades, regime_features)

    # Find BTC index
    btc_idx = None
    for i, s in enumerate(symbols):
        if s == 'BTCUSDT':
            btc_idx = i
            break

    # Compute regime indicators
    logger.info("Computing regime indicators...")

    # 1) BTC 24h return
    btc_ret_24h = np.full(n_bars, np.nan)
    if btc_idx is not None:
        btc_close = C[:, btc_idx]
        for t in range(24, n_bars):
            if btc_close[t - 24] > 0 and not np.isnan(btc_close[t]) and not np.isnan(btc_close[t - 24]):
                btc_ret_24h[t] = btc_close[t] / btc_close[t - 24] - 1

    # 2) BTC 72h return
    btc_ret_72h = np.full(n_bars, np.nan)
    if btc_idx is not None:
        for t in range(72, n_bars):
            if btc_close[t - 72] > 0 and not np.isnan(btc_close[t]) and not np.isnan(btc_close[t - 72]):
                btc_ret_72h[t] = btc_close[t] / btc_close[t - 72] - 1

    # 3) Cross-sectional return dispersion (std of 24h returns across symbols)
    xs_disp_24h = np.full(n_bars, np.nan)
    with np.errstate(divide='ignore', invalid='ignore'):
        ret_24h_mat = np.full((n_bars, n_sym), np.nan)
        ret_24h_mat[24:] = np.where(C[:-24] > 0, C[24:] / C[:-24] - 1, np.nan)
    for t in range(24, n_bars):
        valid = ~np.isnan(ret_24h_mat[t])
        if valid.sum() > 10:
            xs_disp_24h[t] = np.nanstd(ret_24h_mat[t])

    # 4) Market-wide momentum (fraction of symbols with positive 24h return)
    mkt_breadth = np.full(n_bars, np.nan)
    for t in range(24, n_bars):
        valid = ~np.isnan(ret_24h_mat[t])
        if valid.sum() > 10:
            mkt_breadth[t] = np.nanmean(ret_24h_mat[t] > 0)

    # 5) Rolling BTC volatility (24h)
    btc_vol_24h = np.full(n_bars, np.nan)
    if btc_idx is not None:
        btc_ret1h = np.full(n_bars, np.nan)
        btc_ret1h[1:] = np.where(btc_close[:-1] > 0, btc_close[1:] / btc_close[:-1] - 1, np.nan)
        for t in range(24, n_bars):
            chunk = btc_ret1h[t - 24:t]
            v = chunk[~np.isnan(chunk)]
            if len(v) > 5:
                btc_vol_24h[t] = np.std(v)

    # 6) Average cross-sectional 4h return magnitude (are things moving a lot?)
    avg_abs_ret_4h = np.full(n_bars, np.nan)
    for t in range(4, n_bars):
        valid = ~np.isnan(ret_4h[t])
        if valid.sum() > 10:
            avg_abs_ret_4h[t] = np.nanmean(np.abs(ret_4h[t]))

    logger.info("  Regime indicators computed")

    # ── Simulate strategy with regime info ────────────────────────────────
    rebalance_h = 48
    for t in range(72, n_bars - 48, rebalance_h):
        feat_row = ret_4h[t]
        fwd_row = fwd_48h[t]
        valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
        n_valid = int(valid.sum())
        if n_valid < 20:
            continue

        ranks = np.full(n_sym, np.nan)
        ranks[valid] = _rankdata(feat_row[valid]) / n_valid

        # Short top decile (highest 4h returners)
        short_mask = valid & (ranks >= 0.9)
        n_shorts = int(short_mask.sum())
        if n_shorts == 0:
            continue

        trade_rets = -fwd_row[short_mask] - cost
        avg_ret = trade_rets.mean()

        bar_returns.append({
            'bar_idx': t,
            'ts': ts_grid[t],
            'avg_ret': float(avg_ret),
            'n_trades': n_shorts,
            'total_ret': float(trade_rets.sum()),
            'btc_ret_24h': float(btc_ret_24h[t]) if not np.isnan(btc_ret_24h[t]) else None,
            'btc_ret_72h': float(btc_ret_72h[t]) if not np.isnan(btc_ret_72h[t]) else None,
            'xs_disp_24h': float(xs_disp_24h[t]) if not np.isnan(xs_disp_24h[t]) else None,
            'mkt_breadth': float(mkt_breadth[t]) if not np.isnan(mkt_breadth[t]) else None,
            'btc_vol_24h': float(btc_vol_24h[t]) if not np.isnan(btc_vol_24h[t]) else None,
            'avg_abs_ret_4h': float(avg_abs_ret_4h[t]) if not np.isnan(avg_abs_ret_4h[t]) else None,
        })

    logger.info("  %d rebalance periods simulated", len(bar_returns))

    # ── Analyze: correlation between regime indicators and strategy returns ──
    logger.info("")
    logger.info("=" * 70)
    logger.info("REGIME INDICATOR CORRELATION WITH STRATEGY RETURNS")
    logger.info("=" * 70)

    strat_rets = np.array([b['avg_ret'] for b in bar_returns])
    regime_names = ['btc_ret_24h', 'btc_ret_72h', 'xs_disp_24h',
                    'mkt_breadth', 'btc_vol_24h', 'avg_abs_ret_4h']

    for rname in regime_names:
        vals = np.array([b[rname] if b[rname] is not None else np.nan for b in bar_returns])
        mask = ~np.isnan(vals) & ~np.isnan(strat_rets)
        if mask.sum() < 20:
            continue
        ic, pval = _spearmanr(vals[mask], strat_rets[mask])
        logger.info("  %-20s IC=%.4f  p=%.4f  (n=%d)", rname, ic, pval, mask.sum())

    # ── Quintile analysis of strategy returns by regime ────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("STRATEGY RETURNS BY REGIME QUINTILE")
    logger.info("=" * 70)

    for rname in regime_names:
        vals = np.array([b[rname] if b[rname] is not None else np.nan for b in bar_returns])
        mask = ~np.isnan(vals) & ~np.isnan(strat_rets)
        if mask.sum() < 20:
            continue

        v_clean = vals[mask]
        r_clean = strat_rets[mask]

        quintile_edges = np.nanpercentile(v_clean, [20, 40, 60, 80])

        q_labels = ['Q1 (lowest)', 'Q2', 'Q3', 'Q4', 'Q5 (highest)']
        logger.info("")
        logger.info("  %s:", rname)
        for qi in range(5):
            if qi == 0:
                qmask = v_clean <= quintile_edges[0]
            elif qi == 4:
                qmask = v_clean >= quintile_edges[3]
            else:
                qmask = (v_clean > quintile_edges[qi - 1]) & (v_clean <= quintile_edges[qi])

            if qmask.sum() == 0:
                continue

            q_rets = r_clean[qmask]
            q_wr = (q_rets > 0).mean()
            q_mean = q_rets.mean()
            q_wins = q_rets[q_rets > 0].sum() if (q_rets > 0).any() else 0
            q_losses = abs(q_rets[q_rets < 0].sum()) if (q_rets < 0).any() else 0
            q_pf = q_wins / q_losses if q_losses > 0 else float('inf')

            edge_val = v_clean[qmask].mean()
            logger.info("    %s (avg=%.4f): n=%3d, WR=%.1f%%, PF=%.3f, avg=%.1f bps",
                         q_labels[qi], edge_val, qmask.sum(), q_wr * 100,
                         min(q_pf, 99.999), q_mean * 10000)

    # ── Test specific regime filters ───────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("REGIME FILTER CANDIDATES")
    logger.info("=" * 70)

    filters = [
        ("BTC 72h ret > -5%", lambda b: b['btc_ret_72h'] is not None and b['btc_ret_72h'] > -0.05),
        ("BTC 72h ret > -3%", lambda b: b['btc_ret_72h'] is not None and b['btc_ret_72h'] > -0.03),
        ("BTC 24h ret > -3%", lambda b: b['btc_ret_24h'] is not None and b['btc_ret_24h'] > -0.03),
        ("BTC 24h ret > -2%", lambda b: b['btc_ret_24h'] is not None and b['btc_ret_24h'] > -0.02),
        ("Market breadth > 0.3", lambda b: b['mkt_breadth'] is not None and b['mkt_breadth'] > 0.3),
        ("Market breadth > 0.35", lambda b: b['mkt_breadth'] is not None and b['mkt_breadth'] > 0.35),
        ("XS dispersion < 0.10", lambda b: b['xs_disp_24h'] is not None and b['xs_disp_24h'] < 0.10),
        ("XS dispersion < 0.08", lambda b: b['xs_disp_24h'] is not None and b['xs_disp_24h'] < 0.08),
        ("BTC vol 24h < 0.015", lambda b: b['btc_vol_24h'] is not None and b['btc_vol_24h'] < 0.015),
        ("BTC vol 24h < 0.010", lambda b: b['btc_vol_24h'] is not None and b['btc_vol_24h'] < 0.010),
        # Combinations
        ("BTC 72h > -5% AND breadth > 0.3",
         lambda b: (b['btc_ret_72h'] is not None and b['btc_ret_72h'] > -0.05) and
                   (b['mkt_breadth'] is not None and b['mkt_breadth'] > 0.3)),
        ("BTC 72h > -3% AND vol < 0.015",
         lambda b: (b['btc_ret_72h'] is not None and b['btc_ret_72h'] > -0.03) and
                   (b['btc_vol_24h'] is not None and b['btc_vol_24h'] < 0.015)),
    ]

    # Unfiltered baseline
    all_rets = strat_rets
    all_wins = all_rets[all_rets > 0]
    all_losses = all_rets[all_rets < 0]
    base_pf = all_wins.sum() / abs(all_losses.sum()) if all_losses.sum() != 0 else float('inf')
    base_wr = (all_rets > 0).mean()

    logger.info("")
    logger.info("  %-40s n=%4d  WR=%.1f%%  PF=%.3f  avg=%.1f bps",
                "UNFILTERED BASELINE", len(all_rets), base_wr * 100,
                base_pf, all_rets.mean() * 10000)
    logger.info("")

    for fname, ffunc in filters:
        filtered = [b for b in bar_returns if ffunc(b)]
        if len(filtered) < 10:
            logger.info("  %-40s n=%4d  (too few)", fname, len(filtered))
            continue

        f_rets = np.array([b['avg_ret'] for b in filtered])
        f_wins = f_rets[f_rets > 0]
        f_losses = f_rets[f_rets < 0]
        f_pf = f_wins.sum() / abs(f_losses.sum()) if f_losses.sum() != 0 else float('inf')
        f_wr = (f_rets > 0).mean()
        pct_kept = len(filtered) / len(bar_returns) * 100

        logger.info("  %-40s n=%4d (%4.0f%%)  WR=%.1f%%  PF=%.3f  avg=%.1f bps",
                     fname, len(filtered), pct_kept, f_wr * 100,
                     min(f_pf, 99.999), f_rets.mean() * 10000)

    # ── Walk-forward with best regime filter ──────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("WALK-FORWARD WITH REGIME FILTERS")
    logger.info("=" * 70)

    # Test the most promising filters in walk-forward
    holdout_size = n_bars // 6
    holdout_start = n_bars - holdout_size
    train_test_bars = n_bars - holdout_size

    best_filters = [
        ("No filter", lambda t: True),
        ("BTC 72h > -5%", lambda t: not np.isnan(btc_ret_72h[t]) and btc_ret_72h[t] > -0.05),
        ("BTC 72h > -3%", lambda t: not np.isnan(btc_ret_72h[t]) and btc_ret_72h[t] > -0.03),
        ("Breadth > 0.3", lambda t: not np.isnan(mkt_breadth[t]) and mkt_breadth[t] > 0.3),
        ("Breadth > 0.35", lambda t: not np.isnan(mkt_breadth[t]) and mkt_breadth[t] > 0.35),
        ("BTC 72h > -5% + Breadth > 0.3",
         lambda t: (not np.isnan(btc_ret_72h[t]) and btc_ret_72h[t] > -0.05) and
                   (not np.isnan(mkt_breadth[t]) and mkt_breadth[t] > 0.3)),
    ]

    for filter_name, filter_func in best_filters:
        logger.info("")
        logger.info("  Filter: %s", filter_name)

        n_folds = 5
        fold_results = []

        for fold_idx in range(n_folds):
            test_start = (fold_idx + 1) * (train_test_bars // n_folds)
            test_end = min(test_start + (train_test_bars // n_folds), train_test_bars)
            train_end = test_start - 24

            if train_end < 200 or test_end <= test_start:
                continue

            # Compute IC on training to confirm direction
            train_mask = np.zeros(n_bars, dtype=bool)
            train_mask[:train_end] = True
            feat_train = ret_4h[:train_end]
            fwd_train = fwd_48h[:train_end]
            tmask = ~np.isnan(feat_train) & ~np.isnan(fwd_train)
            n_valid = int(tmask.sum())
            if n_valid < 500:
                continue

            x = feat_train[tmask]
            y = fwd_train[tmask]
            if n_valid > 50_000:
                idx = np.random.choice(n_valid, 50_000, replace=False)
                x, y = x[idx], y[idx]
            ic, _ = _spearmanr(x, y)
            short_top = (ic < 0)

            # Simulate on test with filter
            trades = []
            for t in range(test_start, test_end - 48, rebalance_h):
                if not filter_func(t):
                    continue

                feat_row = ret_4h[t]
                fwd_row = fwd_48h[t]
                valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
                n_valid_r = int(valid.sum())
                if n_valid_r < 20:
                    continue

                ranks = np.full(n_sym, np.nan)
                ranks[valid] = _rankdata(feat_row[valid]) / n_valid_r

                if short_top:
                    short_mask = valid & (ranks >= 0.9)
                else:
                    short_mask = valid & (ranks <= 0.1)

                for si in np.where(short_mask)[0]:
                    trades.append(-fwd_row[si] - cost)

            if not trades:
                fold_results.append({'fold': fold_idx + 1, 'n_trades': 0, 'agg_pf': 0})
                continue

            t_arr = np.array(trades)
            wins = t_arr[t_arr > 0]
            losses = t_arr[t_arr < 0]
            pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float('inf')
            wr = len(wins) / len(t_arr)

            fold_results.append({
                'fold': fold_idx + 1,
                'n_trades': len(t_arr),
                'wr': wr,
                'agg_pf': pf,
                'avg_ret': t_arr.mean() * 10000,
            })

        # Report
        trading_folds = [f for f in fold_results if f.get('n_trades', 0) > 0]
        if not trading_folds:
            logger.info("    No trades in any fold")
            continue

        for f in fold_results:
            if f.get('n_trades', 0) > 0:
                logger.info("    Fold %d: %4d trades, WR=%.1f%%, PF=%.3f, avg=%.1f bps",
                             f['fold'], f['n_trades'], f.get('wr', 0) * 100,
                             f['agg_pf'], f.get('avg_ret', 0))
            else:
                logger.info("    Fold %d: no trades", f['fold'])

        profitable = sum(1 for f in trading_folds if f['agg_pf'] > 1.0)
        avg_pf = np.mean([f['agg_pf'] for f in trading_folds])
        total = sum(f['n_trades'] for f in trading_folds)
        logger.info("    ─── %d/%d profitable, avg PF=%.3f, total trades=%d",
                     profitable, len(trading_folds), avg_pf, total)
        if profitable >= 4:
            logger.info("    *** PASSES §4.1 REQUIREMENT (>=80%% folds profitable) ***")

    # ── Holdout with best filter ──────────────────────────────────────────
    logger.info("")
    logger.info("=" * 70)
    logger.info("HOLDOUT WITH REGIME FILTERS")
    logger.info("=" * 70)

    for filter_name, filter_func in best_filters:
        # Train on full pre-holdout
        feat_train = ret_4h[:holdout_start - 24]
        fwd_train = fwd_48h[:holdout_start - 24]
        tmask = ~np.isnan(feat_train) & ~np.isnan(fwd_train)
        x = feat_train[tmask]
        y = fwd_train[tmask]
        if len(x) > 50_000:
            idx = np.random.choice(len(x), 50_000, replace=False)
            x, y = x[idx], y[idx]
        ic, _ = _spearmanr(x, y)
        short_top = (ic < 0)

        trades = []
        for t in range(holdout_start, n_bars - 48, rebalance_h):
            if not filter_func(t):
                continue

            feat_row = ret_4h[t]
            fwd_row = fwd_48h[t]
            valid = ~np.isnan(feat_row) & ~np.isnan(fwd_row)
            n_valid_r = int(valid.sum())
            if n_valid_r < 20:
                continue

            ranks = np.full(n_sym, np.nan)
            ranks[valid] = _rankdata(feat_row[valid]) / n_valid_r

            if short_top:
                short_mask = valid & (ranks >= 0.9)
            else:
                short_mask = valid & (ranks <= 0.1)

            for si in np.where(short_mask)[0]:
                trades.append(-fwd_row[si] - cost)

        if not trades:
            logger.info("  %-40s No holdout trades", filter_name)
            continue

        t_arr = np.array(trades)
        wins = t_arr[t_arr > 0]
        losses = t_arr[t_arr < 0]
        pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float('inf')
        wr = len(wins) / len(t_arr)

        logger.info("  %-40s n=%4d  WR=%.1f%%  PF=%.3f  avg=%.1f bps",
                     filter_name, len(t_arr), wr * 100,
                     min(pf, 99.999), t_arr.mean() * 10000)

    logger.info("")
    logger.info("COMPLETE")


if __name__ == "__main__":
    main()
