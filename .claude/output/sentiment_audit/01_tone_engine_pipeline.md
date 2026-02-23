# Tone Engine Core Pipeline -- Deep Atomic Audit

**Auditor:** Claude Opus 4.6
**Date:** 2026-02-22
**Scope:** All tone engine files in `/home/darren/Desktop/MCAS/MCAS_sentiment/`
**Reference standard:** `/home/darren/Desktop/MCAS/CLAUDE.md` (Hard Nevers, Validation Standards, Architecture)

---

## 1. DB SCHEMA

### 1.1 Tone Engine Tables (new, in separate Postgres 15 instance)

The tone engine runs its **own** Postgres 15 database (`tone_engine` on port 5433), completely separate from the main MCAS Postgres 16 (`trading` on port 5432). This is architecturally significant -- it means zero risk of contaminating production, but also means all data must be mirrored from production into the tone engine DB.

#### Migration: `20260222_tone_phase_state_engine_core.sql`

| Table | PK | Columns | Indexes | Notes |
|---|---|---|---|---|
| `agent_notes` | UUID (gen_random_uuid) | ts, actor, topic, level (CHECK: INFO/WARN/ERROR), message, data (JSONB) | (actor, topic) | Structured agent logging. Good practice. |
| `agent_artifacts` | UUID (gen_random_uuid) | ts, actor, artifact_type, artifact_ref, data (JSONB) | (actor, artifact_type) | Artifact registry for outputs. |
| `social_tone_posts` | BIGINT post_id | symbol, ts, source, lang, informality_score (REAL, 0-1), tone_vec (REAL[]), stance_vec (REAL[]), quality_flags (JSONB), created_at | (symbol, ts), (ts) | **BUG: post_id is BIGINT PRIMARY KEY with no default/sequence.** Callers must supply IDs. No UNIQUE constraint on (source, source_id) like the mirror social_posts table. |
| `social_tone_windows` | (symbol, window_end_ts, window_minutes) composite PK | cutoff_ts, n_posts, n_authors, tone_mean (REAL[]), tone_cov_diag (REAL[]), stance_mean (REAL[]), informality_mean, dup_ratio, author_concentration, novelty_score, created_at | (window_end_ts), (symbol, window_end_ts) | Rolling aggregate windows. Well-designed composite PK. |
| `social_tone_cluster_models` | UUID (gen_random_uuid) | ts, window_minutes, k, tone_dim, stance_dim, centroids (REAL[][]), notes, params (JSONB) | None beyond PK | Cluster model registry. |
| `social_tone_cluster_assignments` | (symbol, window_end_ts, window_minutes, model_id) | cluster_id, cluster_prob, mixture (REAL[]) | (model_id, window_end_ts) | FK to cluster_models with CASCADE. |
| `social_tone_states` | (symbol, ts, window_minutes, model_id) | state_id, p_state, p_transition, transition_from, transition_to, created_at | (symbol, ts) | State machine tracking. FK to cluster_models. |
| `ohlcv_regimes` | (symbol, timeframe, bar_ts) | regime, regime_conf, change_score, created_at | (symbol) | Market regime labels. |
| `tone_signal_metrics` | (model_id, window_minutes, timeframe, delay_minutes, horizon_minutes, signal_def) | n_events, avg_mfe, avg_mae, p_dump, expected_return, sharpe_like, notes, created_at | None beyond PK | **WARNING: 6-column composite PK where one column is JSONB (`signal_def`).** JSONB equality is byte-for-byte, making this PK fragile -- key ordering in JSON matters for conflict detection. |
| `tone_short_alerts` | UUID (gen_random_uuid) | symbol, created_ts, effective_ts, model_id (FK), window_minutes, timeframe, confidence, expected_edge, rationale (JSONB), status (CHECK: OPEN/EXPIRED/TRIGGERED) | (symbol) | Alert table. Missing index on status. |
| `schema_versions` | filename (TEXT) | checksum, created_at | None beyond PK | Migration tracker. |

#### Migration: `20260222_tone_engine_source_mirror_tables.sql`

