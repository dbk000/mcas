# D1 Universe Segmentation — Scope Document

## Origin

During D1 v1.1 development, the strategy was initially validated on the dev DB (422 symbols, 955 opportunities) producing daily PF 2.41. When re-validated on the production DB (1,912 eligible symbols → 528 with D1 signals, 2,836 opportunities), Config D **improved** to daily PF 2.70, WR 43.4%, aggPF 3.63.

This revealed that the full universe contains two distinct populations — liquid symbols (consistently in both dev and prod) and illiquid symbols (only in prod's larger Known Universe). Rather than averaging across both, each population should be calibrated independently.

## Architecture: 3 Variants Per Microcap Strategy

Every microcap strategy (D1, and all future microcap setups) gets three independently calibrated variants:

### 1. D1-Liquid
- **Universe:** Symbols present in the ~422 higher-liquidity tier (dev-equivalent coverage)
- **Characteristics:** Tighter spreads, faster breakouts, more competition, more consistent OHLCV
- **Calibration:** Independent sim matrix sweep → own winning config
- **Pipeline node:** Separate node with own biography, version history, node_impact records

### 2. D1-Illiquid
- **Universe:** Symbols present ONLY in the broader ~1,490 tier (prod minus liquid)
- **Characteristics:** Wider spreads, slower ignitions, bigger moves when they fire, spottier data
- **Calibration:** Independent sim matrix sweep → own winning config
- **Pipeline node:** Separate node with own biography, version history, node_impact records

### 3. D1-Combined
- **Universe:** Full production universe (~1,912 eligible → 528+ with signals)
- **Purpose:** Baseline reference, detects interaction effects between tiers
- **Calibration:** Independent sim matrix sweep (this is the current Config D result)
- **Pipeline node:** Separate node — the "parent" strategy

## Implementation Plan

### Phase 1: Universe Tier Classification
- Add `universe_tier` column or logic to partition symbols:
  - **Liquid:** Symbols with rolling 24h quote volume consistently above threshold (e.g., top 422 by volume)
  - **Illiquid:** All other symbols that pass D1's minimum volume band (75K-3M)
- Classification refreshes periodically (weekly or monthly) since liquidity shifts

### Phase 2: Segmented Backfill
- `backfill_d1_opportunities.py` gains `--universe-tier {liquid,illiquid,combined}` flag
- Batch IDs encode tier: `backfill_20260303_D1v1_liquid_XXXX`, `backfill_20260303_D1v1_illiquid_XXXX`
- Three separate backfills, three separate opportunity pools

### Phase 3: Segmented Sim Matrix
- `sim_d1_matrix.py` runs independently per tier
- Each tier finds its own winning config (Config D-Liquid, Config D-Illiquid, Config D-Combined)
- Different optimal stops, TPs, and time windows expected per tier

### Phase 4: Pipeline Topology
- Three D1 nodes on Pipeline page: `D1-Liquid`, `D1-Illiquid`, `D1-Combined`
- Same evaluator code (`D1Evaluator`), different `D1Config` parameters loaded by tier
- Independent biographies, version histories, experiment records, node_impact entries
- Each node's winning config documented atomically in its biography

### Phase 5: Live Signal Worker
- `signal_worker.py` runs D1 evaluator with tier-appropriate config per symbol
- Symbol's tier determined at evaluation time from latest volume classification
- Signals tagged with `universe_tier` in payload for downstream analysis

## Current Validated Results (Production, Combined Universe)

| Config | Description | WR | aggPF | dailyPF | PnL | N | maxDD |
|--------|------------|-----|-------|---------|------|---|-------|
| **D (winner)** | 10% TP, 1.5% stop, 3d TS | 43.4% | 3.63 | **2.70** | $25,426 | 890 | $412 |
| A | 10% TP, 2.5% stop, 3d TS | 22.8% | 1.02 | 1.10 | $1,005 | 2299 | $10,611 |
| B | 15% TP, 2.5% stop, 3d TS | 16.3% | 0.95 | 0.90 | -$2,486 | 2279 | $12,029 |
| C | 20% TP, 2.5% stop, 3d TS | 12.9% | 0.90 | 0.88 | -$5,476 | 2273 | $11,747 |
| E | 10% TP, 2.5% stop, 5d TS | 22.7% | 1.02 | 1.10 | $765 | 2298 | $10,721 |
| F | Runner trail +10%, 3d TS | 21.1% | 0.79 | 0.86 | -$10,142 | 2296 | $14,215 |

### High-quality slices (Config D):
| Slice | WR | aggPF | N |
|-------|-----|-------|---|
| dorm≥0.80 | 47.2% | 4.16 | 405 |
| dorm≥0.90 | 53.4% | 6.08 | 178 |
| dorm≥0.95 | 55.9% | 7.55 | 102 |
| dorm≥0.80, vol_z≥4.0 | 63.0% | 7.46 | 92 |
| dorm≥0.90, vol_z≥4.0 | 75.0% | 14.05 | 48 |
| dorm≥0.95, vol_z≥4.0 | **81.5%** | **24.61** | 27 |

## Key Insight

The dev/prod gap didn't invalidate the result — it revealed that liquid and illiquid microcaps are fundamentally different instruments. Liquid ones have tighter spreads, faster breakouts, more competition. Illiquid ones have wider spreads, slower ignitions, but bigger moves. The optimal stop, TP, and time window are almost certainly different for each. Running one config across both is like using the same rod for river trout and deep sea tuna. Same principle, different calibration.

## Status
- [x] Combined universe validated on production (Config D: daily PF 2.70)
- [ ] Tier classification logic implemented
- [ ] Liquid tier backfill + sim matrix
- [ ] Illiquid tier backfill + sim matrix
- [ ] Pipeline topology (3 nodes)
- [ ] Live signal worker tier routing

## Files
- Production sim output: `Backend/scripts/output/sim_d1_matrix_20260303_135102.json`
- Production sim log: `Backend/scripts/output/sim_d1_matrix_20260303_135102.txt`
- Dev sim output (preliminary): `Backend/scripts/output/sim_d1_matrix_20260303_131113.json`
