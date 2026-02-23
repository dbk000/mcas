# tobemerged/ Manifest — 2026-02-19 Session

Paper trail of all uncommitted work from today's dev session.

---

## What Happened Today

**Two major pipelines built:**

1. **Derivatives pipeline** (Binance Futures) — data collection, IC screening, walk-forward validation, production integration of funding rate as conditioning filter in U1 Unicorn Ignition. This was completed by the previous session.

2. **On-chain data pipeline** (NEW) — complete infrastructure for ingesting on-chain + market data from 6 sources (DefiLlama, CoinGecko, Etherscan/BSCScan, Coinglass, Santiment). Data source assessment, symbol mapping, unified collector with backfill + live modes, DB schema for 11 raw tables + materialized feature table with 31 derived features. Budget: EUR 117/month from EUR 150 allocation.

Also: strategy docs for S3-S7, simulation results, signal genesis research artifacts, and a logging enhancement plan.

---

## PART A: On-Chain Data Pipeline (NEW — this session)

### A1. Assessment & Architecture

| File | What It Is |
|------|-----------|
| `ONCHAIN_DATA_ASSESSMENT.md` | **Deliverable 1+6**: Full data source assessment with coverage, cost, granularity, API quality for 14 sources evaluated. Budget allocation: Coinglass Pro ($79/mo) + Santiment Pro ($49/mo) + free sources. Priority ordering. |

### A2. Schema & Migration

| File | What It Is |
|------|-----------|
| `Backend/docs/db/migrations/20260219_add_onchain_tables.sql` | **Deliverable 2**: 11 raw tables (protocol_tvl, dex_volume, stablecoin_supply, market_metadata, token_holder_stats, liquidations, oi_history_extended, exchange_flows, whale_transactions, network_activity, holder_distribution) + symbol_mapping table + onchain_features_1h materialized table with 31 feature columns |

### A3. Collection Scripts

| File | What It Is |
|------|-----------|
| `Backend/scripts/map_onchain_symbols.py` | Symbol mapper: maps Binance symbols to CoinGecko IDs, Santiment slugs, DefiLlama protocol names, Etherscan/BSCScan contract addresses. Writes to DB + JSON. |
| `Backend/scripts/onchain_collector.py` | **Deliverable 5**: Unified collector with 5 source modules (DefiLlama, CoinGecko, Etherscan, Coinglass, Santiment). ThreadPoolExecutor(20), dedicated DB writer thread, per-source rate limiters, backfill (365d) + live modes, feature materialization. ~1200 lines. |

### A4. Feature Hypotheses

Documented in `ONCHAIN_DATA_ASSESSMENT.md` — 12 Tier 1/2 per-symbol features + 3 Tier 3 market regime features. Highest expected value: exchange_outflow_zscore, liquidation_cascade_score, whale_tx_count_spike, oi_extended_buildup, holder_whale_accumulation.

### A5. Infrastructure

| File | What Changed |
|------|-------------|
| `Backend/docker-compose.yml` | Added `onchain_collector` service (joins `derivatives_collector` already added) |

### A6. API Keys Needed (add to .env)

```
COINGLASS_API_KEY=xxx       # Sign up at coinglass.com, Professional plan ~$79/mo
SANTIMENT_API_KEY=xxx       # Sign up at santiment.net, Pro plan ~$49/mo
ETHERSCAN_API_KEY=xxx       # Free at etherscan.io (optional, higher rate limits)
BSCSCAN_API_KEY=xxx         # Free at bscscan.com (optional, higher rate limits)
COINGECKO_API_KEY=xxx       # Free at coingecko.com (optional, higher rate limits)
```

Free sources (DefiLlama, CoinGecko free tier) work without keys. Paid sources (Coinglass, Santiment) gracefully skip with a log message when keys aren't set.

---

## PART B: Derivatives Pipeline (previous session)

### B1. Data Collection & Integration

| File | What It Is |
|------|-----------|
| `Backend/docs/db/migrations/20260219_add_derivatives_tables.sql` | DB migration: 5 raw tables + 1 materialized feature table (15 features) |
| `Backend/scripts/derivatives_collector.py` | Standalone service: polls Binance Futures API, writes raw tables, materializes hourly features |
| `Backend/scripts/map_futures_symbols.py` | One-off utility: maps spot universe to USDT-M perpetuals |
| `Backend/scripts/futures_symbol_mapping.json` | Output of symbol mapping (370 overlap symbols, 81.7% coverage) |
| `derivatives_funding_filter.patch` | Unified diff for U1 + signal_worker funding integration. `git apply`-ready |

### B2. Modified Production Files

| File | What Changed |
|------|-------------|
| `Backend/worker/app/setups/u1_unicorn_ignition.py` | Added `load_funding_rates()`, funding state fields, post-scoring multiplier logic (1.5x/1.8x) |
| `Backend/worker/app/signal_worker.py` | 3-line addition: load funding rates before U1 trigger eval |

### B3. Validation Scripts + Results