| Table | PK | Notes |
|---|---|---|
| `social_posts` (mirror) | BIGINT id | Mirror of production social_posts. Has UNIQUE(source, source_id). No sequence by default (added by separate migration). |
| `ohlcv` (mirror) | BIGINT id | Mirror of production OHLCV. Has UNIQUE(symbol, timeframe, bar_start_ts, source). |
| `tone_ingestion_state` | stream (TEXT) | Cursor tracking for incremental ingestion. cursor_id, cursor_ts, state (JSONB). |

#### Migration: `20260222_tone_engine_social_posts_id_default.sql`

Adds a sequence (`social_posts_id_seq`) and sets it as the default for `social_posts.id`. Seeds from MAX(id). This fixes the "no default" issue on the mirror table for locally-generated rows.

### 1.2 Pre-existing Social Tables (in main MCAS schema via `20260219_add_social_tables.sql`)

| Table | Notes |
|---|---|
| `social_posts` | BIGSERIAL PK. Source of truth in production MCAS DB. |
| `social_features_1h` | Hourly aggregated features (mention_count, unique_authors, velocities, etc). PK: (symbol, bar_start_ts). |
| `social_signals` | LLM-generated signal vectors (catalyst detection, narrative analysis). |
| `symbol_aliases` | Ticker-to-symbol mapping with ambiguity flags. PK: (symbol, alias). |

### 1.3 Comparison to Main MCAS Schema (`/home/darren/Desktop/MCAS/Backend/docs/db/schema/mcas_schema.sql`)

**Key differences:**

1. **Separate DB instance.** Tone engine uses Postgres 15 on port 5433. Main MCAS uses Postgres 16 on port 5432. This creates data isolation but requires the ingestion pipeline to mirror data.

2. **OHLCV schema mismatch.** Main MCAS: `id BIGSERIAL PRIMARY KEY` (auto-increment). Tone engine mirror: `id BIGINT PRIMARY KEY` (no auto-increment -- expects mirrored IDs from production). The mirrored OHLCV table uses integer PK from source, which means ON CONFLICT (id) upserts in the ingestion scripts depend on production IDs being stable. This is correct for a mirror.

3. **social_posts does not exist in main MCAS schema.** The `social_posts` table is defined only in migration `20260219_add_social_tables.sql`, not in the base `mcas_schema.sql`. This means social data is an add-on, not part of the core schema.

4. **No Redis integration.** The tone engine uses Postgres-only storage. No Redis ZSETs, no pub/sub. This means it cannot feed real-time signals to the existing MCAS pipeline without additional integration work.

5. **No `opportunities` table equivalent.** The tone engine generates `tone_short_alerts` but has no path to the MCAS `opportunities` pipeline that drives simulation/paper trading.

6. **Postgres version downgrade.** Tone engine uses Postgres 15; main MCAS uses Postgres 16. No functional issue but inconsistent.

---

## 2. DOCKER SERVICES

### File: `docker-compose.tone-engine.yml`

```yaml
services:
  tone_engine_postgres:
    image: postgres:15
    container_name: tone-engine-postgres
    restart: unless-stopped
    ports:
      - "5433:5432"
    environment:
      POSTGRES_DB: tone_engine
      POSTGRES_USER: tone_user
      POSTGRES_PASSWORD: tone_engine_secret
    volumes:
      - /mnt/HC_Volume_104727149/mcas_tone_engine_pgdata:/var/lib/postgresql/data
```

### File: `.env.tone-engine`

```
DATABASE_URL=postgresql://tone_user:tone_engine_secret@localhost:5433/tone_engine
```

**Assessment:**

- **Minimal composition.** Only a single Postgres container. No worker, no Redis, no API. All scripts are run ad-hoc rather than as persistent services.
- **Password in plaintext.** `tone_engine_secret` is hardcoded in both docker-compose and .env. Not production-grade but acceptable for a research DB.
- **Volume mount.** Uses the dedicated SSD volume (`HC_Volume_104727149`), which is the same volume referenced in the main MCAS data migration. This is good -- keeps data off the boot disk.
- **No network configuration.** No explicit Docker network, so it uses the default bridge. The tone engine Postgres is not on the same Docker network as the main MCAS stack. Scripts connect via `localhost:5433` from the host, which works.
- **No health check.** Missing Docker healthcheck for the Postgres container.
- **No resource limits.** No memory/CPU limits defined.

---

## 3. PIPELINE FLOW

### 3.1 Data Flow Diagram

