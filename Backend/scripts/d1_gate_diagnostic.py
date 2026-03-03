"""
D1 Gate Diagnostic — identifies which gate kills candidates.
Walks the D1 evaluator over known high-dormancy symbols and logs
gate-by-gate rejection counts.

Run inside Docker:
  docker compose run --rm -T -e PYTHONUNBUFFERED=1 worker \
      python3 /scripts/d1_gate_diagnostic.py
"""
import os, sys, time, json
import numpy as np
import psycopg

DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)


def dormancy_score(highs, lows, closes, n=48, threshold=0.001):
    if len(closes) < 2:
        return 0.0
    if len(closes) - 1 < n:
        n = len(closes) - 1
    if n == 0:
        return 0.0
    h, l, c = highs[-(n+1):-1], lows[-(n+1):-1], closes[-(n+1):-1]
    safe_c = np.where(c > 0, c, 1.0)
    ranges = (h - l) / safe_c
    return float(np.sum(ranges < threshold) / n)


def bb_width(closes, n=20):
    if len(closes) < n:
        return 0.0
    c = closes[-n:]
    mb = np.mean(c)
    if mb == 0:
        return 0.0
    std = np.std(c, ddof=0)
    return float((mb + 2*std - (mb - 2*std)) / mb)


def volume_z(volumes, n=100):
    if len(volumes) < n:
        n = len(volumes)
    if n < 2:
        return 0.0
    v = volumes[-n:]
    mean_v = np.mean(v[:-1])
    std_v = np.std(v[:-1], ddof=0)
    if std_v == 0:
        return 0.0
    return float((v[-1] - mean_v) / std_v)


def rvol(volumes, n=20):
    if len(volumes) < n + 1:
        return 1.0
    avg = np.mean(volumes[-(n+1):-1])
    if avg == 0:
        return 1.0
    return float(volumes[-1] / avg)


# ─── Config ──────────────────────────────────────────────────────────────
DORM_THRESH = 0.75
BB_THRESH = 0.05
VZ_THRESH = 2.0
RV_THRESH = 3.0

print("Loading OHLCV data...")
t0 = time.time()

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

print(f"Total symbols: {len(all_symbols)}")

# Gate counters
total_bars_evaluated = 0
gate_pass = {
    'dormancy': 0,
    'dormancy+bb': 0,
    'dormancy+bb+volz': 0,
    'dormancy+bb+volz+rvol': 0,
    'dormancy+bb+volz+rvol+breakout': 0,
}
gate_symbols = {k: set() for k in gate_pass}

# Per-symbol tracking
symbol_stats = {}
t0 = time.time()
processed = 0

