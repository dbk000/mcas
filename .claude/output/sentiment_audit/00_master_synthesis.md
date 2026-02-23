# Master Synthesis: MCAS_sentiment Audit & Integration Plan

**Author:** Claude Opus 4.6
**Date:** 2026-02-22
**Status:** APPROVED FOR EXECUTION

---

## 1. Audit Summary

Five parallel agents audited the MCAS_sentiment repo (built by Codex/GPT-5.3). Key findings:

### What Was Built
| Pillar | Files | Tables | Quality | Action |
|--------|-------|--------|---------|--------|
| Equities Forward Predictor | 16 scripts, 1 API, 5 tests, 4 migrations | 12 tables | Good architecture, toy models, no OOS validation | **MERGE + UPGRADE** |
| Tone Engine | 5 scripts, 3 migrations | 10+ tables (separate DB) | Novel features, zero validation, volatility confound | **MERGE features only** |
| Task Board | 11 scripts, 2 migrations | 3 tables | Duplicates existing MCAS coordination | **SKIP** |
| Vision22 (Football) | 2 scripts, 2 migrations | 9+ tables | Unrelated to trading | **SKIP** |
| Frontend | 0 changes | — | Byte-for-byte identical | **SKIP** |
| Social Collector | 0 changes | — | Already in production | **SKIP** |

### Critical Validation Gaps (Both Pipelines)
- NO walk-forward validation (CLAUDE.md requires 5-fold purged rolling)
- NO holdout set (CLAUDE.md requires 15-20% final holdout)
- NO DSR correction (343 tests in tone, ~50 configs in equities)
- NO transaction cost model in evaluation
- NO average daily PF computation (the actual success metric)
- Equities walk-forward has 4 folds, no purge gap, no embargo

### GPT-5.3 PF Estimate: 0.95-1.1
**Opus assessment: Realistic PF after OOS degradation = 0.85-1.0 (breakeven at best)**

---

## 2. Integration Plan

### Phase 1: Merge Equities Pipeline (files only, no code changes)
Copy from MCAS_sentiment to main MCAS:
- `Backend/scripts/equities_forward_predictor.py`
- `Backend/scripts/equities_feature_builder.py`
- `Backend/scripts/equities_events_collector.py`
- `Backend/scripts/equities_ohlcv_ingestor.py`
- `Backend/scripts/equities_outcome_settler.py`
- `Backend/scripts/equities_policy_optimizer.py`
- `Backend/scripts/equities_calibration_watchdog.py`
- `Backend/scripts/equities_alert_bridge.py`
- `Backend/scripts/equities_pipeline_runner.py`
- `Backend/scripts/equities_backfill_enqueue.py`
- `Backend/scripts/equities_raw_backfill_enqueue.py`
- `Backend/scripts/equities_raw_mentions_backfill.py`
- `Backend/scripts/equities_queue_refiller.py`
- `Backend/scripts/equities_task_executor.py`
- `Backend/api/app/equities_api.py`
- `Backend/tests/test_equities_forward_predictor.py`
- `Backend/tests/test_equities_api_contract.py`
- `Backend/tests/test_equities_outcome_settler.py`
- `Backend/tests/test_equities_policy_feedback.py`
- `Backend/tests/test_equities_raw_backfill.py`
- `Backend/docs/db/migrations/20260222_add_equities_predictor_tables.sql`
- `Backend/docs/db/migrations/20260222_add_equities_outcomes.sql`
- `Backend/docs/db/migrations/20260222_add_equities_policy_tables.sql`
- `Backend/docs/db/migrations/20260222_add_equities_raw_mentions.sql`
- `Backend/docs/equities_forward_predictor.md`

### Phase 2: Merge Tone Engine Features (selective)
Copy useful components only:
- `Backend/scripts/tone_engine_forward_predictor_discovery.py` (for the feature engineering code)
- `Backend/scripts/tone_engine_ingest_sources.py`
- `Backend/scripts/tone_engine_backfill_sharded.py`
- Tone engine migrations (into main DB, not separate Postgres 15)

### Phase 3: Opus-Grade Upgrade of Equities Forward Predictor
This is the main value-add. Transform the GPT-5.3 baseline into CLAUDE.md-compliant research:

#### 3a. Validation Infrastructure
1. **Walk-forward: 4 folds → 5+ folds with purge/embargo**
   - Minimum 5-fold purged rolling walk-forward
   - 1-day embargo (purge gap) between train and test periods
   - Non-overlapping test periods
   - Expanding window (already implemented) is correct

2. **Holdout set: absent → 15-20% reserved**
   - Final 15-20% of data never seen during walk-forward
   - Only evaluated once for strategies that pass walk-forward

3. **DSR correction: absent → mandatory**
   - Track n_trials across all configurations tested
   - Compute E[max(SR)] and DSR per Harvey et al. (2016)
   - DSR > 0 required for any result to be reported

4. **Average daily PF: absent → primary success metric**
   - Compute PF per trading day, then average
   - Replace RMSE/accuracy as the success gate
   - Report aggregate PF alongside but not as gate

#### 3b. Model Upgrade
1. **Ridge + logistic → gradient-boosted trees**
   - The linear model is a useful baseline but leaves non-linear structure on the table
   - Replace with LightGBM or pure-numpy decision tree ensemble
   - Keep the linear model as a comparison baseline

2. **Feature interactions: manual → learned**
   - Currently 16 features with no interactions
   - Tree models learn interactions automatically
   - Add the tone engine's social-price interaction features as additional inputs

#### 3c. Transaction Cost Model
1. **Add cost model to validation**
   - Equities slippage: ~5-10 bps depending on liquidity
   - Fee: 0-2 bps (many brokers commission-free, but spread matters)
   - Test at base, 1.5x, and 2x slippage per CLAUDE.md Section 4.4
   - Strategy must survive at 1.5x

#### 3d. Performance Fixes
1. **O(n) bar lookup → dict-based O(1)**
2. **Connection pooling for API**
3. **Multiprocessing for walk-forward folds**
4. **Batch DB writes with progress logging**

#### 3e. Architecture Fixes
1. **Add `is_final` check** (currently missing, required by CLAUDE.md)
2. **Fix gap risk score** (currently placeholder: `abs(pred_ret) * 10`)
3. **Fix memory accumulation** (currently loads all records into memory)
4. **Add execution context detection** (Docker vs host path resolution)

---

## 3. Priority Order

1. **Phase 1** (file merge) — mechanical, 15 minutes
2. **Phase 3a** (validation infrastructure) — highest value, transforms toy into research
3. **Phase 3d** (performance fixes) — enables faster iteration
4. **Phase 3b** (model upgrade) — unlocks non-linear edge
5. **Phase 3c** (transaction cost model) — gates whether any edge is real
6. **Phase 3e** (architecture fixes) — production readiness
7. **Phase 2** (tone engine features) — additive, lower priority

---

## 4. What "Opus-Grade Improvement" Looks Like

GPT-5.3 delivered:
- Clean schema design (genuinely good)
- Anti-leakage discipline (available_at_ts, append-only triggers)
- Toy models with no validation

Opus 4.6 will deliver:
- CLAUDE.md-compliant validation (5-fold purged walk-forward + holdout + DSR)
- Transaction cost-aware evaluation
- Average daily PF as success metric
- Non-linear models that can capture interaction effects
- Honest reporting: if the equities edge doesn't survive validation, we say so

The improvement isn't just "better code" — it's the difference between "looks like it might work" and "here's the evidence it does or doesn't work."
