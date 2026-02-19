# Derivatives Walk-Forward Validation — Build Specification

## Your Role

You are building the final validation gate for derivatives features that survived IC screening. The kill/confirm script (derivatives_signal_test.py) found signal. This script answers the harder question: **does the signal survive out-of-sample testing, and does it actually improve ignition model precision?**

## Context From IC Screening Results

The signal test found 8 features with |IC| > 0.015 (p < 0.01). The survivors cluster into three independent signal types, all contrarian:

**Tier 1 — Strongest, use these:**
- `funding_rate` — IC = -0.135 (30d), IC = -0.081 (365d, 630K obs). Low/negative funding → upside.
- `oi_change_pct_24h` — IC = -0.111. 24h OI decline → upside. Strongest single feature on MI.
- `ls_ratio` — IC = -0.094. Lower L/S (more shorts) → upside.

**Tier 2 — Usable but possibly redundant:**
- `funding_rate_ma8` — IC = -0.134. Nearly identical to funding_rate (ρ > 0.95 likely). May not add information.
- `oi_change_pct_4h` — IC = -0.042. Same signal as 24h, weaker.
- `ls_ratio_change_4h` — IC = -0.037. Same signal family as ls_ratio.

**KILLED:**
- `oi_value` — sign flip between pooled and per-symbol IC. Unreliable.
- `funding_rate_acceleration` — IC nonsignificant. MI-only.
- All taker features — IC ≈ 0.

**Critical data constraint:**
- Funding rates: 365 days of raw data (full year)
- OI, L/S, taker: only ~21 days
- OHLCV: ~125 days (453 symbols)

This means: funding rate can be walk-forward validated properly (125 days of aligned OHLCV+funding). OI and L/S features have only 21 days — too short for meaningful expanding-window validation alone. The script must handle this asymmetry honestly.

## Step 0 — Orientation

Read these files:
```
Backend/scripts/derivatives_signal_test.py        — the IC screening script you just ran. Reuse its data loading functions.
Backend/scripts/signal_genesis_ignition.py         — walk-forward implementation pattern (lines 1500-2000 for walk-forward, trade simulation)
Backend/scripts/derivatives_collector.py           — schema context
```

Also read the signal_test JSON results:
```bash
cat Backend/scripts/derivatives_signal_test_results.json | python3 -m json.tool | head -100
```

And check data availability:
```bash
docker compose exec -T postgres psql -U trading -d trading -c "
    -- Funding rate date range
    SELECT count(*), min(funding_time)::date, max(funding_time)::date FROM funding_rates;
    -- OI date range  
    SELECT count(*), min(timestamp)::date, max(timestamp)::date FROM open_interest;
    -- OHLCV date range
    SELECT count(DISTINCT symbol), min(bar_start_ts)::date, max(bar_start_ts)::date
    FROM ohlcv WHERE timeframe = '1h' AND is_final = TRUE;
"
```

## Step 1 — Build the Validation Script

Create `Backend/scripts/derivatives_walkforward.py`.

### Architecture

```
derivatives_walkforward.py
├── Data Loading (reuse from derivatives_signal_test.py)
│   ├── load_ohlcv_matrices(days=140)        — full OHLCV window
│   ├── load_funding_features_long()          — 365d funding → hourly features
│   ├── load_derivatives_features()           — 21d materialized features (OI, LS)
│   └── compute targets: MFH, ignition mask, compression mask
├── Test A: Funding-Only Walk-Forward (125 days, proper validation)
│   ├── 3-fold expanding window
│   ├── Model: logistic regression on funding features → ignition probability
│   ├── Baseline: OHLCV-only ignition score (compression percentile + vol features)
│   ├── Enhanced: OHLCV + funding features
│   └── Compare precision, recall, F1, lift at each fold
├── Test B: Full-Feature Short-Window (21 days, limited but honest)
│   ├── Single train/test split (first 14d train, last 7d test)
│   ├── Model: logistic regression on all Tier 1+2 features → ignition probability
│   ├── Same baseline comparison
│   └── Report with appropriate caveats about sample size
├── Test C: Conditional Precision Improvement
│   ├── During compression windows: does adding funding_rate improve ignition detection?
│   ├── Uses Phase 1 contingency insight: funding separates during compression
│   ├── Simple rule: compressed AND funding_rate < median → "enhanced ignition candidate"
│   ├── Compare ignition rate: enhanced candidates vs all compressed vs random
│   └── McNemar's test for statistical significance
├── Correlation Analysis
│   ├── Pairwise correlation of all surviving features
│   ├── Identify redundancy (|ρ| > 0.8 → drop the weaker feature)
│   └── Determine minimum feature set
└── Final Verdict + Report
```

