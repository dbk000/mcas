#!/usr/bin/env python3
"""
export_model.py — Train the ridge model on production data and export frozen config.

Run this once to generate model_config.json, then the runner loads it.

Usage:
  cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -T \
      -v "$(pwd)/scripts:/backend/scripts" -v "$(pwd)/trading:/backend/trading" \
      computers python3 -m trading.export_model
"""
from __future__ import annotations
import os, sys, time, json
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import numpy as np

if os.path.isdir("/backend"):
    sys.path.insert(0, "/backend")
else:
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

from trading.ridge_model import (
    FEATURE_NAMES, compute_features_for_bar, ModelConfig, save_config,
)
import psycopg

TIMEFRAME = "1h"
EVAL_START = datetime(2026, 1, 1, tzinfo=timezone.utc)
EVAL_END = datetime(2026, 4, 10, tzinfo=timezone.utc)
WARMUP_BARS = 210
FETCH_START = EVAL_START - timedelta(hours=WARMUP_BARS)
SLIPPAGE_BPS = 3.0
ROUND_TRIP_COST = 2 * SLIPPAGE_BPS / 10000
BEST_STOP = 1.5
BEST_TS = 8
BEST_ALPHA = 100.0
SCORE_PCT = 80  # p80 threshold

WHITELIST_PATH = None
for p in ["/backend/scripts/output/bybit_eu_spot_whitelist.txt",
          "Backend/scripts/output/bybit_eu_spot_whitelist.txt"]:
    if os.path.isfile(p):
        WHITELIST_PATH = p
        break


