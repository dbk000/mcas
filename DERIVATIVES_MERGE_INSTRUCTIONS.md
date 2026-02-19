# Derivatives Pipeline — Merge Instructions

This document tells you how to merge the derivatives pipeline work from the dev branch into production. It was produced by a Claude Code session on 2026-02-19.

---

## What was built

A three-phase research pipeline to determine whether Binance derivatives data (funding rate, open interest, long/short ratio) has predictive value for the U1 Unicorn Ignition setup, and if so, how to integrate it.

### Phase 1: Data collection
- `derivatives_collector.py` — standalone service that polls Binance futures API every hour, computes features, writes to `derivatives_features_1h` table
- `20260219_add_derivatives_tables.sql` — creates the `derivatives_features_1h` table

### Phase 2: Statistical validation
- `derivatives_signal_test.py` — kill/confirm test. 12 of 15 features survived. Strongest: `funding_rate` (IC=-0.135, contrarian), `oi_change_pct_24h` (IC=-0.111), `ls_ratio` (IC=-0.094)
- `derivatives_walkforward.py` — walk-forward validation with 3 tests:
  - **Test A** (funding as model feature): FAIL — adding funding to logistic regression hurts precision by 1-2pp across all folds
  - **Test B** (full-feature short-window): baseline precision 4.67%
  - **Test C** (funding as conditional filter): **PASS** — OR=1.537, Fisher p=2.7e-13. Negative funding during compression predicts ignition 1.54x better than positive funding
- **Conclusion:** funding rate works as a binary conditioning filter, NOT as a model feature

### Phase 3: Production integration
- Applied as a post-scoring multiplier in U1's `evaluate_trigger()`:
  - If `bb_width_pctile < 10` (compressed) AND `funding_rate < 0`: score × 1.5
  - If `bb_width_pctile < 10` AND `funding_rate < -0.0001`: score × 1.8
  - No change if funding >= 0, not compressed, or data unavailable
- Zero risk of regression: if `derivatives_features_1h` is empty or query fails, multiplier = 1.0 (no change to existing behavior)

---

## Files to merge

### New files (drop in place, no conflicts possible)

| File | Destination |
|------|-------------|
| `Backend/docs/db/migrations/20260219_add_derivatives_tables.sql` | DB migration — run on production Postgres |
| `Backend/scripts/derivatives_collector.py` | Data collector service |
| `Backend/scripts/derivatives_signal_test.py` | Kill/confirm research script (reference only) |
| `Backend/scripts/derivatives_signal_test_results.json` | Results from signal test (reference only) |
| `Backend/scripts/derivatives_walkforward.py` | Walk-forward validation script (reference only) |
| `Backend/scripts/derivatives_walkforward_results.json` | Results from walk-forward (reference only) |
| `DERIVATIVES_PIPELINE_SPEC.md` | Build spec (reference only) |
| `DERIVATIVES_WALKFORWARD_SPEC.md` | Build spec (reference only) |

### Modified files (apply patch)

| File | What changed |
|------|-------------|
| `Backend/worker/app/setups/u1_unicorn_ignition.py` | 4 edits: `load_funding_rates()` function, U1State fields, post-scoring multiplier, explainability |
| `Backend/worker/app/signal_worker.py` | 2 edits: import `load_funding_rates`, call it in `_evaluate_u1()` |

---

## How to apply

### Step 1: Apply the patch to the two modified files

```bash
cd /srv/mcas
git apply derivatives_funding_filter.patch
```

If production has diverged and the patch doesn't apply cleanly, the conflicts will be in these specific locations — resolve manually:

**u1_unicorn_ignition.py:**
1. Line 24 area: `import time as _time` added after `import logging`
2. Line 40 area: `load_funding_rates()` function + cache globals added before the `# Configuration` section
3. U1State dataclass: `funding_rate` and `funding_rate_updated` fields added after `vol_z_at_trigger`
4. U1State.to_dict(): 2 new keys added at end
5. U1State.from_dict(): 2 new kwargs added at end
6. evaluate_trigger(): `funding_rates: dict = None` param added to signature
7. evaluate_trigger() body: 17 lines of funding boost logic added between `compute_ignition_score()` and `state.ignition_score = total`
8. Explainability section: 3 lines added before the `why_items.extend` block

**signal_worker.py:**
1. Import line: `load_funding_rates` added to the u1 import
2. `_evaluate_u1()`: 3 lines added before `self.u1_evaluator.evaluate_trigger(...)` call, plus `funding_rates=funding_rates` kwarg added to that call

### Step 2: Copy new files

```bash
cp Backend/docs/db/migrations/20260219_add_derivatives_tables.sql /srv/mcas/Backend/docs/db/migrations/
cp Backend/scripts/derivatives_collector.py /srv/mcas/Backend/scripts/
cp Backend/scripts/derivatives_signal_test.py /srv/mcas/Backend/scripts/
cp Backend/scripts/derivatives_signal_test_results.json /srv/mcas/Backend/scripts/
cp Backend/scripts/derivatives_walkforward.py /srv/mcas/Backend/scripts/
cp Backend/scripts/derivatives_walkforward_results.json /srv/mcas/Backend/scripts/
cp DERIVATIVES_PIPELINE_SPEC.md /srv/mcas/
cp DERIVATIVES_WALKFORWARD_SPEC.md /srv/mcas/
```

### Step 3: Run the migration

```bash
docker exec backend-postgres-1 psql -U trading -d trading -f /docs/db/migrations/20260219_add_derivatives_tables.sql
```

### Step 4: Rebuild and restart signal workers

```bash
cd /srv/mcas/Backend
docker compose -f docker-compose.yml build signal_worker_0 signal_worker_1
docker compose -f docker-compose.yml up -d --no-deps signal_worker_0 signal_worker_1
```

### Step 5: Verify

Check signal worker logs for the funding rate load message:
```bash
docker compose -f docker-compose.yml logs -f signal_worker_0 | grep -i funding
```

Expected: `Loaded funding rates for N symbols` (once per hour), and `Funding boost: XXXUSDT funding_rate=-0.000XXX multiplier=1.5` when compressed symbols have negative funding.

If `derivatives_features_1h` is empty (collector hasn't run yet), you'll see `Failed to load funding rates` once, then silence — the multiplier stays 1.0, zero impact on existing behavior.

### Step 6: Start the collector (optional, can be deferred)

The collector is a standalone service. Add to docker-compose or run manually:
```bash
docker compose -f docker-compose.yml run --rm -T worker python3 /scripts/derivatives_collector.py
```

---

## Rollback

If anything goes wrong, the funding filter is fully inert when data is missing. To disable completely:
1. Revert the patch: `git checkout -- Backend/worker/app/setups/u1_unicorn_ignition.py Backend/worker/app/signal_worker.py`
2. Rebuild signal workers

The `derivatives_features_1h` table and collector can remain — they have no coupling to anything else.