### Test A: Funding-Only Walk-Forward (the main event)

This is the most statistically rigorous test because funding rate has the longest history.

```python
def walk_forward_funding(close, high, volume, ts_grid, symbols,
                         funding_features: dict, 
                         n_folds=3, embargo_bars=48):
    """
    Expanding-window walk-forward validation of funding rate features
    for ignition prediction.
    
    Design:
    - Target: binary ignition (MFH >= 20% within 24 bars)
    - Features (baseline): BB width percentile, vol_z, range_pct, CLV
      (same features the live ignition model uses)
    - Features (enhanced): baseline + funding_rate + funding_rate_ma8
    - Model: logistic regression (simple, interpretable, less overfit risk)
    - Folds: expanding window, first 40% train → test on next 20%, 
      then 60% train → test on next 20%, etc.
    - Embargo: 48 bars (2 days) between train and test to prevent leakage
    
    Returns per-fold and aggregate results.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import precision_score, recall_score, f1_score
    
    n_bars, n_symbols = close.shape
    
    # Compute targets
    mfh = compute_max_forward_high(close, high, HORIZON)
    ignition = (~np.isnan(mfh)) & (mfh >= IGNITION_THRESHOLD)
    
    # Compute OHLCV baseline features (same as ignition model)
    bb_width_pctile = compute_bb_width_percentile(close)  # see below
    vol_z = compute_vol_z(volume)
    range_pct = compute_range_pct(high, close)  # use low too if available
    
    # Build feature matrices
    # baseline_X: (n_obs, n_baseline_features)
    # enhanced_X: (n_obs, n_baseline_features + n_funding_features)
    
    baseline_features = {
        'bb_width_pctile': bb_width_pctile,
        'vol_z': vol_z,
    }
    
    funding_feature_names = ['funding_rate', 'funding_rate_ma8']
    # Only use features that exist in the funding_features dict
    available_funding = {k: v for k, v in funding_features.items() 
                        if k in ['funding_rate_long', 'funding_rate_ma8_long']}
    
    # Flatten to (n_obs,) arrays — only where ALL features are non-NaN
    # Each observation is a (bar, symbol) pair
    
    obs_t = []
    obs_s = []
    baseline_X_list = []
    enhanced_X_list = []
    y_list = []
    
    for t in range(WARMUP_BARS, n_bars - HORIZON):
        for s in range(n_symbols):
            # Check all baseline features are valid
            bb = bb_width_pctile[t, s]
            vz = vol_z[t, s]
            if np.isnan(bb) or np.isnan(vz):
                continue
            
            # Check funding features
            fr_vals = []
            all_valid = True
            for fname, farr in available_funding.items():
                v = farr[t, s]
                if np.isnan(v):
                    all_valid = False
                    break
                fr_vals.append(v)
            
            if not all_valid:
                continue
            
            # Check target
            ign = ignition[t, s]
            if np.isnan(mfh[t, s]):
                continue
            
            obs_t.append(t)
            obs_s.append(s)
            baseline_X_list.append([bb, vz])
            enhanced_X_list.append([bb, vz] + fr_vals)
            y_list.append(1 if ign else 0)
    
    obs_t = np.array(obs_t)
    baseline_X = np.array(baseline_X_list)
    enhanced_X = np.array(enhanced_X_list)
    y = np.array(y_list)
    
    logger.info("Walk-forward dataset: %d observations, %.2f%% positive",
                len(y), 100 * np.mean(y))
    
    # Define fold boundaries based on time
    t_min, t_max = obs_t.min(), obs_t.max()
    t_range = t_max - t_min
    
    fold_results = []
    
    for fold in range(n_folds):
        # Expanding window: train on first (40 + 20*fold)%, test on next 20%
        train_frac = 0.40 + 0.20 * fold
        test_start_frac = train_frac
        test_end_frac = min(train_frac + 0.20, 1.0)
        
        train_end_t = t_min + int(t_range * train_frac)
        test_start_t = train_end_t + embargo_bars
        test_end_t = t_min + int(t_range * test_end_frac)
        
        if test_start_t >= test_end_t:
            continue
        
        train_mask = obs_t <= train_end_t
        test_mask = (obs_t >= test_start_t) & (obs_t <= test_end_t)
        
        if np.sum(train_mask) < 100 or np.sum(test_mask) < 50:
            logger.warning("Fold %d: insufficient data (train=%d, test=%d)", 
                          fold, np.sum(train_mask), np.sum(test_mask))
            continue
        
        # Train baseline model
        scaler_b = StandardScaler()
        X_train_b = scaler_b.fit_transform(baseline_X[train_mask])
        X_test_b = scaler_b.transform(baseline_X[test_mask])
        y_train = y[train_mask]
        y_test = y[test_mask]
        
        model_b = LogisticRegression(class_weight='balanced', max_iter=1000, C=0.1)
        model_b.fit(X_train_b, y_train)
        pred_b = model_b.predict(X_test_b)
        prob_b = model_b.predict_proba(X_test_b)[:, 1]
        
        # Train enhanced model
        scaler_e = StandardScaler()
        X_train_e = scaler_e.fit_transform(enhanced_X[train_mask])
        X_test_e = scaler_e.transform(enhanced_X[test_mask])
        
        model_e = LogisticRegression(class_weight='balanced', max_iter=1000, C=0.1)
        model_e.fit(X_train_e, y_train)
        pred_e = model_e.predict(X_test_e)
        prob_e = model_e.predict_proba(X_test_e)[:, 1]
        
        # Metrics
        prec_b = precision_score(y_test, pred_b, zero_division=0)
        prec_e = precision_score(y_test, pred_e, zero_division=0)
        rec_b = recall_score(y_test, pred_b, zero_division=0)
        rec_e = recall_score(y_test, pred_e, zero_division=0)
        
        # Lift at top decile (more relevant than overall precision)
        top_10_b = np.argsort(prob_b)[-max(1, len(prob_b)//10):]
        top_10_e = np.argsort(prob_e)[-max(1, len(prob_e)//10):]
        lift_b = np.mean(y_test[top_10_b]) / np.mean(y_test) if np.mean(y_test) > 0 else 0
        lift_e = np.mean(y_test[top_10_e]) / np.mean(y_test) if np.mean(y_test) > 0 else 0
        
        # McNemar's test: are the models making different errors?
        # Contingency: both right, baseline right + enhanced wrong, etc.
        both_right = np.sum((pred_b == y_test) & (pred_e == y_test))
        b_right_e_wrong = np.sum((pred_b == y_test) & (pred_e != y_test))
        b_wrong_e_right = np.sum((pred_b != y_test) & (pred_e == y_test))
        both_wrong = np.sum((pred_b != y_test) & (pred_e != y_test))
        
        # McNemar statistic (with continuity correction)
        if (b_right_e_wrong + b_wrong_e_right) > 0:
            mcnemar_chi2 = (abs(b_right_e_wrong - b_wrong_e_right) - 1)**2 / (b_right_e_wrong + b_wrong_e_right)
            from scipy.stats import chi2
            mcnemar_p = 1 - chi2.cdf(mcnemar_chi2, df=1)
        else:
            mcnemar_chi2, mcnemar_p = 0, 1.0
        
        result = {
            "fold": fold,
            "train_size": int(np.sum(train_mask)),
            "test_size": int(np.sum(test_mask)),
            "test_positive_rate": float(np.mean(y_test)),
            "baseline_precision": float(prec_b),
            "enhanced_precision": float(prec_e),
            "precision_delta": float(prec_e - prec_b),
            "baseline_recall": float(rec_b),
            "enhanced_recall": float(rec_e),
            "baseline_lift_top10": float(lift_b),
            "enhanced_lift_top10": float(lift_e),
            "mcnemar_chi2": float(mcnemar_chi2),
            "mcnemar_p": float(mcnemar_p),
            "enhanced_model_coefs": {name: float(c) for name, c in 
                                      zip(["bb_pctile", "vol_z"] + list(available_funding.keys()),
                                          model_e.coef_[0])},
        }
        fold_results.append(result)
        
        logger.info("  Fold %d: baseline_prec=%.3f enhanced_prec=%.3f (Δ=%+.3f)  "
                    "lift_base=%.2f lift_enh=%.2f  McNemar p=%.4f  [test=%d obs, %.1f%% pos]",
                    fold, prec_b, prec_e, prec_e - prec_b,
                    lift_b, lift_e, mcnemar_p,
                    np.sum(test_mask), 100*np.mean(y_test))
    
    return fold_results
```