```
Production MCAS DB (Postgres 16, port 5432)
    |
    |-- [tone_engine_ingest_sources.py] --> Mirrors social_posts + ohlcv
    |-- [tone_engine_backfill_sharded.py] --> Sharded bulk mirror (4 parallel workers)
    |-- [tone_engine_reddit_backfill_sharded.py] --> Direct Reddit API ingestion
    |-- [tone_engine_expand_socials.py] --> Enriches from equities_raw_mentions
    |
    v
Tone Engine DB (Postgres 15, port 5433)
    |
    |-- social_posts (mirrored + enriched)
    |-- ohlcv (mirrored)
    |-- tone_ingestion_state (cursors)
    |
    |-- [tone_engine_forward_predictor_discovery.py]
    |       |
    |       |-- Reads social_posts + ohlcv from tone engine DB
    |       |-- Builds (timestamps x symbols) matrices
    |       |-- Engineers 48 features (20 basic + 28 extended)
    |       |-- Computes forward return targets with execution delay
    |       |-- Evaluates each feature via cross-sectional Spearman IC
    |       |-- Ranks features by composite predictor_score
    |       |-- Writes results to CSV + tone_signal_metrics table
    |       |
    |       v
    |   tone_signal_metrics (persisted rankings)
    |   tone_short_alerts (not yet populated by any code)
    |
    v
[NO INTEGRATION TO MAIN MCAS PIPELINE]
```

### 3.2 Ingestion Scripts

#### `tone_engine_ingest_sources.py`
- **Purpose:** Incremental mirror of social_posts + OHLCV from production to tone engine DB.
- **Source:** Production DB via SOURCE_DATABASE_URL.
- **Target:** Tone engine DB via DATABASE_URL.
- **Mechanism:** Cursor-based pagination (by id ASC), upsert on conflict.
- **Batching:** social=5000 rows, ohlcv=20000 rows per batch.
- **State:** Persisted in `tone_ingestion_state`.

#### `tone_engine_backfill_sharded.py`
- **Purpose:** Historical bulk backfill with parallelism via sharding.
- **Sharding:** `(id % shards) = shard` for even distribution across workers.
- **Design:** Run 4 instances (shard 0-3) concurrently for 4x throughput.

#### `tone_engine_reddit_backfill_sharded.py`
- **Purpose:** Direct Reddit ingestion via Arctic Shift / PullPush APIs.
- **Sharding:** Distributes subreddits across shards (not rows).
- **Dependencies:** Imports `RedditBackfiller`, `SymbolMatcher`, `load_symbol_aliases_direct` from `backfill_social_posts.py`.
- **Extra subreddits:** 70+ subreddits including crypto, equities, macro, tech.

#### `tone_engine_expand_socials.py`
- **Purpose:** Pull additional social content from `equities_raw_mentions` table.
- **Synthetic expansion disabled:** `expand_with_ohlcv_synthetic()` raises RuntimeError("Synthetic social expansion is permanently disabled"). Good -- no fabricated data.
- **Negative IDs:** Uses negative BIGINT IDs to avoid collision with production-mirrored positive IDs. Clever but fragile -- any code that assumes id > 0 will break.

### 3.3 Analysis Script

#### `tone_engine_forward_predictor_discovery.py` (1096 lines)

This is the main research script. Detailed analysis in Section 4.

---

## 4. FORWARD PREDICTOR DISCOVERY -- METHODOLOGY ANALYSIS

### 4.1 Feature Engineering (48 features)

**Basic set (20 features):**
- Price: ret_1h, ret_6h, ret_24h, range_pct, vol_ratio_24, realized_vol_24, trend_24, trend_72, vol_regime_24_72
- Social: mentions_1h/6h/24h, authors_6h/24h, attention_ratio_6h, mention_accel_6h, posts_per_author_6h, text_len_6h, exclaim_6h, cashtag_6h

**Extended set (28 additional):**
- Z-scores: z_ret_6h, z_ret_24h, z_mentions_6h/24h, z_attention_6h, z_vol_ratio_24, z_realized_vol_24
- Author structure: author_diversity_24h, author_concentration_24h, mention_author_gap_24h
- Surprise: surprise_mentions_1h/6h/24h
- Divergence: social_price_divergence_6h/24h, attention_vs_volume_gap, attention_vs_volatility_gap
- Interaction: crowding_pressure, panic_chatter, ticker_frenzy, verbosity_pressure
- Cross: social_reversion_pressure, social_trend_pressure, accel_reversion_pressure, accel_trend_pressure
- Coupling: vol_attention, range_attention, volume_attention, momentum_attention

