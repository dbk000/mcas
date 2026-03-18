# Live Ops Stats — Final Implementation Plan (v3.1, Opus 4.6)
_2026-03-18 — Three independent reviews integrated. Governance-compliant._
_Updated: devil's advocate confirmed S9 NameError destroyed ~352 signals TODAY._

## Mission
Get all five strategies (S9, S10, U1, D1, D2) generating live signals correctly,
build a governed rolling stats view + API endpoint, and verify end-to-end with a
post-execution report saved at repo root.

---

## Bug Triage: What's Actually Broken

Three independent reviews (systems reliability, signal quality, devil's advocate)
converged on findings the original Sonnet plan completely missed.

### P0 — Active Bugs (no signals until fixed)

| # | Bug | Impact | Root Cause |
|---|-----|--------|------------|
| **P0-1** | **SNAPSHOT_FIELDS serialization boundary** | S10 and D1 have NEVER fired a live signal. Indicator worker computes all values, but `store_indicators()` only persists fields in `SNAPSHOT_FIELDS` list. 7 critical fields silently dropped. Signal worker reads zeroed defaults from Redis. | `cache_contract.py` SNAPSHOT_FIELDS missing: `macd_line`, `macd_signal`, `macd_histogram`, `macd_prev_histogram`, `sma_20`, `rvol`, `dormancy_score`, `accum_divergence` |
| **P0-2** | **S9 NameError on StrategyRegistry (ACTIVELY DESTROYING SIGNALS)** | S9 breadth was ABOVE 0.30 for 11 hours on March 18 (00:00-10:00 UTC). Klines fetched for 31-33 top-decile symbols each hour. ALL evaluations then crashed with NameError at persist stage. ~352 signals lost TODAY ALONE. `_get_setup_version()` and `_get_setup_label()` reference `StrategyRegistry` which is only locally imported in `__init__`, not at module level. Swallowed by `except Exception` at line 1365, zero crash, zero signal, zero alert. | `signal_worker.py` lines 1101, 1108 — bare `StrategyRegistry` reference with no local import |
| **P0-3** | **social_collector missing entry point** | D2 data feed dead. `collector.py` lost in repo move. Container crash-looping with `No such file or directory`. | Bind mount `./social_collector/app:/app` empty. Files exist in old snapshots. |

### P1 — Architecture Gaps (signals fire but suboptimal)

| # | Issue | Impact |
|---|-------|--------|
| **P1-1** | **Global congestion budget** `SIGNAL_TOP_N_DAILY=10` is one counter for ALL strategies. S9 cross-sectional fires 50+ signals per hourly eval, saturating the budget instantly. S10/D1/U1 get zero. | Raise cap AND make it per-strategy or remove entirely (strategies have their own quality gates) |
| **P1-2** | **D1 state not persisted** — `self._d1_prev_states` is in-memory dict, lost on container restart. D1 needs 48+ hours of 1h bars to build dormancy state. Every restart = cold start. | Checkpoint D1 state to Redis via existing `setup_state_key` infrastructure |
| **P1-3** | **Equities bridge broken** — 6,091 predictions generated since March 2, only 14 bridged to opportunities (99.77% loss). Bridge completes in 0.0s. | Separate investigation — not blocking crypto stats |
| **P1-4** | **3-day total signal blackout (March 11-13)** — zero signals across ALL strategies for 3 consecutive days. Logs from that period deleted after March 17 restart. Cause unknown. Demonstrates the system can fail silently for days with zero detection. | Need alerting infrastructure |
| **P1-5** | **Zero alerting infrastructure** — S9 NameError ran undetected for 36+ hours. No Discord/Telegram/email notification when signals stop. Solo operator with ADHD cannot reliably monitor manually. | Add minimum viable alert |

### Data/View Issues (stats table correctness)

| # | Issue | Fix |
|---|-------|-----|
| **D-1** | **No index for live opps** — seq scan on 1.77GB `opportunities` table (~2s per query) | `CREATE INDEX idx_opps_live_created ON opportunities (created_ts DESC) WHERE batch_id IS NULL;` |
| **D-2** | **Direction casing** — 2 rows uppercase `SHORT`, 338 NULL (EQFP) | Use `LOWER()` in view, exclude EQFP from crypto CTE |
| **D-3** | **payload_json is text not jsonb** — per-row parse cost | Cast in view; long-term: `ALTER COLUMN ... TYPE jsonb` |
| **D-4** | **Fee model simplified** — view uses flat 8bps; real is TF-scaled (5m=19bps, 1h=9bps) | Use `CASE timeframe WHEN '5m' THEN 0.19 WHEN '1h' THEN 0.09 ...` from `execution.py` slippage profile |

---

## Universe Governance Assessment

### Current Architecture
- `KNOWN_UNIVERSE_SIZE = 1024` ← **TypedConfigRegistry** ✅
- `MIN_24H_QUOTE_VOLUME_USD = 5,000,000` ← **TypedConfigRegistry** ✅
- Ingestor sorts ALL Binance USDT pairs by 24h volume, takes top 1024 as Known,
  filters by $5M volume for Monitored (~390 symbols). **Dynamic, registry-governed.** ✅

### The Overlap Problem
- D1 and U1 target microcaps: `MIN_VOLUME_USD = $75K`, `MAX_VOLUME_USD = $3M`
- Monitored universe requires $5M+ volume
- **These ranges do not overlap.** D1's target symbols never reach the signal_worker.
- U1 works around this via `unicorn_pipeline` (self-contained, fetches own candles)
- D1 has NO such escape hatch — it evaluates only monitored symbols, ALL of which
  exceed its $3M volume cap (if the cap is enforced in evaluate_trigger)

### Findings
- `is_d1_excluded()` filters by symbol name (majors, stables, wrapped, leveraged) — **no volume filter**
- D1Config `MIN/MAX_VOLUME_USD` are in TypedConfigRegistry ✅ but where they're
  enforced in the live path needs verification
- Of D1's top 20 backfill symbols, 6 of top 10 are NOT in the monitored universe
- **Action:** Verify whether D1 applies its volume gate inside `evaluate_trigger()`.
  If yes: D1 needs its own pipeline (like unicorn_pipeline) or the universe needs
  a D1-specific lower-volume tier. If no: the SNAPSHOT_FIELDS fix alone unblocks D1.

### Required Governance Checks During Execution
- [ ] Confirm `MIN_24H_QUOTE_VOLUME_USD` is read from TypedConfigRegistry (not hardcoded)
- [ ] Confirm D1/U1 `MIN_VOLUME_USD` and `MAX_VOLUME_USD` are read from their TypedConfigs
- [ ] Confirm ingestor universe refresh is dynamic (hourly full scan + incremental)
- [ ] Confirm all SETUP_IDS are derived from StrategyRegistry, not hardcoded in .env
- [ ] Verify that the stats view fee model reads from execution.py constants (which
  are themselves governed by TypedConfigRegistry)

---

## S10/D1 Atomic Signal Trace

### Why Backtest Worked But Live Doesn't
**Backfills bypass Redis entirely.** `RunExecutor` → `FeatureFactory.compute()` → full
50+ field `IndicatorSnapshot` → evaluator. The Redis serialization boundary doesn't exist.

**Live pipeline has a two-hop architecture:**
1. `indicator_worker` → `FeatureFactory.compute()` → full snapshot → **`store_indicators()`
   filters through SNAPSHOT_FIELDS** → drops 7 fields → Redis
2. `signal_worker` → `read_fresh_snapshot()` → zeroed defaults for missing fields → evaluator

### Trace: S10 (MACD Scalp) — Why It Never Fires
```
indicator_worker computes:
  macd_histogram = -0.00234 (real value)
  macd_prev_histogram = 0.00156 (real value)
  rvol = 3.7 (real value)
  sma_20 = 0.4523 (real value)
         ↓
store_indicators() → SNAPSHOT_FIELDS filter → macd_* NOT in list → DROPPED
         ↓
Redis ind_snap:BTCUSDT:5m → macd_histogram: (empty)
         ↓
signal_worker read_fresh_snapshot() → macd_histogram: 0.0 (default)
         ↓
S10 compute_state():
  macd_crossover_bull = (prev_hist <= 0 AND hist > 0) = (0.0 <= 0 AND 0.0 > 0) = FALSE
  rvol = 0.0 < RVOL_MIN (2.0) = FAIL
  entry_valid = FALSE (always)
         ↓
evaluate_trigger() → returns None → zero signals forever
```

### Trace: D1 (Dormancy Ignition) — Why It Never Fires
```
indicator_worker computes:
  dormancy_score = 0.82 (real value — 82% of 48 bars are dormant)
  accum_divergence = 0.15 (real value — ADL rising, price flat)
         ↓
store_indicators() → SNAPSHOT_FIELDS filter → dormancy_score NOT in list → DROPPED
         ↓
Redis ind_snap:JSTUSDT:1h → dormancy_score: (empty)
         ↓
signal_worker read_fresh_snapshot() → dormancy_score: 0.0 (default)
         ↓
D1 compute_state():
  dormancy_passes = (0.0 >= 0.75) = FALSE
  phase stays at SCANNING (never progresses to DORMANT_CONFIRMED)
         ↓
evaluate_trigger() requires phase in (DORMANT_CONFIRMED, ACCUMULATING)
  → returns None → zero signals forever
```

### The Fix
Add to `SNAPSHOT_FIELDS` in `governance/cache_contract.py`:
```python
# S10 MACD Scalp
"sma_20", "macd_line", "macd_signal", "macd_histogram", "macd_prev_histogram", "rvol",
# D1 Dormancy Ignition
"dormancy_score", "accum_divergence",
```

After restart, verify via:
```bash
docker exec backend-redis-1 redis-cli HGET "ind_snap:BTCUSDT:5m" macd_histogram
# Should return a non-zero float, not (nil)
```

---

## Implementation Plan

### Parallel Execution Groups

The work splits into 4 independent tracks that can execute simultaneously:

```
Track A: P0 Bug Fixes + Infrastructure (code changes)
  A1: Fix SNAPSHOT_FIELDS (cache_contract.py)
  A2: Fix S9 NameError (signal_worker.py)
  A3: Restore social_collector files
  A4: Fix congestion budget — per-strategy (signal_worker.py + config_registry.py)
  A5: Minimum viable alerting (cron + webhook)

Track B: DB Migration (SQL)
  B1: Create partial index for live opps
  B2: Create v_live_opportunity_pnl view
  B3: Create v_live_opportunity_stats view

Track C: API Endpoint (Python)
  C1: New routers/stats.py
  C2: Mount in main.py

Track D: Governance Verification (read-only audit)
  D1: Verify universe is dynamically populated from registry
  D2: Verify D1 volume gate enforcement path
  D3: Verify fee constants are registry-derived
  D4: Verify SETUP_IDS derivation
```

### Track A — P0 Bug Fixes

**A1: SNAPSHOT_FIELDS** (`governance/cache_contract.py`)
- Add 8 missing fields to `SNAPSHOT_FIELDS` list
- Governance: fields must match what `FeatureFactory.compute()` produces and what
  evaluators in `core_logic/` consume. No hardcoded field names that aren't in the
  indicator registry.

**A2: S9 NameError** (`governance/signal_worker.py`)
- Add `from governance.strategy_registry import StrategyRegistry` at module level
  (after bootstrap_genesis() call), OR add local import inside `_get_setup_version()`
  and `_get_setup_label()`.
- Governance: StrategyRegistry is the sole authority for strategy identity. The
  import must be post-bootstrap (registry must be frozen first).

**A3: Restore social_collector** (`Backend/social_collector/app/`)
- Source: `/home/darren/Desktop/MCAS_ss-docs/MCAS/Backend/social_collector/app/`
  (largest file sizes, includes discord_channels.json)
- Copy: collector.py, config.py, symbol_matcher.py, collectors/*.py, *.json
- Verify: Dockerfile entry point matches, DB schema compatible

**A4: Congestion budget** — Must be per-strategy, not global:
- S9 cross-sectional fires 50+ signals per hourly eval when breadth passes
- A global cap of 50 means S9 fills ALL slots in one hour; S10/D1/U1 get zero
- **Fix:** Change Redis key from `signals:daily_count:{date}` to
  `signals:daily_count:{setup_id}:{date}`. Per-strategy cap of 50.
- Governance: the cap must be in TypedConfigRegistry. Add
  `SIGNAL_TOP_N_DAILY_PER_SETUP: int = 50` to WorkerTypedConfig.

**A5: Minimum viable alerting** — Prevent future multi-day silent failures:
- Cron job (every 6 hours) checks
  `SELECT MAX(created_ts) FROM opportunities WHERE batch_id IS NULL`
- If older than 6 hours AND market is open, send alert via webhook
  (Discord/Telegram — user's choice of channel)
- Also alert if any container has restarted more than 3 times in 1 hour
- Governance: alert thresholds in TypedConfigRegistry

### Track B — DB Migration

File: `Backend/docs/db/migrations/20260318_live_opportunity_stats.sql`

```sql
-- B1: Partial index (CRITICAL for performance)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_opps_live_created
    ON opportunities (created_ts DESC)
    WHERE batch_id IS NULL;

-- B2: Base PnL view (computes PnL from payload_json, 100% coverage)
CREATE OR REPLACE VIEW v_live_opportunity_pnl AS
WITH crypto_opps AS (
    SELECT
        id, 'crypto'::text AS asset_class, setup_id, symbol, timeframe,
        created_ts, status, score,
        LOWER(payload_json::jsonb->>'direction') AS direction,
        (payload_json::jsonb->>'entry_price')::double precision AS entry_price,
        (payload_json::jsonb->>'exit_price')::double precision AS exit_price,
        (payload_json::jsonb->>'exit_reason')::text AS exit_reason,
        (payload_json::jsonb->>'exit_ts')::timestamptz AS exit_ts
    FROM opportunities
    WHERE batch_id IS NULL
      AND setup_id NOT IN ('EQFP','EQ_ATC','EQ_VPD','EQ_TSMOM',
                           'EQ_XSMOM','EQ_REV1A','EQ_REV1B','EQ_NVDA_MACRO')
),
with_pnl AS (
    SELECT *,
        CASE WHEN exit_price IS NOT NULL AND entry_price > 0 THEN
            CASE direction
                WHEN 'long'  THEN (exit_price - entry_price) / entry_price * 100
                WHEN 'short' THEN (entry_price - exit_price) / entry_price * 100
            END
            - CASE timeframe  -- TF-scaled roundtrip cost (slippage + 4bps fee)
                WHEN '5m'  THEN 0.19
                WHEN '15m' THEN 0.14
                WHEN '1h'  THEN 0.09
                WHEN '4h'  THEN 0.07
                ELSE 0.08
            END
        END AS pnl_pct_net,
        (exit_ts IS NOT NULL) AS is_closed
    FROM crypto_opps
)
SELECT * FROM with_pnl;

-- B3: Stats rollup view
CREATE OR REPLACE VIEW v_live_opportunity_stats AS
SELECT window, asset_class, setup_id,
       opp_count, closed_count, open_count,
       avg_pnl_pct, win_rate, avg_hours_held
FROM (
    SELECT '24h'::text AS window, asset_class, setup_id,
        COUNT(*) AS opp_count,
        COUNT(*) FILTER (WHERE is_closed) AS closed_count,
        COUNT(*) FILTER (WHERE NOT is_closed) AS open_count,
        ROUND(AVG(pnl_pct_net) FILTER (WHERE is_closed)::numeric, 3) AS avg_pnl_pct,
        ROUND((COUNT(*) FILTER (WHERE is_closed AND pnl_pct_net > 0)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE is_closed), 0)), 3) AS win_rate,
        ROUND(AVG(EXTRACT(EPOCH FROM (exit_ts - created_ts)) / 3600)
            FILTER (WHERE is_closed)::numeric, 1) AS avg_hours_held
    FROM v_live_opportunity_pnl
    WHERE created_ts >= NOW() - INTERVAL '24 hours'
    GROUP BY asset_class, setup_id

    UNION ALL

    SELECT '7d'::text, asset_class, setup_id,
        COUNT(*),
        COUNT(*) FILTER (WHERE is_closed),
        COUNT(*) FILTER (WHERE NOT is_closed),
        ROUND(AVG(pnl_pct_net) FILTER (WHERE is_closed)::numeric, 3),
        ROUND((COUNT(*) FILTER (WHERE is_closed AND pnl_pct_net > 0)::numeric
            / NULLIF(COUNT(*) FILTER (WHERE is_closed), 0)), 3),
        ROUND(AVG(EXTRACT(EPOCH FROM (exit_ts - created_ts)) / 3600)
            FILTER (WHERE is_closed)::numeric, 1)
    FROM v_live_opportunity_pnl
    WHERE created_ts >= NOW() - INTERVAL '7 days'
    GROUP BY asset_class, setup_id
) sub;
```

Also add a `30d` and `all_time` window to the UNION ALL so the dashboard shows
historical performance alongside the recent windows. A dashboard of zeros is
demoralizing for a solo operator — historical aggregates (30,675 opps, 63.7%
win rate on S9) prove the system works even when current market is quiet.

Run: production first (`backend-postgres-1`), then dev. Views are idempotent + non-destructive.

### Track C — API Endpoint

File: `Backend/api/app/routers/stats.py`

- `GET /v1/stats/opportunities` — queries `v_live_opportunity_stats`, 60s TTL cache
- Response envelope: `{data: {windows: {24h: [...], 7d: [...]}, pipeline_status: {...}}, meta: {...}}`
- Pipeline status derived from Redis heartbeats (not container inspection)
- All setup_ids derived from StrategyRegistry (no hardcoded list)
- Fee schedule and timeframe map from TypedConfigRegistry
- Mount in `main.py` with auth gate

### Track D — Governance Audit

Read-only verification during execution:
- [ ] `MIN_24H_QUOTE_VOLUME_USD` sourced from `get_config().Worker.MIN_24H_QUOTE_VOLUME_USD`
- [ ] Ingestor refreshes universe dynamically (confirmed: hourly full scan at line 755)
- [ ] D1/U1 `MIN_VOLUME_USD`/`MAX_VOLUME_USD` from their TypedConfig dataclasses
- [ ] `SETUP_IDS` env var → loaded into TypedConfigRegistry → StrategyRegistry populated from it
- [ ] Fee constants in view match `execution.py` `SLIPPAGE_PROFILE_BPS` + 4bps taker fee
- [ ] SNAPSHOT_FIELDS addition is consistent with `FeatureFactory.compute()` output
- [ ] No new hardcoded values introduced anywhere

---

## Deploy Sequence

```
1. [Tracks A+B+C+D in parallel — code changes + SQL + audit]

2. Commit all changes (single commit)

3. Push to GitHub → git pull on /srv/mcas

4. Run migration on production:
   cat docs/db/migrations/20260318_live_opportunity_stats.sql \
     | docker exec -i backend-postgres-1 psql -U trading -d trading

5. Rebuild + restart affected services (order matters):
   docker compose -f docker-compose.yml build indicator_worker_0 indicator_worker_1 \
     signal_worker_0 signal_worker_1 social_collector api
   docker compose -f docker-compose.yml up -d --no-deps \
     indicator_worker_0 indicator_worker_1 \
     signal_worker_0 signal_worker_1 \
     social_collector api

6. Verify (within 5 minutes of restart):
   # SNAPSHOT_FIELDS fix
   docker exec backend-redis-1 redis-cli HGET "ind_snap:BTCUSDT:5m" macd_histogram
   docker exec backend-redis-1 redis-cli HGET "ind_snap:BTCUSDT:1h" dormancy_score

   # S9 NameError fix (wait for hourly eval, check no errors)
   docker compose logs --tail=50 signal_worker_0 | grep -i error

   # social_collector not crash-looping
   docker compose logs --tail=10 social_collector

   # API endpoint
   curl -s -H "X-API-Key: $API_KEY" https://api.mcasui.com/v1/stats/opportunities | python3 -m json.tool

7. Save post-execution report to repo root
```

---

## Post-Execution Report

After all tasks complete, save `post_execution_report.md` at repo root with:

1. **Bug fixes applied** — exact file:line changes, before/after
2. **Redis verification** — actual HGET outputs for each new SNAPSHOT_FIELD
3. **Signal worker logs** — first 10 minutes after restart, any errors
4. **API endpoint response** — actual JSON from `/v1/stats/opportunities`
5. **Governance audit results** — each checklist item with PASS/FAIL/FINDING
6. **Universe assessment** — current known/monitored counts, D1 eligibility analysis
7. **Outstanding issues** — anything deferred (equities bridge, D1 pipeline, D1 state persistence)
8. **Signal generation status** — per-strategy: firing/not-firing/blocked-by-market

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| SNAPSHOT_FIELDS addition causes indicator_worker memory spike | LOW | MED | 8 extra floats per snapshot × 390 symbols = negligible |
| S9 generates flood after NameError fix + breadth recovers | MED | LOW | Daily budget (raised to 200) provides safety valve |
| social_collector snapshot has stale Telegram session | MED | MED | Check `sessions/` directory; may need manual re-auth |
| D1 never fires even with fix (market too quiet) | HIGH | LOW | Honest reporting. Stats table shows 0. That's valid. |
| View performance degrades as opportunities grow | MED | MED | Partial index + 60s cache. Monitor `compute_ms` in response. |
| Direction casing introduces more variants over time | LOW | LOW | `LOWER()` handles all cases |
| D1 volume gate rejects all monitored symbols | MED | HIGH | Verified: `is_d1_excluded()` has NO volume gate. SNAPSHOT_FIELDS is the sole blocker. |