**Baseline feature computation helpers** (match signal_genesis_ignition.py logic):

```python
def compute_bb_width_percentile(close, sma_window=20, pctile_lookback=100):
    """BB width as percentile of trailing history. Shape: (n_bars, n_symbols)."""
    sma = _rolling_mean_2d(close, sma_window)
    std = _rolling_std_2d(close, sma_window)
    bb_width = np.where(sma > 0, 4 * std / sma, np.nan)
    
    n_bars, n_symbols = close.shape
    pctile = np.full_like(bb_width, np.nan)
    
    from numpy.lib.stride_tricks import sliding_window_view
    if n_bars > pctile_lookback:
        try:
            windows = sliding_window_view(bb_width, pctile_lookback, axis=0)
            current = bb_width[pctile_lookback-1:]
            n_below = np.nansum(windows < current[:, :, np.newaxis], axis=2)
            n_valid = np.nansum(~np.isnan(windows), axis=2)
            pctile[pctile_lookback-1:] = np.where(n_valid > 0, 100.0 * n_below / n_valid, 50.0)
        except MemoryError:
            # Fallback to loop
            for t in range(pctile_lookback, n_bars):
                window = bb_width[t-pctile_lookback:t]
                current = bb_width[t]
                n_below = np.nansum(window < current[np.newaxis, :], axis=0)
                n_valid = np.nansum(~np.isnan(window), axis=0)
                pctile[t] = np.where(n_valid > 0, 100.0 * n_below / n_valid, 50.0)
    
    return pctile

def compute_vol_z(volume, window=20):
    """Volume z-score. Shape: (n_bars, n_symbols)."""
    mean = _rolling_mean_2d(volume, window)
    std = _rolling_std_2d(volume, window)
    return np.where(std > 0, (volume - mean) / std, 0.0)
```