def main():
    print("[export] training ridge model on full dataset...", flush=True)
    t0 = time.time()

    with open(WHITELIST_PATH) as f:
        symbols = sorted([l.strip() for l in f if l.strip()])
    print(f"[export] {len(symbols)} symbols", flush=True)

    db_url = os.getenv("DATABASE_URL", "")
    with psycopg.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ON (symbol, bar_start_ts)
                       symbol, bar_start_ts, open, high, low, close, volume
                FROM ohlcv WHERE symbol = ANY(%s) AND timeframe = %s
                  AND bar_start_ts >= %s AND bar_start_ts < %s AND is_final = TRUE
                ORDER BY symbol, bar_start_ts,
                         CASE source WHEN 'ws' THEN 0 WHEN 'binance' THEN 1 ELSE 2 END
            """, (symbols, TIMEFRAME, FETCH_START, EVAL_END + timedelta(hours=BEST_TS+2)))
            rows = cur.fetchall()

    by_sym = defaultdict(list)
    for r in rows:
        by_sym[r[0]].append(r)
    all_data = {}
    for sym, sr in by_sym.items():
        all_data[sym] = {
            "open": np.array([float(r[2]) for r in sr]),
            "high": np.array([float(r[3]) for r in sr]),
            "low": np.array([float(r[4]) for r in sr]),
            "close": np.array([float(r[5]) for r in sr]),
            "volume": np.array([float(r[6]) for r in sr]),
            "ts": [r[1] for r in sr],
            "hours": np.array([r[1].hour if hasattr(r[1], 'hour') else 0
                               for r in sr], dtype=np.float64),
        }
    print(f"[export] {len(all_data)} symbols loaded", flush=True)

    # Build feature matrix + PnL
    eval_start_ep = EVAL_START.timestamp()
    eval_end_ep = EVAL_END.timestamp()
    all_features = {f: [] for f in FEATURE_NAMES}
    all_pnl = []
    all_ret24 = []
    all_epochs = []

    for sym in sorted(all_data.keys()):
        bars = all_data[sym]
        n_bars = len(bars["close"])
        ts_ep = np.array([t.timestamp() for t in bars["ts"]])
        feats = compute_features_for_bar(
            bars["close"], bars["high"], bars["low"], bars["volume"], bars["hours"])

        mask = ((ts_ep >= eval_start_ep) & (ts_ep < eval_end_ep)
                & ~np.isnan(feats["rsi_14"]) & ~np.isnan(feats["atr_raw"])
                & (feats["atr_raw"] > 0))
        mask[n_bars - (BEST_TS + 2):] = False
        ei = np.where(mask)[0]
        if len(ei) == 0:
            continue

        eb = ei + 1
        valid = eb < n_bars
        ei = ei[valid]
        eb = eb[valid]
        ep = bars["open"][eb] * (1 + SLIPPAGE_BPS / 10000)
        atrs = feats["atr_raw"][ei]
        sp = ep - BEST_STOP * atrs

        pnl = np.full(len(ei), np.nan)
        for j in range(len(ei)):
            ex = None
            for bi in range(1, BEST_TS + 1):
                fi = eb[j] + bi
                if fi >= n_bars:
                    ex = bars["close"][min(fi - 1, n_bars - 1)]
                    break
                if bars["low"][fi] <= sp[j]:
                    ex = sp[j]
                    break
            if ex is None:
                ex = bars["close"][min(eb[j] + BEST_TS, n_bars - 1)]
            pnl[j] = (ex / ep[j] - 1) - ROUND_TRIP_COST

        for f in FEATURE_NAMES:
            all_features[f].append(feats[f][ei])
        all_pnl.append(pnl)
        all_ret24.append(feats["return_24h"][ei])
        all_epochs.append(ts_ep[ei])

    X_dict = {f: np.concatenate(all_features[f]) for f in FEATURE_NAMES}
    pnl = np.concatenate(all_pnl)
    ret24 = np.concatenate(all_ret24)
    ee = np.concatenate(all_epochs)
    N = len(ee)

    # Compute regime threshold
    hour_keys = (ee // 3600).astype(np.int64)
    uniq_hours, inv_hours = np.unique(hour_keys, return_inverse=True)
    mean_ret24h_raw = np.zeros(len(uniq_hours))
    for i in range(len(uniq_hours)):
        m = inv_hours == i
        r = ret24[m]
        ok = ~np.isnan(r)
        if ok.sum() > 0:
            mean_ret24h_raw[i] = np.mean(r[ok])
    mean_ret24h = np.copy(mean_ret24h_raw)
    for i in range(24, len(mean_ret24h)):
        mean_ret24h[i] = np.mean(mean_ret24h_raw[i - 24:i])
    regime = mean_ret24h[inv_hours]
    regime_threshold = float(np.percentile(regime[~np.isnan(regime)], 60))

    # Prepare X
    X_raw = np.column_stack([X_dict[f] for f in FEATURE_NAMES])
    valid_mask = ~np.isnan(pnl) & ~np.any(np.isnan(X_raw), axis=1)
    X = X_raw[valid_mask]
    y = pnl[valid_mask]
    N_valid = len(y)

    X_mean = X.mean(axis=0)
    X_std = X.std(axis=0)
    X_std[X_std == 0] = 1.0
    X_norm = (X - X_mean) / X_std

    # Train ridge
    w = np.linalg.solve(X_norm.T @ X_norm + BEST_ALPHA * np.eye(X_norm.shape[1]),
                        X_norm.T @ y)
    scores = X_norm @ w

    # Compute score threshold at p80
    score_threshold = float(np.percentile(scores, SCORE_PCT))

    # Validate
    sel = scores >= score_threshold
    pf = float(np.sum(y[sel][y[sel] > 0]) / (-np.sum(y[sel][y[sel] <= 0])))
    wp = 100 * (y[sel] > 0).sum() / sel.sum()

    print(f"[export] trained on {N_valid:,} entries", flush=True)
    print(f"[export] regime threshold: {regime_threshold:.4f}", flush=True)
    print(f"[export] score threshold (p{SCORE_PCT}): {score_threshold:.6f}", flush=True)
    print(f"[export] in-sample: PF={pf:.4f}, win={wp:.1f}%, N={sel.sum():,}", flush=True)

    config = ModelConfig(
        weights=w,
        feature_means=X_mean,
        feature_stds=X_std,
        score_threshold=score_threshold,
        regime_threshold=regime_threshold,
        stop_mult=BEST_STOP,
        time_stop_bars=BEST_TS,
        alpha=BEST_ALPHA,
        feature_names=FEATURE_NAMES,
    )

    out_path = os.path.join(os.path.dirname(__file__), "model_config.json")
    save_config(config, out_path)
    print(f"[export] saved: {out_path}", flush=True)
    print(f"[export] done in {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