**Assessment:** The feature engineering is genuinely first-principles. The interaction features (social x price divergences, crowding pressure) are creative and not standard TA. The z-score normalization over rolling windows is correct. The author-structure features (diversity, concentration, novelty) are novel.

### 4.2 Target Construction

```python
entry = lead(close_mat, delay_steps)  # execution delay (default 30 min = 1 bar at 1h)
exit_px = lead(close_mat, delay_steps + h)  # horizon after delay
target = exit_px / entry - 1.0  # forward return
```

- **Delay-aware:** Correctly accounts for execution latency (30 min default).
- **Horizons:** Default 6, 24, 72 bars (6h, 24h, 72h at 1h timeframe).
- **No transaction costs in target.** The forward returns are gross, not net. This is acceptable for a discovery/screening phase, but any feature that passes screening must be re-evaluated with the Section 4.4 cost model (4bps taker + slippage).

### 4.3 Evaluation Methodology

```python
def evaluate_feature(feature_mat, target_mat, *, min_cs_samples, min_observations):
```

For each feature x horizon combination:

1. **Overall IC:** Spearman correlation on a random sample of up to 100k observations (pooled across all timestamps and symbols).
2. **Cross-sectional IC:** For each timestamp, compute Spearman IC between feature values and forward returns across symbols. Requires min 12 symbols per timestamp.
3. **Decile analysis:** Top/bottom decile of feature values, compute mean forward return for short side.
4. **Composite score:** `predictor_score = max(0, chosen_short_ret) * abs(mean_ic) * log1p(max(1, n_events)) * 10000`

**Assessment -- CRITICAL GAPS:**

1. **NO WALK-FORWARD VALIDATION.** This is the most serious issue. The entire analysis is in-sample. Features are evaluated on the full dataset without any train/test split, purged folds, or holdout. This violates CLAUDE.md Section 4.1 ("Walk-forward validation is mandatory. No in-sample results reported as findings.") **Every result from this script is potentially overfit.**

2. **NO DSR CORRECTION.** With 48 features x 3 horizons = 144 hypothesis tests, multiple testing correction is essential. No Deflated Sharpe Ratio, no Bonferroni, no FDR control. Violates CLAUDE.md Section 4.3.

3. **NO TRANSACTION COST MODEL.** Features are ranked by gross forward returns. No fee/slippage deduction. A feature showing +5 bps edge at 1h timeframe needs 5+4=9 bps just to break even (5bps slippage + 4bps taker). Violates CLAUDE.md Section 4.4.

4. **NO AVERAGE DAILY PF.** The success metric in CLAUDE.md Section 4.5 is "average daily Profit Factor > 1.0". This script computes Sharpe-like ratios and mean short returns but never computes PF, let alone daily PF.

5. **Random subsampling introduces variance.** `np.random.choice(idx, 100_000, replace=False)` with no seed means results are non-reproducible. The overall IC estimate changes between runs.

6. **Cross-sectional IC conflates regime effects.** Computing IC across all timestamps without regime conditioning means a feature that works only in bull markets will show moderate IC in a mixed-regime sample, masking the fact that it fails (or inverts) in bear markets.

### 4.4 Vectorization Quality

The feature computation is **excellently vectorized**. All operations use numpy matrix operations on (timestamps x symbols) arrays:

- `rolling_sum`, `rolling_mean`, `rolling_std` -- all implemented as cumsum-based numpy operations, no Python loops.
- `lag()`, `lead()` -- pure numpy slicing.
- `rolling_zscore` -- composed from rolling_mean and rolling_std.
- Feature interactions -- all element-wise numpy operations.

**One exception:** `_rankdata()` contains a Python `while` loop for tie handling (lines 467-477). This loop processes ties in sorted data, so the iteration count is bounded by the number of distinct values, not total observations. For typical financial data this is acceptable, but for features with many ties (e.g., mention_count which has many zeros), this could be slow. The fix would be `scipy.stats.rankdata`, but avoiding scipy is a reasonable dependency choice.