### Test B: Full-Feature Short-Window

```python
def short_window_validation(close, high, volume, ts_grid, symbols,
                            deriv_features: dict, deriv_names: list):
    """
    Single train/test split on the 21-day derivatives window.
    Uses all Tier 1+2 features. Reports with caveats about limited sample.
    
    Split: first 14 days train, last 7 days test, 48-bar embargo.
    """
    # Same structure as walk_forward_funding but single fold
    # Features: bb_width_pctile, vol_z + all non-NaN derivatives features
    # Model: logistic regression
    # Report: precision, recall, lift, plus feature importance (coefficients)
    
    # IMPORTANT: Only include features that have >50% non-NaN coverage
    # in the training window. Skip features with insufficient data.
    
    # ... (follow same pattern as Test A but single fold)
    pass
```

### Test C: Conditional Precision Improvement

This is the most operationally relevant test. It answers: **if I only trade compressed coins with favorable derivatives positioning, does my hit rate improve?**

```python
def conditional_precision_test(close, high, volume, ts_grid, symbols,
                               funding_features: dict, deriv_features: dict):
    """
    The practical question: during compression, does funding rate improve
    ignition detection precision?
    
    Strategy:
    - Detect compressed bars (BB width < 10th percentile)
    - Split compressed bars into:
      A) funding_rate < symbol's 30th percentile ("bearish positioning")
      B) funding_rate >= symbol's 30th percentile ("neutral/bullish")
    - Compare ignition rates: A vs B vs all compressed
    
    This is the Phase 1 contingency result, but computed properly with
    per-symbol percentiles (not global) and with time-aware splitting
    (train percentile on first half, test on second half).
    """
    n_bars, n_symbols = close.shape
    
    # Targets
    mfh = compute_max_forward_high(close, high, HORIZON)
    ignition = (~np.isnan(mfh)) & (mfh >= IGNITION_THRESHOLD)
    
    # Compression
    compressed = detect_compressions(close)
    
    # Funding rate (use long-window version)
    fr = funding_features.get('funding_rate_long', 
                              funding_features.get('funding_rate'))
    if fr is None:
        logger.error("No funding rate feature available for conditional test")
        return None
    
    # Time split: first 60% for calibration, last 40% for test
    split_t = int(n_bars * 0.6)
    
    # Calibrate: compute per-symbol 30th percentile of funding_rate on train window
    sym_thresholds = np.full(n_symbols, np.nan)
    for s in range(n_symbols):
        train_vals = fr[:split_t, s]
        valid = train_vals[~np.isnan(train_vals)]
        if len(valid) > 20:
            sym_thresholds[s] = np.percentile(valid, 30)
    
    # Test window: compressed bars after split
    test_compressed = compressed[split_t:] 
    test_ignition = ignition[split_t:]
    test_fr = fr[split_t:]
    
    # Group A: compressed AND funding < threshold (bearish positioning)
    group_a = np.zeros_like(test_compressed)
    group_b = np.zeros_like(test_compressed)
    for s in range(n_symbols):
        if np.isnan(sym_thresholds[s]):
            continue
        comp_mask = test_compressed[:, s]
        group_a[:, s] = comp_mask & (test_fr[:, s] < sym_thresholds[s])
        group_b[:, s] = comp_mask & (test_fr[:, s] >= sym_thresholds[s]) & ~np.isnan(test_fr[:, s])
    
    # Ignition rates
    n_a = np.sum(group_a)
    n_b = np.sum(group_b)
    n_all_comp = np.sum(test_compressed[..., :n_symbols] if test_compressed.ndim == 2 else test_compressed)
    
    ign_a = np.sum(test_ignition[group_a]) if n_a > 0 else 0
    ign_b = np.sum(test_ignition[group_b]) if n_b > 0 else 0
    ign_all = np.sum(test_ignition[test_compressed[:, :n_symbols]]) if n_all_comp > 0 else 0
    
    rate_a = ign_a / n_a if n_a > 0 else 0
    rate_b = ign_b / n_b if n_b > 0 else 0
    rate_all = ign_all / n_all_comp if n_all_comp > 0 else 0
    
    # Fisher's exact test or chi-squared
    from scipy.stats import fisher_exact
    if n_a > 0 and n_b > 0:
        table = np.array([[ign_a, n_a - ign_a], [ign_b, n_b - ign_b]])
        odds_ratio, fisher_p = fisher_exact(table)
    else:
        odds_ratio, fisher_p = 1.0, 1.0
    
    result = {
        "group_a_bearish": {"n": int(n_a), "ignited": int(ign_a), "rate": float(rate_a)},
        "group_b_neutral": {"n": int(n_b), "ignited": int(ign_b), "rate": float(rate_b)},
        "all_compressed": {"n": int(n_all_comp), "ignited": int(ign_all), "rate": float(rate_all)},
        "odds_ratio_a_vs_b": float(odds_ratio),
        "fisher_p": float(fisher_p),
        "precision_improvement_pp": float((rate_a - rate_all) * 100),
    }
    
    logger.info("\n" + "=" * 70)
    logger.info("TEST C: CONDITIONAL PRECISION — Compression + Bearish Funding")
    logger.info("=" * 70)
    logger.info("  Bearish (funding < p30):  %d obs, %d ignited, %.2f%% rate",
                n_a, ign_a, 100 * rate_a)
    logger.info("  Neutral (funding >= p30): %d obs, %d ignited, %.2f%% rate",
                n_b, ign_b, 100 * rate_b)
    logger.info("  All compressed:           %d obs, %d ignited, %.2f%% rate",
                n_all_comp, ign_all, 100 * rate_all)
    logger.info("  Odds ratio (bearish/neutral): %.2f  (Fisher p=%.4f)", odds_ratio, fisher_p)
    logger.info("  Precision improvement: %+.1f pp", (rate_a - rate_all) * 100)
    
    return result
```

