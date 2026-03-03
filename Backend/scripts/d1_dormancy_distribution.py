"""
D1 Dormancy Score Distribution Analysis
========================================
Self-contained: computes dormancy_score, accum_divergence, bb_width, volume_z,
rvol from raw OHLCV using numpy — no FeatureFactory dependency.

Run inside Docker:
  cd /home/darren/Desktop/MCAS/Backend
  docker compose -f docker-compose.yml run --rm -T -e PYTHONUNBUFFERED=1 worker \
      python3 /scripts/d1_dormancy_distribution.py
"""
import os, sys, time
import numpy as np
import psycopg

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

# ─── Indicator functions (self-contained, no FeatureFactory) ────────────

def dormancy_score(highs, lows, closes, n=48, threshold=0.001):
    """Fraction of trailing N bars (EXCLUDING current) where (high-low)/close < threshold."""
    if len(closes) < 2:
        return 0.0
    if len(closes) - 1 < n:
        n = len(closes) - 1
    if n == 0:
        return 0.0
    # Exclude current bar: [-(n+1):-1]
    h, l, c = highs[-(n+1):-1], lows[-(n+1):-1], closes[-(n+1):-1]
    safe_c = np.where(c > 0, c, 1.0)
    ranges = (h - l) / safe_c
    return float(np.sum(ranges < threshold) / n)


def accum_divergence(highs, lows, closes, volumes, n=20):
    """ADL slope vs price slope divergence, normalized."""
    if len(closes) < n:
        return 0.0
    h, l, c, v = highs[-n:], lows[-n:], closes[-n:], volumes[-n:]
    hl_range = h - l
    safe_range = np.where(hl_range > 0, hl_range, 1.0)
    clv = ((c - l) - (h - c)) / safe_range
    clv = np.where(hl_range > 0, clv, 0.0)
    adl = np.cumsum(clv * v)
    x = np.arange(n, dtype=float)
    x_mean = x.mean()
    x_var = np.sum((x - x_mean) ** 2)
    if x_var == 0:
        return 0.0
    adl_slope = np.sum((x - x_mean) * (adl - adl.mean())) / x_var
    price_slope = np.sum((x - x_mean) * (c - c.mean())) / x_var
    avg_price = np.mean(c)
    avg_volume = np.mean(v)
    normalizer = avg_price * avg_volume if (avg_price > 0 and avg_volume > 0) else 1.0
    norm_adl = adl_slope / normalizer
    norm_price = price_slope / (avg_price if avg_price > 0 else 1.0)
    return float(norm_adl - norm_price)


def bb_width(closes, n=20):
    """Bollinger Band width = (upper - lower) / middle."""
    if len(closes) < n:
        return 0.0
    c = closes[-n:]
    mb = np.mean(c)
    if mb == 0:
        return 0.0
    std = np.std(c, ddof=0)
    ub = mb + 2 * std
    lb = mb - 2 * std
    return float((ub - lb) / mb)


def volume_z(volumes, n=100):
    """Z-score of latest volume vs trailing N-bar mean/std."""
    if len(volumes) < n:
        n = len(volumes)
    if n < 2:
        return 0.0
    v = volumes[-n:]
    mean_v = np.mean(v[:-1])  # exclude current for z-score
    std_v = np.std(v[:-1], ddof=0)
    if std_v == 0:
        return 0.0
    return float((v[-1] - mean_v) / std_v)


def rvol(volumes, n=20):
    """Relative volume = current / avg(trailing N)."""
    if len(volumes) < n + 1:
        return 1.0
    avg = np.mean(volumes[-(n+1):-1])
    if avg == 0:
        return 1.0
    return float(volumes[-1] / avg)