The `evaluate_feature` function has a Python `for i in range(feature_mat.shape[0])` loop over timestamps (line 725). With ~8000 timestamps at 1h over 1 year, this is moderate. Inside the loop, operations are vectorized across symbols. This is the correct design -- the cross-sectional analysis requires per-timestamp computation, and the inner operations are numpy.

### 4.5 Social Data Aggregation

The `load_social_hourly` function aggregates social posts at the database level:

```sql
SELECT sym, date_trunc('hour', p.post_ts), COUNT(*), COUNT(DISTINCT author),
       AVG(char_length(content)), AVG(CASE WHEN content ~ '[!]{2,}' THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN content ~ '\\$[A-Za-z]...' THEN 1.0 ELSE 0.0 END)
FROM social_posts p CROSS JOIN LATERAL unnest(symbols) AS sym
```

**Issues:**

- `CROSS JOIN LATERAL unnest(symbols)` means a post mentioning 5 symbols creates 5 rows. This inflates mention counts and dilutes author uniqueness metrics per symbol. This is actually the correct behavior for a "how much attention is this symbol getting?" metric, but should be documented.
- The regex `[!]{2,}` for exclaim rate detects `!!` but not single `!`. This is intentional (filters normal punctuation) but aggressive.

---

## 5. CODE QUALITY ASSESSMENT

### 5.1 Strengths

1. **Retry infrastructure is excellent.** Every script has `RetryPolicy` + `run_with_retry` with exponential backoff + jitter. RETRYABLE_SQLSTATES are correctly identified. This is production-grade error handling.

2. **Agent logging pattern.** All scripts log to `agent_notes` and `agent_artifacts` tables. This creates a persistent audit trail that survives crashes. Follows the corrections log entry "2026-02-06 -- Persist Audit Trail vs. Ephemeral Data".

3. **Incremental ingestion with cursor state.** `tone_ingestion_state` tracks cursor position per stream. Restartable after failures without re-processing.

4. **Batched writes.** All upserts use batched `executemany`, not single-row inserts. Inter-batch sleeps prevent overwhelming the DB. Follows "NEVER accumulate all results in memory for single bulk write".

5. **Progress logging.** Backfill scripts log every 20 batches. Discovery script logs top-10 results at completion. Follows "Every long-running script must emit periodic progress logs".

6. **Sharding design.** The backfill scripts support parallel execution via `(id % shards) = shard`. Work units are balanced by ID distribution (random across data), not by estimated cost, but this is acceptable for row-scanning workloads.

7. **Synthetic data permanently disabled.** `expand_with_ohlcv_synthetic` raises RuntimeError. This prevents data contamination.

### 5.2 Bugs

1. **`social_tone_posts.post_id` has no sequence/default.** The table is defined with `BIGINT PRIMARY KEY` but no DEFAULT. Code that inserts tone-analyzed posts must supply IDs. No code in the codebase actually writes to this table, suggesting it was designed but never wired up.

2. **`tone_signal_metrics` has JSONB in composite PK.** The 6-column PK includes `signal_def JSONB`. JSONB equality in Postgres is byte-for-byte comparison. If the same signal definition is serialized with different key ordering, it creates duplicate rows instead of upserting. The `persist_metrics` function controls serialization so this is unlikely to manifest, but it is architecturally fragile. A better design would be a hash of signal_def as a TEXT column in the PK.

3. **`tone_engine_reddit_backfill_sharded.py` line 20:** `from backfill_social_posts import (RedditBackfiller, SymbolMatcher, load_symbol_aliases_direct)`. This import assumes `backfill_social_posts.py` is in the Python path. When run via Docker (`python3 /scripts/tone_engine_reddit_backfill_sharded.py`), this will fail unless `/scripts` is in `PYTHONPATH` or the CWD is `/scripts`. No execution context detection (violates Hard Never: "NEVER hardcode import paths -- detect execution context at runtime").

4. **`backfill_social_posts.py` line 592:** `cur.executemany(sql, feature_rows[i:i + batch_size])` -- uses `executemany` with tuples, not dicts. The SQL uses positional `%s` placeholders. This works but is fragile if column order changes. The tone engine scripts correctly use `%(name)s` parameterized dicts.