### Correlation Analysis

```python
def feature_correlation_analysis(features: dict, feature_names: list):
    """Pairwise Spearman correlation of surviving features.
    Identifies redundancy — if |ρ| > 0.8, recommend dropping the weaker feature.
    """
    from scipy.stats import spearmanr
    
    # Stack all features into a matrix, using only rows where all are non-NaN
    arrays = [features[f].ravel() for f in feature_names]
    stacked = np.column_stack(arrays)
    valid = ~np.any(np.isnan(stacked), axis=1)
    stacked = stacked[valid]
    
    n = len(feature_names)
    corr_matrix = np.zeros((n, n))
    
    for i in range(n):
        for j in range(i, n):
            rho, _ = spearmanr(stacked[:, i], stacked[:, j])
            corr_matrix[i, j] = rho
            corr_matrix[j, i] = rho
    
    logger.info("\n" + "=" * 70)
    logger.info("FEATURE CORRELATION MATRIX")
    logger.info("=" * 70)
    
    # Pretty print
    header = "".join(f"{f[:12]:>13}" for f in feature_names)
    logger.info("             %s", header)
    for i, fname in enumerate(feature_names):
        row = "".join(f"{corr_matrix[i,j]:>13.3f}" for j in range(n))
        logger.info("%12s %s", fname[:12], row)
    
    # Flag redundant pairs
    redundant = []
    for i in range(n):
        for j in range(i+1, n):
            if abs(corr_matrix[i, j]) > 0.8:
                redundant.append((feature_names[i], feature_names[j], corr_matrix[i, j]))
                logger.info("  ⚠ REDUNDANT: %s ↔ %s (ρ=%.3f) — drop the weaker",
                           feature_names[i], feature_names[j], corr_matrix[i, j])
    
    return corr_matrix, redundant
```