# ─── Main ───────────────────────────────────────────────────────────────
print("Loading symbol list...")
with psycopg.connect(DATABASE_URL) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT symbol FROM ohlcv
            WHERE timeframe = '1h'
              AND bar_start_ts >= NOW() - INTERVAL '6 months'
              AND is_final = TRUE
            ORDER BY symbol
        """)
        all_symbols = [r[0] for r in cur.fetchall()]

print(f"Total symbols with 1h data: {len(all_symbols)}")

np.random.seed(42)
sample_size = min(200, len(all_symbols))
sample_symbols = list(np.random.choice(all_symbols, sample_size, replace=False))

# Collectors
all_dormancy = []
all_accum_div = []
all_bb_w = []
all_vol_z = []
all_rv = []
all_sym_ids = []     # symbol name per observation
all_bar_ts = []      # bar timestamp (epoch seconds) per observation
symbol_max_dormancy = {}

t0 = time.time()
processed = 0
skipped = 0

for sym in sample_symbols:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = %s AND timeframe = '1h'
                  AND bar_start_ts >= NOW() - INTERVAL '6 months'
                  AND is_final = TRUE
                ORDER BY bar_start_ts ASC
            """, (sym,))
            rows = cur.fetchall()

    if len(rows) < 201:
        skipped += 1
        continue

    timestamps = np.array([r[0].timestamp() for r in rows])
    opens = np.array([float(r[1]) for r in rows])
    highs = np.array([float(r[2]) for r in rows])
    lows = np.array([float(r[3]) for r in rows])
    closes = np.array([float(r[4]) for r in rows])
    volumes = np.array([float(r[5]) for r in rows])

    max_dorm = 0.0
    for i in range(201, len(closes), 6):
        h_w = highs[:i]
        l_w = lows[:i]
        c_w = closes[:i]
        v_w = volumes[:i]

        d = dormancy_score(h_w, l_w, c_w, n=48)
        ad = accum_divergence(h_w, l_w, c_w, v_w, n=20)
        bw = bb_width(c_w, n=20)
        vz = volume_z(v_w, n=100)
        rv = rvol(v_w, n=20)

        all_dormancy.append(d)
        all_accum_div.append(ad)
        all_bb_w.append(bw)
        all_vol_z.append(vz)
        all_rv.append(rv)
        all_sym_ids.append(sym)
        all_bar_ts.append(timestamps[i])
        max_dorm = max(max_dorm, d)

    symbol_max_dormancy[sym] = max_dorm
    processed += 1

    if processed % 50 == 0:
        elapsed = time.time() - t0
        print(f"  [{processed}/{sample_size}] {elapsed:.0f}s elapsed, "
              f"{len(all_dormancy):,} observations, {skipped} skipped")

elapsed = time.time() - t0

# ─── Report ─────────────────────────────────────────────────────────────
d = np.array(all_dormancy)
ts_arr = np.array(all_bar_ts)
sym_arr = np.array(all_sym_ids)

# Time dimension: answer "is this 500 symbols over 9 days or 100 over 47?"
n_unique_syms = len(set(all_sym_ids))
if len(ts_arr) > 0:
    from datetime import datetime as dt, timezone as tz
    min_ts = dt.fromtimestamp(ts_arr.min(), tz=tz.utc)
    max_ts = dt.fromtimestamp(ts_arr.max(), tz=tz.utc)
    span_days = (max_ts - min_ts).total_seconds() / 86400
else:
    min_ts = max_ts = None
    span_days = 0

print(f"\n{'='*60}")
print(f"DORMANCY SCORE DISTRIBUTION ({len(d):,} obs, {processed} symbols, {elapsed:.0f}s)")
print(f"{'='*60}")
print(f"  Unique symbols: {n_unique_syms}")
print(f"  Date range: {min_ts} to {max_ts}")
print(f"  Span: {span_days:.1f} days")
print(f"  Obs per symbol: {len(d)/max(n_unique_syms, 1):.0f} avg")
print(f"  Obs per day: {len(d)/max(span_days, 1):.0f} avg")

if len(d) == 0:
    print("ERROR: 0 observations collected. Check data.")
    sys.exit(1)

print(f"\nPercentiles:")
for p in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
    print(f"  P{p:02d}: {np.percentile(d, p):.4f}")

print(f"\nMean: {np.mean(d):.4f}")
print(f"Std:  {np.std(d):.4f}")

print(f"\nThreshold analysis (% of observations above threshold):")
for t in [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 1.00]:
    pct = np.mean(d >= t) * 100
    count = int(np.sum(d >= t))
    print(f"  dormancy >= {t:.2f}: {pct:6.2f}% ({count:,} obs)")

# Symbol-level
print(f"\nSymbol-level (max dormancy per symbol across all bars):")
max_dorms = np.array(list(symbol_max_dormancy.values()))
for t in [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95]:
    n = int(np.sum(max_dorms >= t))
    print(f"  Symbols with max dormancy >= {t:.2f}: {n}/{len(max_dorms)} ({n/len(max_dorms)*100:.1f}%)")