5. **`tone_engine_backfill_sharded.py` line 653:** `cur.execute(f"SELECT COUNT(*) FROM public.{table}")` -- SQL injection via f-string table name. The `table` parameter comes from `count_rows(conn, retry, "social_posts")` which is always a string literal in the calling code, so this is not exploitable in practice, but it is still an anti-pattern. The `ingest_sources` version (line 572) correctly uses `psycopg.sql.Identifier`.

6. **No connection pooling.** All scripts use single persistent connections (`psycopg.connect()`). For the ingestion scripts that do both reads (source) and writes (target), this is fine. But the connections are never refreshed -- if a connection goes stale mid-run, the retry logic will catch it, but a fresh connection is never established. The `run_with_retry` retries the operation on the same (possibly dead) connection.

7. **`evaluate_feature` returns `None` for insufficient data.** The caller silently skips these features (line 1023: `if metrics is None: continue`). No logging of how many features were skipped due to insufficient data. This makes it hard to diagnose data coverage issues.

### 5.3 Anti-patterns

1. **Massive code duplication across scripts.** `RetryPolicy`, `run_with_retry`, `is_retryable_db_error`, `connect_db`, `log_agent_note`, `log_agent_artifact`, `table_exists`, `get_state_cursor`, `upsert_state_cursor` are copy-pasted identically across all 5 tone engine scripts. These should be in a shared module.

2. **No type annotations on `run_with_retry` return type.** The function returns `Any` because it wraps arbitrary callables. This makes static analysis impossible.

3. **`FallbackTickerMatcher` in reddit backfill is very aggressive.** Any 2-5 character uppercase token that is not in TICKER_STOPWORDS is treated as a ticker. Words like "BEST", "HUGE", "LONG", "PUMP" would be matched as tickers. The stopword list has 30 entries but common English 2-5 letter uppercase words number in the thousands. This will produce many false positive symbol matches.

4. **`log_note` in `tone_engine_reddit_backfill_sharded.py` creates a new connection per call.** `psycopg.connect(db_url)` on every note insert (lines 105-116). With progress logging per subreddit, this creates ~70 short-lived connections. The other scripts reuse a persistent connection.

---

## 6. INTEGRATION POINTS

### 6.1 Shared Resources

| Resource | Main MCAS | Tone Engine | Shared? |
|---|---|---|---|
| Postgres | Port 5432, `trading` DB, Postgres 16 | Port 5433, `tone_engine` DB, Postgres 15 | NO -- separate instances |
| Redis | Port 6379 | Not used | NO |
| Docker network | `backend_default` | Default bridge | NO |
| SSD Volume | `/mnt/HC_Volume_104727149/mcas_pgdata` | `/mnt/HC_Volume_104727149/mcas_tone_engine_pgdata` | Same physical disk, different directories |
| OHLCV data | Source of truth | Mirrored copy | One-way replication |
| Social data | Source of truth | Mirrored + enriched | One-way + augmentation |

### 6.2 Missing Integration

1. **No path from tone_signal_metrics to MCAS opportunities.** The discovery script finds features with predictive power, but there is no:
   - Setup class (`SetupBase` subclass) that uses these features
   - Signal worker that reads tone data
   - Opportunity generation pipeline
   - Integration with the existing signal_engine.py

2. **No real-time pipeline.** All scripts are batch/offline. There is no:
   - Streaming social data ingestion
   - Real-time tone window computation
   - Live signal generation from tone state changes

3. **No Redis bridge.** The main MCAS pipeline uses Redis ZSETs for ranked signals. Tone engine has no Redis interaction.

4. **tone_short_alerts table exists but nothing writes to it.** The schema defines it, but no script in the codebase populates it.

5. **social_tone_posts table exists but nothing writes to it.** The schema defines the tone analysis output table, but no NLP/tone analysis code exists. The "tone" in "tone engine" appears to be aspirational -- the actual pipeline computes social attention metrics (mention counts, author diversity), not linguistic tone analysis.

---

## 7. HARD NEVER VIOLATIONS

### 7.1 Confirmed Violations