### Main Function

```python
def main():
    parser = argparse.ArgumentParser(description="Derivatives Walk-Forward Validation")
    parser.add_argument("--days", type=int, default=140, help="OHLCV window (default: 140 for full history)")
    parser.add_argument("--funding-days", type=int, default=400, help="Funding rate lookback")
    parser.add_argument("--n-folds", type=int, default=3, help="Walk-forward folds for Test A")
    args = parser.parse_args()
    
    database_url = os.environ["DATABASE_URL"]
    
    logger.info("=" * 70)
    logger.info("DERIVATIVES WALK-FORWARD VALIDATION")
    logger.info("=" * 70)
    
    # ── Load data ──
    logger.info("Loading OHLCV (%d days)...", args.days)
    close, open_, high, low, volume, ts_grid, symbols, btc_idx, _ = \
        load_ohlcv_matrices(database_url, args.days)
    n_bars, n_symbols = close.shape
    logger.info("OHLCV: %d bars × %d symbols", n_bars, n_symbols)
    
    logger.info("Loading funding features (long window)...")
    funding_features = load_funding_features_long(database_url, symbols, ts_grid)
    
    logger.info("Loading 21-day derivatives features...")
    deriv_features, deriv_names = load_derivatives_features(database_url, symbols, ts_grid)
    
    # ── Correlation analysis first (informs feature selection) ──
    tier1_names = ['funding_rate_long', 'oi_change_pct_24h', 'ls_ratio']
    tier2_names = ['funding_rate_ma8_long', 'oi_change_pct_4h', 'ls_ratio_change_4h']
    
    # Merge feature dicts for correlation
    all_features = {**funding_features, **deriv_features}
    available = [f for f in tier1_names + tier2_names if f in all_features or f.replace('_long', '') in all_features]
    # Map names to actual arrays
    feat_for_corr = {}
    for f in available:
        if f in all_features:
            feat_for_corr[f] = all_features[f]
        elif f.replace('_long', '') in all_features:
            feat_for_corr[f] = all_features[f.replace('_long', '')]
    
    if len(feat_for_corr) >= 2:
        corr_matrix, redundant = feature_correlation_analysis(feat_for_corr, list(feat_for_corr.keys()))
    
    # ── Test A: Funding walk-forward (main event) ──
    logger.info("\n" + "=" * 70)
    logger.info("TEST A: FUNDING-ONLY WALK-FORWARD (%d folds, 125-day window)", args.n_folds)
    logger.info("=" * 70)
    
    fold_results_a = walk_forward_funding(
        close, high, volume, ts_grid, symbols,
        funding_features, n_folds=args.n_folds
    )
    
    if fold_results_a:
        avg_prec_delta = np.mean([r["precision_delta"] for r in fold_results_a])
        avg_lift_delta = np.mean([r["enhanced_lift_top10"] - r["baseline_lift_top10"] for r in fold_results_a])
        consistent = sum(1 for r in fold_results_a if r["precision_delta"] > 0)
        
        logger.info("\n  Test A Summary:")
        logger.info("    Avg precision delta: %+.3f", avg_prec_delta)
        logger.info("    Avg lift improvement: %+.2f", avg_lift_delta)
        logger.info("    Consistent improvement: %d/%d folds", consistent, len(fold_results_a))
    
    # ── Test B: Short-window full-feature (limited) ──
    logger.info("\n" + "=" * 70)
    logger.info("TEST B: FULL-FEATURE SHORT-WINDOW (21 days, single split)")
    logger.info("=" * 70)
    
    fold_results_b = short_window_validation(
        close, high, volume, ts_grid, symbols,
        deriv_features, deriv_names
    )
    
    # ── Test C: Conditional precision (operational test) ──
    logger.info("\n" + "=" * 70)
    logger.info("TEST C: CONDITIONAL PRECISION — Compression + Funding")
    logger.info("=" * 70)
    
    test_c_result = conditional_precision_test(
        close, high, volume, ts_grid, symbols,
        funding_features, deriv_features
    )
    
    # ════════════════════════════════════════════════════════════════
    # FINAL VERDICT
    # ════════════════════════════════════════════════════════════════
    
    logger.info("\n" + "=" * 70)
    logger.info("FINAL VERDICT")
    logger.info("=" * 70)
    
    # Accept criteria:
    # 1. Test A: precision improvement >= 1pp in majority of folds
    # 2. Test C: odds ratio > 1.3 with Fisher p < 0.10
    # 3. Feature coefficients have consistent sign across folds
    
    accept_a = False
    if fold_results_a:
        accept_a = (consistent >= len(fold_results_a) * 0.67 and avg_prec_delta > 0.01)
    
    accept_c = False
    if test_c_result:
        accept_c = (test_c_result["odds_ratio_a_vs_b"] > 1.3 and 
                    test_c_result["fisher_p"] < 0.10)
    
    if accept_a or accept_c:
        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════════════════╗")
        logger.info("║  ACCEPT: Derivatives features improve ignition detection.       ║")
        logger.info("║                                                                 ║")
        if accept_a:
            logger.info("║  Test A passed: +%.1f pp precision across %d/%d folds.           ║",
                       avg_prec_delta * 100, consistent, len(fold_results_a))
        if accept_c:
            logger.info("║  Test C passed: OR=%.2f (p=%.3f) for bearish positioning.       ║",
                       test_c_result["odds_ratio_a_vs_b"], test_c_result["fisher_p"])
        logger.info("║                                                                 ║")
        logger.info("║  Recommended features for live model:                           ║")
        logger.info("║    1. funding_rate (strongest, longest history)                 ║")
        logger.info("║    2. oi_change_pct_24h (if OI data extends via Coinglass)      ║")
        logger.info("║    3. ls_ratio (independent signal, if not redundant)           ║")
        logger.info("║                                                                 ║")
        logger.info("║  Next: Wire into live ignition model as conditioning features.  ║")
        logger.info("╚══════════════════════════════════════════════════════════════════╝")
    else:
        logger.info("")
        logger.info("╔══════════════════════════════════════════════════════════════════╗")
        logger.info("║  REJECT: Features show IC but don't improve ignition model.     ║")
        logger.info("║                                                                 ║")
        logger.info("║  IC screening showed correlation, but walk-forward shows it     ║")
        logger.info("║  doesn't translate to precision improvement. Possible causes:   ║")
        logger.info("║  - Signal is real but too weak vs noise at decision boundary    ║")
        logger.info("║  - 21-day OI/LS window too short (retry with Coinglass data)    ║")
        logger.info("║  - Features are informative generally but not at compression    ║")
        logger.info("║                                                                 ║")
        logger.info("║  Keep collector running. Retry with 6-month data if available.  ║")
        logger.info("╚══════════════════════════════════════════════════════════════════╝")
    
    # Write results
    _write_results({
        "test_a_funding_walkforward": fold_results_a,
        "test_b_short_window": fold_results_b,
        "test_c_conditional": test_c_result,
        "correlation_redundant_pairs": [(a, b, float(r)) for a, b, r in (redundant if 'redundant' in dir() else [])],
        "verdict": {
            "accept": accept_a or accept_c,
            "test_a_passed": accept_a,
            "test_c_passed": accept_c,
        }
    })

def _write_results(data):
    out_path = os.path.join(os.path.dirname(__file__), "derivatives_walkforward_results.json")
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    logger.info("Results written to %s", out_path)
```