| File | What It Is |
|------|-----------|
| `Backend/scripts/derivatives_signal_test.py` | IC screening of 15 derivatives features. Result: 12/15 survived p<0.01 |
| `Backend/scripts/derivatives_signal_test_results.json` | Top: funding_rate (IC=-0.135), oi_change_pct_24h (IC=-0.111) |
| `Backend/scripts/derivatives_walkforward.py` | 3-test walk-forward validation |
| `Backend/scripts/derivatives_walkforward_results.json` | Test A FAIL (direct feature), Test C PASS (OR=1.537, conditioning filter) |

### B4. Spec Documents

| File | What It Is |
|------|-----------|
| `DERIVATIVES_PIPELINE_SPEC.md` | Build spec for derivatives collection pipeline |
| `DERIVATIVES_WALKFORWARD_SPEC.md` | Walk-forward test design with pseudocode |
| `DERIVATIVES_MERGE_INSTRUCTIONS.md` | 6-step merge procedure |

---

## PART C: Other Work

### C1. Strategy Docs (S3-S7)

| File | Strategy |
|------|---------|
| `S3_RSI_Divergence_Reversal.docx` | RSI divergence reversal |
| `S4_VWAP_Reversion.docx` | VWAP reversion |
| `S5_Support_Resistance_Breakout.docx` | Support/resistance breakout |
| `S6_Trend_Continuation_Pullback.docx` | Trend continuation pullback |
| `S7_Momentum_Breakout_Donchian.docx` | Momentum breakout + Donchian |

### C2. Signal Genesis Research

| Path | What It Is |
|------|-----------|
| `Backend/scripts/signal_genesis_alt4/` | 14 files: alternative signal gen approaches (regime, sparse, vol-gated, event-driven, long-MR, time-series) |
| `Backend/scripts/signal_genesis_ignition/` | 3 files: ignition-specific signal genesis |

### C3. Simulation & Analysis

| Path | What It Is |
|------|-----------|
| `Simulations/` | 24 simulation run CSVs |
| `analysis/sim_runs/` | 6 analyzed simulation runs with features, trades, profiles, MAE/MFE reports |
| `Backend/scripts/sim_run_jarvis.py` | Simulation runner script |

### C4. Other

| File | What It Is |
|------|-----------|
| `logging_plan.txt` | Proposed logging enhancements (5 improvements) |

---

## Full Merge Procedure

### Step 1: Run migrations
```bash
# Derivatives tables
docker exec backend-postgres-1 psql -U trading -d trading -f /docs/db/migrations/20260219_add_derivatives_tables.sql

# On-chain tables
docker exec backend-postgres-1 psql -U trading -d trading -f /docs/db/migrations/20260219_add_onchain_tables.sql
```

### Step 2: Apply derivatives funding filter
```bash
cd /srv/mcas && git apply derivatives_funding_filter.patch
```

### Step 3: Copy new scripts
```bash
cp Backend/scripts/derivatives_collector.py /srv/mcas/Backend/scripts/
cp Backend/scripts/map_futures_symbols.py /srv/mcas/Backend/scripts/
cp Backend/scripts/map_onchain_symbols.py /srv/mcas/Backend/scripts/
cp Backend/scripts/onchain_collector.py /srv/mcas/Backend/scripts/
# Plus validation scripts, results JSONs, symbol mapping JSON
```

### Step 4: Add API keys to .env
```bash
# Edit /srv/mcas/Backend/.env and add:
COINGLASS_API_KEY=xxx
SANTIMENT_API_KEY=xxx
ETHERSCAN_API_KEY=xxx   # optional (free)
BSCSCAN_API_KEY=xxx     # optional (free)
```

### Step 5: Run symbol mapping
```bash
docker compose run --rm -T worker python3 /scripts/map_onchain_symbols.py
```

### Step 6: Backfill on-chain data
```bash
tmux new -s onchain-backfill
docker compose run --rm -T worker python3 /scripts/onchain_collector.py --backfill-only --backfill-days 365
```

### Step 7: Rebuild and restart
```bash
cd /srv/mcas/Backend
docker compose -f docker-compose.yml build signal_worker_0 signal_worker_1
docker compose -f docker-compose.yml up -d --no-deps signal_worker_0 signal_worker_1
# Start collectors
docker compose -f docker-compose.yml up -d derivatives_collector onchain_collector
```

### Step 8: Verify
```bash
# Check derivatives tables
docker exec backend-postgres-1 psql -U trading -d trading -c "SELECT 'derivatives_features_1h', count(*) FROM derivatives_features_1h"

# Check on-chain tables
docker exec backend-postgres-1 psql -U trading -d trading -c "
SELECT 'symbol_mapping' as tbl, count(*) FROM symbol_mapping
UNION ALL SELECT 'protocol_tvl', count(*) FROM protocol_tvl
UNION ALL SELECT 'market_metadata', count(*) FROM market_metadata
UNION ALL SELECT 'onchain_features_1h', count(*) FROM onchain_features_1h;"

# Check collector logs
docker compose -f docker-compose.yml logs --tail=50 onchain_collector
```

**Zero regression risk** — all on-chain features are additive. Signal workers only read derivatives_features_1h (already integrated). On-chain features flow to signal_genesis for IC screening; no live signal changes until walk-forward validates them.