| Hard Never Rule | Violation | File:Line | Severity |
|---|---|---|---|
| "Walk-forward validation is mandatory" (Section 4.1) | No walk-forward, no holdout, no train/test split. All analysis is in-sample. | `tone_engine_forward_predictor_discovery.py` (entire evaluate_feature) | **CRITICAL** |
| "DSR > 0 required for confirmation" (Section 4.3) | No DSR correction despite 144 hypothesis tests. | `tone_engine_forward_predictor_discovery.py` | **CRITICAL** |
| "Slippage scales by timeframe" (Section 4.4) | No transaction cost model applied. | `tone_engine_forward_predictor_discovery.py` | **HIGH** |
| "Average daily PF > 1.0" (Section 4.5) | PF never computed. | `tone_engine_forward_predictor_discovery.py` | **HIGH** |
| "NEVER hardcode import paths" (Section 2) | `from backfill_social_posts import ...` without path detection. | `tone_engine_reddit_backfill_sharded.py:20` | **MEDIUM** |
| "NEVER accumulate all results in memory for single bulk write" (Section 2) | `materialize_features_bulk` loads ALL social_posts into memory (`cur.fetchall()`), computes ALL features, then bulk upserts. | `backfill_social_posts.py:480-597` | **MEDIUM** |
| "NEVER use bare `docker compose up`" (Section 2, Infra) | `docker-compose.tone-engine.yml` has no explicit `-f` usage documented. | docker-compose.tone-engine.yml | **LOW** |

### 7.2 Rules Correctly Followed

| Rule | Evidence |
|---|---|
| "NEVER act on forming candles" | OHLCV queries filter `is_final = TRUE` |
| "NEVER key OHLCV by asset alone" | All OHLCV queries include timeframe |
| "Binance timestamps are milliseconds" | No direct Binance API calls in tone engine; timestamps come from mirrored data already in correct format |
| "NEVER apply live dedup logic to simulation/backtest queries" | OHLCV dedup uses ROW_NUMBER() over (PARTITION BY symbol, bar_start_ts) which is correct for getting latest version |
| "Use `%s * INTERVAL '1 day'` not `INTERVAL '%s days'`" | No INTERVAL parameterization in tone engine scripts |
| "Write to DB in batches" | All ingestion scripts use batched executemany with inter-batch sleep |
| "Emit periodic progress logs" | All scripts log batch progress |
| Half-open range boundaries `[start, end)` | All date queries use `>= start AND < end` consistently |

---

## 8. ADDITIONAL OBSERVATIONS

### 8.1 The "Tone" in Tone Engine is Aspirational

The name "tone engine" suggests NLP-based tone/sentiment analysis. But:
- `social_tone_posts` (the tone analysis output table) is never written to by any script.
- No NLP model, no sentiment classifier, no tone extraction code exists.
- The actual pipeline computes quantitative social metrics: mention counts, author diversity, attention ratios, exclaim rates.
- The `tone_vec` and `stance_vec` REAL[] columns in `social_tone_posts` suggest a vector-space sentiment model was planned but never implemented.
- The cluster models (`social_tone_cluster_models`, `social_tone_cluster_assignments`, `social_tone_states`) represent a state-machine approach to regime detection from tone vectors, but no code populates these tables either.

What exists is a **social attention engine**, not a tone engine. The discovery script's features are all attention-based (mention counts, author counts, surprise ratios) rather than tone-based (positive/negative sentiment, urgency, fear).

### 8.2 The fortim Directory is Unrelated

The `fortim/` directory contains a football (soccer) event data pipeline (`vision22` schema). This is completely unrelated to tone/sentiment analysis and appears to be a separate project that was accidentally included in this repo clone.

### 8.3 Equities Data Cross-Pollination

`tone_engine_expand_socials.py` pulls from `equities_raw_mentions` -- a table for equity (stock) market social data. This means the tone engine intentionally crosses the crypto/equities boundary. The expanded social posts are tagged as `source="equities_raw_mentions"`. This is fine for research but means the social feature distributions will be different from pure-crypto data.

### 8.4 Spearman IC Implementation

The custom `spearman_corr` and `_rankdata` implementations (lines 462-491) avoid scipy dependency. The implementation is correct but has O(n log n) sort + O(n) tie resolution. For the typical cross-sectional analysis (12-120 symbols per timestamp), this is efficient. For the pooled overall IC (100k samples), scipy would be faster due to C implementation, but the random subsampling keeps n manageable.