### Dependencies

The script needs scikit-learn and scipy. Install at top:
```python
try:
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import precision_score, recall_score
    from scipy.stats import chi2, fisher_exact, spearmanr
except ImportError:
    import subprocess
    subprocess.check_call(["pip", "install", "scikit-learn", "scipy", "--break-system-packages", "-q"])
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import precision_score, recall_score
    from scipy.stats import chi2, fisher_exact, spearmanr
```

### Copy/Import These From derivatives_signal_test.py

Don't rewrite — copy verbatim:
- `load_ohlcv_matrices()`
- `load_funding_features_long()`
- `load_derivatives_features()`
- `compute_max_forward_high()`
- `build_event_catalog()`
- `_rolling_mean_2d()`, `_rolling_std_2d()`
- All constants: HORIZON, IGNITION_THRESHOLD, WARMUP_BARS, MIN_BARS_PER_SYMBOL

Also copy detect_compressions from derivatives_signal_test.py (the fast version with sliding_window_view, with MemoryError fallback).

## Step 2 — Run It

```bash
docker compose run --rm -T worker python3 /scripts/derivatives_walkforward.py --days 140 --funding-days 400 --n-folds 3
```

Expected runtime: 3-10 minutes (logistic regression on ~100K observations is fast).

## Step 3 — Report Results