for sym in all_symbols:
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT bar_start_ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = %s AND timeframe = '1h'
                  AND bar_start_ts >= NOW() - INTERVAL '9 months'
                  AND is_final = TRUE
                ORDER BY bar_start_ts ASC
            """, (sym,))
            rows = cur.fetchall()

    if len(rows) < 201:
        continue

    highs = np.array([float(r[2]) for r in rows])
    lows = np.array([float(r[3]) for r in rows])
    closes = np.array([float(r[4]) for r in rows])
    volumes = np.array([float(r[5]) for r in rows])

    sym_gates = {k: 0 for k in gate_pass}
    prev_donchian_high = 0.0

    for i in range(201, len(closes)):
        total_bars_evaluated += 1
        h_w = highs[:i+1]
        l_w = lows[:i+1]
        c_w = closes[:i+1]
        v_w = volumes[:i+1]

        # Gate 1: Dormancy
        d = dormancy_score(h_w, l_w, c_w, n=48)
        if d < DORM_THRESH:
            # Update donchian for next bar
            prev_donchian_high = float(np.max(highs[max(0,i-19):i]))
            continue

        gate_pass['dormancy'] += 1
        gate_symbols['dormancy'].add(sym)
        sym_gates['dormancy'] += 1

        # Gate 2: BB width
        bw = bb_width(c_w, n=20)
        if bw >= BB_THRESH:
            prev_donchian_high = float(np.max(highs[max(0,i-19):i]))
            continue

        gate_pass['dormancy+bb'] += 1
        gate_symbols['dormancy+bb'].add(sym)
        sym_gates['dormancy+bb'] += 1

        # Gate 3: Volume Z
        vz = volume_z(v_w, n=100)
        if vz < VZ_THRESH:
            prev_donchian_high = float(np.max(highs[max(0,i-19):i]))
            continue

        gate_pass['dormancy+bb+volz'] += 1
        gate_symbols['dormancy+bb+volz'].add(sym)
        sym_gates['dormancy+bb+volz'] += 1

        # Gate 4: RVOL
        rv = rvol(v_w, n=20)
        if rv < RV_THRESH:
            prev_donchian_high = float(np.max(highs[max(0,i-19):i]))
            continue

        gate_pass['dormancy+bb+volz+rvol'] += 1
        gate_symbols['dormancy+bb+volz+rvol'].add(sym)
        sym_gates['dormancy+bb+volz+rvol'] += 1

        # Gate 5: Compression box breakout (close > prev donchian high)
        close_now = closes[i]
        if prev_donchian_high > 0 and close_now > prev_donchian_high:
            gate_pass['dormancy+bb+volz+rvol+breakout'] += 1
            gate_symbols['dormancy+bb+volz+rvol+breakout'].add(sym)
            sym_gates['dormancy+bb+volz+rvol+breakout'] += 1

        # Update donchian for next bar (BEFORE current bar, matching D1 logic)
        prev_donchian_high = float(np.max(highs[max(0,i-19):i]))

    if sym_gates['dormancy'] > 0:
        symbol_stats[sym] = sym_gates

    processed += 1
    if processed % 100 == 0:
        elapsed = time.time() - t0
        print(f"  [{processed}/{len(all_symbols)}] {elapsed:.0f}s elapsed")

elapsed = time.time() - t0

# ─── Report ──────────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"D1 GATE DIAGNOSTIC ({total_bars_evaluated:,} bars, {processed} symbols, {elapsed:.0f}s)")
print(f"{'='*60}")
print(f"Thresholds: dorm>={DORM_THRESH} bb<{BB_THRESH} vz>={VZ_THRESH} rv>={RV_THRESH}")
print()

for gate, count in gate_pass.items():
    n_syms = len(gate_symbols[gate])
    print(f"  {gate:45s}: {count:6,} obs, {n_syms:3d} symbols")

print(f"\n--- Drop-off analysis ---")
prev_count = total_bars_evaluated
prev_label = "all bars"
for gate, count in gate_pass.items():
    if prev_count > 0:
        survival = count / prev_count * 100
        dropped = prev_count - count
        print(f"  {prev_label} -> {gate}: {dropped:,} dropped ({survival:.3f}% survived)")
    prev_count = count
    prev_label = gate

# Show which symbols pass all gates vs which get killed at breakout
print(f"\n--- Symbols passing volume gates but failing breakout ---")
vol_syms = gate_symbols['dormancy+bb+volz+rvol']
breakout_syms = gate_symbols['dormancy+bb+volz+rvol+breakout']
killed_at_breakout = vol_syms - breakout_syms
print(f"  Pass all volume gates: {len(vol_syms)} symbols")
print(f"  Also pass breakout:   {len(breakout_syms)} symbols")
print(f"  Killed at breakout:   {len(killed_at_breakout)} symbols")
if killed_at_breakout:
    for s in sorted(killed_at_breakout)[:20]:
        n = symbol_stats[s]['dormancy+bb+volz+rvol']
        print(f"    {s}: {n} obs passed volume gates, 0 breakouts")

# Show breakout survivors detail
if breakout_syms:
    print(f"\n--- Breakout survivors ---")
    for s in sorted(breakout_syms):
        stats = symbol_stats[s]
        print(f"    {s}: dorm={stats['dormancy']}, +bb={stats['dormancy+bb']}, "
              f"+vz={stats['dormancy+bb+volz']}, +rv={stats['dormancy+bb+volz+rvol']}, "
              f"+breakout={stats['dormancy+bb+volz+rvol+breakout']}")