---

## 9. SUMMARY: KEEP / FIX / SKIP

### KEEP (Good code to integrate)

1. **Retry infrastructure pattern.** `RetryPolicy` + `run_with_retry` with RETRYABLE_SQLSTATES, exponential backoff + jitter. Extract into a shared module.

2. **Agent logging pattern.** `agent_notes` + `agent_artifacts` tables. Persistent structured logging for long-running research scripts. Useful across all MCAS research.

3. **Cursor-based incremental ingestion.** `tone_ingestion_state` with stream-keyed cursors. Restartable, auditable. Good pattern for any data pipeline.

4. **Vectorized feature engineering.** The numpy matrix operations in `build_feature_matrices` are clean, efficient, and correctly handle NaN propagation. The rolling_* functions using cumsum are textbook numpy.

5. **Social attention features.** The interaction features (social_price_divergence, crowding_pressure, panic_chatter, attention_vs_volume_gap) are novel and worth testing in the main MCAS validation framework.

6. **Sharded backfill pattern.** `(id % shards) = shard` for parallel ingestion. Simple, effective, balanced.

7. **Execution delay in target construction.** `lead(close, delay_steps)` correctly models that you cannot act on a signal immediately. This should be standard in all MCAS research.

### FIX (Issues that need repair before integration)

1. **Add walk-forward validation.** The discovery script must be wrapped in purged k-fold walk-forward. This is non-negotiable per CLAUDE.md Section 4.1. The feature evaluation function is already structured for it (operates on matrices), so the fix is structural (add fold splitting) not algorithmic.

2. **Add DSR correction.** With 144 hypothesis tests (48 features x 3 horizons), apply DSR or at minimum Holm-Bonferroni correction. Report n_trials and E[max(SR)].

3. **Add transaction cost model.** Deduct 5bps slippage + 4bps taker from all forward return targets before evaluating features. Test at 1.5x and 2x slippage.

4. **Compute average daily PF.** Replace or augment the predictor_score with daily PF computation per CLAUDE.md Section 4.5.

5. **Extract shared utilities.** The retry, logging, connection, and cursor management code duplicated across 5 scripts should be in `tone_engine_common.py`.

6. **Fix JSONB in composite PK.** Replace `signal_def JSONB` in `tone_signal_metrics` PK with a deterministic hash TEXT column.

7. **Fix import path in reddit backfill.** Add execution context detection for the `backfill_social_posts` import.

8. **Set random seed for reproducibility.** `np.random.choice` in `evaluate_feature` needs a seed parameter.

9. **Add connection refresh logic.** On retry after connection error, create a fresh connection rather than retrying on the stale one.

10. **Fix `FallbackTickerMatcher` false positives.** The aggressive uppercase matching needs a much larger stopword list or contextual validation.

### SKIP (Code not worth integrating)

1. **`backfill_social_posts.py` materialization functions** (`materialize_features_bulk`, `compute_mention_volume_ratio`). These load all data into memory and use nested Python loops over (symbol, hour) pairs. They target the `social_features_1h` table which is not part of the tone engine pipeline. The tone engine discovery script computes its own features directly from raw data, making these functions redundant.

2. **Separate Postgres 15 instance.** The ring-fenced DB was a good safety measure during development, but for production integration, tone engine features should be computed against the main MCAS Postgres 16 instance. The entire mirroring pipeline becomes unnecessary.

3. **`social_tone_posts`, `social_tone_windows`, `social_tone_cluster_models`, `social_tone_cluster_assignments`, `social_tone_states` tables.** These represent an NLP-based tone analysis pipeline that was designed but never implemented. No code writes to them. Skip until there is actual tone analysis code.

4. **`tone_short_alerts` table.** Designed but never populated. Skip.

5. **`ohlcv_regimes` table.** Designed but never populated by tone engine code. Skip.

6. **fortim/vision22 schema.** Football data pipeline. Completely unrelated. Skip.

7. **`expand_with_ohlcv_synthetic` function.** Already permanently disabled. Skip.

8. **`equities_raw_mentions` ingestion path.** Cross-pollinating equities social data into crypto analysis. If the goal is crypto-specific signals, this introduces distributional mismatch. Skip unless explicitly pursuing cross-asset social features.

---

*End of audit.*