Report:
1. **Correlation matrix** — which features are redundant?
2. **Test A fold-by-fold results** — precision delta, lift delta, McNemar p, model coefficients
3. **Test B results** — with caveat about 21-day window
4. **Test C results** — the operational question: odds ratio and precision improvement
5. **Final verdict**: ACCEPT or REJECT
6. **Full JSON results file contents**

## Non-Goals

- Don't modify any existing code
- Don't attempt to extend OI data via Coinglass (that's a separate decision)
- Don't wire features into the live model (that's next, only if ACCEPT)
- Don't optimize hyperparameters — C=0.1 logistic regression is deliberately simple to prevent overfit on short windows
- Don't run multiple model types — logistic regression only. If LR can't find the signal, fancier models will overfit.

## Quality Bar

Honest reporting. If the walk-forward says REJECT despite strong IC screening, that's a valid result — IC measures correlation, walk-forward measures practical predictive value. They can disagree. Report it straight.

The accept thresholds are:
- Test A: precision improvement >= 1pp in 2/3+ folds (deliberately low bar — we're looking for any improvement, not large improvement)
- Test C: odds ratio > 1.3 with Fisher p < 0.10 (operational: does filtering by funding rate meaningfully increase hit rate?)

If neither passes, the features have statistical association with ignition but don't translate to actionable improvement. That's a REJECT with the recommendation to retry when more OI/LS data accumulates or after Coinglass backfill.