# BB width when dormancy is high
for dorm_cut in [0.50, 0.70, 0.80]:
    mask = d >= dorm_cut
    if np.any(mask):
        bb_arr = np.array(all_bb_w)
        bb_filt = bb_arr[mask]
        print(f"\nBB width when dormancy >= {dorm_cut:.2f} ({int(np.sum(mask)):,} obs):")
        print(f"  Mean:   {np.mean(bb_filt):.6f}")
        print(f"  Median: {np.median(bb_filt):.6f}")
        print(f"  P25:    {np.percentile(bb_filt, 25):.6f}")
        print(f"  P75:    {np.percentile(bb_filt, 75):.6f}")
        for bb_t in [0.02, 0.05, 0.10, 0.20]:
            print(f"  % below {bb_t}: {np.mean(bb_filt < bb_t)*100:.1f}%")

# Accum divergence distribution
ad = np.array(all_accum_div)
print(f"\nAccumulation Divergence distribution:")
for p in [1, 10, 25, 50, 75, 90, 99]:
    print(f"  P{p:02d}: {np.percentile(ad, p):.6f}")
print(f"  % > 0: {np.mean(ad > 0)*100:.1f}%")

# Volume Z distribution
vz = np.array(all_vol_z)
print(f"\nVolume Z distribution:")
for p in [50, 75, 90, 95, 99]:
    print(f"  P{p:02d}: {np.percentile(vz, p):.4f}")
print(f"  % >= 2.0: {np.mean(vz >= 2.0)*100:.1f}%")
print(f"  % >= 3.0: {np.mean(vz >= 3.0)*100:.1f}%")
print(f"  % >= 5.0: {np.mean(vz >= 5.0)*100:.1f}%")

# RVOL distribution
rv = np.array(all_rv)
print(f"\nRVOL distribution:")
for p in [50, 75, 90, 95, 99]:
    print(f"  P{p:02d}: {np.percentile(rv, p):.4f}")
print(f"  % >= 3.0: {np.mean(rv >= 3.0)*100:.1f}%")
print(f"  % >= 5.0: {np.mean(rv >= 5.0)*100:.1f}%")
print(f"  % >= 10.0: {np.mean(rv >= 10.0)*100:.1f}%")

# ─── Conjunctive gate estimates ─────────────────────────────────────────
print(f"\n{'='*60}")
print(f"CONJUNCTIVE GATE ESTIMATES")
print(f"{'='*60}")
bb = np.array(all_bb_w)
vz_arr = vz
rv_arr = rv
ad_arr = ad

for dorm_t in [0.30, 0.50, 0.60, 0.70, 0.80, 0.90]:
    for bb_t in [0.02, 0.05, 0.10, 0.20]:
        g1 = (d >= dorm_t) & (bb < bb_t)
        g1_pct = np.mean(g1) * 100
        g1_count = int(np.sum(g1))
        if g1_pct < 0.001:
            continue
        g1_and_vol = g1 & (vz_arr >= 2.0) & (rv_arr >= 5.0)
        g1_vol_pct = np.mean(g1_and_vol) * 100
        g1_vol_count = int(np.sum(g1_and_vol))
        g1_vol_accum = g1_and_vol & (ad_arr > 0)
        g1_vol_accum_pct = np.mean(g1_vol_accum) * 100
        g1_vol_accum_count = int(np.sum(g1_vol_accum))
        print(f"  dorm>={dorm_t:.2f} bb<{bb_t}: "
              f"G1={g1_pct:.3f}%({g1_count})  "
              f"+vol={g1_vol_pct:.4f}%({g1_vol_count})  "
              f"+accum={g1_vol_accum_pct:.4f}%({g1_vol_accum_count})")

# ─── Save raw arrays ────────────────────────────────────────────────────
output_dir = '/scripts/output' if os.path.isdir('/scripts') else 'output'
os.makedirs(output_dir, exist_ok=True)
out_path = os.path.join(output_dir, 'd1_dormancy_distribution.npz')
np.savez(out_path, dormancy=d, accum_div=ad_arr, bb_width=bb,
         vol_z=vz_arr, rvol=rv_arr, timestamps=ts_arr, symbols=sym_arr)
print(f"\nRaw arrays saved to {out_path}")
print(f"Done in {elapsed:.0f}s")
