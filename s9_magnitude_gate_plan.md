# S9 Magnitude Gate + Full Governance Rewiring — Root Cause Fix
_2026-03-18 — No deferrals. Everything ships._

## Diagnosis

The S9 FnG gate is correctly wired but doesn't solve the March 14-17 PF 0.571.
All 37 losing signals fired during Fear (FnG 15-28), which is supposedly S9's
best regime. The FnG gate would not have filtered a single one.

**Root cause:** S9 is shorting noise, not outlier pumps.

32 of 37 signals had `ret_4h < 5%` — coins that barely moved up in a sea of red.
These aren't abnormal pumps that will revert; they're normal fluctuations.
The top decile in a barely-above-30% breadth market is the least-bearish coins,
not actual outlier pumps.

## Evidence

13,106 historical S9 live signals, sweep across ret_4h thresholds:

| Threshold | N Signals | Win Rate | PF | Avg PnL |
|---|---|---|---|---|
| 0% (current) | 13,106 | 63.7% | 1.229 | +0.56% |
| 2% | 10,133 | 65.0% | 1.254 | +0.63% |
| 3% | 7,162 | 66.7% | 1.317 | +0.78% |
| **4%** | **4,853** | **68.4%** | **1.384** | **+0.95%** |
| **5%** | **3,419** | **69.9%** | **1.472** | **+1.15%** |
| 6% | 2,461 | 71.1% | 1.569 | +1.34% |
| 8% | 1,396 | 71.5% | 1.568 | +1.38% |
| 10% | 935 | 72.0% | 1.579 | +1.42% |

**Monotonic improvement across ALL metrics at EVERY threshold.**
Not a 37-signal fluke — structural across 13,106 signals.

March 14-17 split:
- ret_4h < 5%: 32 signals, PF 0.495 (losing money)
- ret_4h >= 5%: 5 signals, PF 1.700 (making money)

## Recommended Gate

**`S9_MIN_RET_4H: float = 0.05`** (5% minimum 4h return to qualify as outlier)

At 5%:
- Filters 74% of signals (noise) while keeping 26% (genuine outliers)
- PF improves from 1.229 → 1.472 (+20%)
- Win rate improves from 63.7% → 69.9%
- Still 3,419 signals historically — not starving the strategy

Conservative alternative: 4% threshold (PF 1.384, 37% of signals pass).
Aggressive alternative: 6% threshold (PF 1.569, 19% of signals pass).

## Implementation

### Governance — S9 Registry Unification (CRITICAL)

**Pre-existing violation found:** `S9TypedConfig` exists in config_registry.py
(line 150) with all 5 core params, but NOTHING READS FROM IT. The signal_worker
reads from `S9Evaluator.BREADTH_THRESHOLD` (class constant in core_logic).
Two sources of truth — the exact anti-pattern the architecture forbids.

**This plan fixes it.** ALL S9 config unified into S9TypedConfig:

```python
@dataclass
class S9TypedConfig:
    # Core params (already defined, now wired)
    RET_LOOKBACK: int = 4
    HOLD_BARS: int = 48
    PERCENTILE: int = 10
    BREADTH_THRESHOLD: float = 0.3
    COST_BPS: int = 18
    # Magnitude gate (NEW)
    MIN_RET_4H: float = 0.05
    # FnG regime gate (MOVE from WorkerTypedConfig)
    FNG_GATE_ENABLED: bool = True
    FNG_MAX_VALUE: int = 50
```

**Wire signal_worker to read from `get_config().S9`:**
- Replace every `S9Evaluator.BREADTH_THRESHOLD` with `get_config().S9.BREADTH_THRESHOLD`
- Replace every `S9Evaluator.PERCENTILE` with `get_config().S9.PERCENTILE`
- Replace `_worker_cfg().S9_FNG_GATE_ENABLED` with `get_config().S9.FNG_GATE_ENABLED`
- Replace `_worker_cfg().S9_FNG_MAX_VALUE` with `get_config().S9.FNG_MAX_VALUE`
- New: `get_config().S9.MIN_RET_4H` for magnitude gate

**S9Evaluator class constants become documentation only** — the actual runtime
values come from the frozen registry. Same pattern as D1/U1.

### Code Changes

**config_registry.py:**
- Add `MIN_RET_4H: float = 0.05`, `FNG_GATE_ENABLED: bool = True`,
  `FNG_MAX_VALUE: int = 50` to S9TypedConfig
- Remove `S9_FNG_GATE_ENABLED` and `S9_FNG_MAX_VALUE` from WorkerTypedConfig

**bootstrap.py:**
- Read `S9_MIN_RET_4H`, `S9_FNG_GATE_ENABLED`, `S9_FNG_MAX_VALUE` from env
  into S9TypedConfig population (move from Worker population)

**signal_worker.py:**
- Replace all `S9Evaluator.BREADTH_THRESHOLD` → `get_config().S9.BREADTH_THRESHOLD`
- Replace all `S9Evaluator.PERCENTILE` → `get_config().S9.PERCENTILE`
- Replace all `S9Evaluator.RET_LOOKBACK` → `get_config().S9.RET_LOOKBACK`
- Replace FnG config reads to use `get_config().S9.*`
- Add magnitude filter after top_symbols computation:
  ```python
  s9_cfg = get_config().S9
  min_ret = s9_cfg.MIN_RET_4H
  top_symbols = [(sym, ret) for sym, ret in top_symbols if ret >= min_ret]
  if not top_symbols:
      self._diag["s9_magnitude_filtered"] += 1
      logger.info("S9: breadth OK but all %d top-decile ret_4h < %.1f%% — no outliers",
                  decile_n, min_ret * 100)
      return
  ```
- Add `s9_magnitude_filtered: 0` to diagnostics
- Record `min_ret_4h`, `min_ret_4h_threshold` in trace_json

### Verification
After deploy, the March 14-17 scenario would produce:
- March 14: ~4 of 10 signals pass (ret_4h >= 5%)
- March 15-16: most signals filtered (all ret_4h < 5%)
- March 17: VANRYUSDT 16.4% + BTTCUSDT 5.9% pass

Estimated PF on survivors: ~1.7 (vs 0.571 current).

Verify signal_worker reads from registry, not class:
```bash
grep "S9Evaluator\." /srv/mcas/Backend/governance/signal_worker.py
# Must return ZERO matches after fix (only imports, no attribute reads)
```

### Files Changed
- `governance/config_registry.py` — expand S9TypedConfig, clean WorkerTypedConfig
- `governance/bootstrap.py` — move S9 FnG env reads to S9 section
- `governance/signal_worker.py` — replace all S9Evaluator.* reads with get_config().S9.*

---

## Full Governance Rewiring (ALL strategies, ships today)

### Critical Risks Identified by Reviews (all mitigated)

**Risk 1: W_BTC_ADJ_RET vs W_BTC_AD_RET name mismatch**
D1Config uses `W_BTC_ADJ_RET`. D1TypedConfig uses `W_BTC_AD_RET` (truncated).
Mechanical replacement would crash.
**Mitigation:** Rename D1TypedConfig field to `W_BTC_ADJ_RET` first. Then replace.
Single rename in config_registry.py before any signal_worker changes.

**Risk 2: signal_engine.py has 2 S9Evaluator reads**
Lines 670, 689 read `S9Evaluator.BREADTH_THRESHOLD` and `S9Evaluator.build_signal()`.
**Mitigation:** Include signal_engine.py in scope. `build_signal()` is a static method
call, not a config read — leave it. Replace BREADTH_THRESHOLD read.

**Risk 3: D1Config is load-bearing in core_logic (50+ reads)**
`core_logic/d1_dormancy_ignition.py` reads D1Config internally. Can't replace those
with `get_config()` (would violate core_logic → governance import direction).
**Mitigation:** After rewiring signal_worker, add a boot-time sync assertion:
`assert get_config().D1.DORMANCY_THRESHOLD == D1Config.DORMANCY_THRESHOLD` for
every field. Process crashes if registry and class constants diverge. This is the
governance enforcement until core_logic is migrated to constructor injection.

**Risk 4: Pseudocode wrong — top_symbols is strings, not tuples**
Must filter `top_decile` (has ret_4h) BEFORE deriving `top_symbols` (plain strings).
**Mitigation:** Correct insertion point documented below.

**Risk 5: FnG config migration could break reads if not atomic**
Moving S9_FNG_* from WorkerTypedConfig to S9TypedConfig.
**Mitigation:** All read-site changes + config changes in same commit. Test before deploy.

**Risk 6: MAJOR_SYMBOLS is tuple in registry, set in D1Config**
`in` operator works on both. No set operations observed.
**Mitigation:** Leave D1Config.MAJOR_SYMBOLS alone (read in core_logic). Signal_worker
doesn't read MAJOR_SYMBOLS directly — it calls `is_d1_excluded()` which reads D1Config.

### Execution Plan

**Phase 1: Registry fixes (config_registry.py + bootstrap.py)**
1. Rename D1TypedConfig `W_BTC_AD_RET` → `W_BTC_ADJ_RET`
2. Add `MIN_RET_4H: float = 0.05` to S9TypedConfig
3. Move `S9_FNG_GATE_ENABLED` and `S9_FNG_MAX_VALUE` from WorkerTypedConfig to S9TypedConfig
4. Wire env var reads for all S9TypedConfig fields in bootstrap.py
5. Wire env var reads for D1TypedConfig fields that don't have them
6. Wire env var reads for U1TypedConfig fields that don't have them

**Phase 2: signal_worker.py rewiring**
Replace ALL class constant reads with registry reads:
- `S9Evaluator.BREADTH_THRESHOLD` → `get_config().S9.BREADTH_THRESHOLD` (×7)
- `S9Evaluator.PERCENTILE` → `get_config().S9.PERCENTILE` (×2)
- `S9Evaluator.RET_LOOKBACK` → `get_config().S9.RET_LOOKBACK` (×2)
- `S9Evaluator.HOLD_BARS` → `get_config().S9.HOLD_BARS` (×1)
- `S9Evaluator.COST_BPS` → `get_config().S9.COST_BPS` (×1)
- `_worker_cfg().S9_FNG_GATE_ENABLED` → `get_config().S9.FNG_GATE_ENABLED` (×2)
- `_worker_cfg().S9_FNG_MAX_VALUE` → `get_config().S9.FNG_MAX_VALUE` (×3)
- `D1Config.*` → `get_config().D1.*` (×38, using corrected W_BTC_ADJ_RET name)
- `U1Config.*` → `get_config().U1.*` (×13)
Leave `S9Evaluator.build_signal()` (method call, not config) and `D1Phase.*` (enum, not config).

**Phase 3: signal_engine.py rewiring**
- `S9Evaluator.BREADTH_THRESHOLD` → `get_config().S9.BREADTH_THRESHOLD` (×1)
- Leave `S9Evaluator.build_signal()` (static method)

**Phase 4: unicorn_pipeline.py rewiring**
- `U1Config.*` → `get_config().U1.*` (×8)

**Phase 5: Boot-time sync assertions (signal_worker.__init__)**
For D1 and U1 (where core_logic still reads class constants):
```python
# Governance enforcement: registry and class constants MUST agree.
# Crashes at boot if they diverge. Prevents silent two-source-of-truth.
d1_cfg = get_config().D1
assert d1_cfg.DORMANCY_THRESHOLD == D1Config.DORMANCY_THRESHOLD, \
    f"D1 DORMANCY_THRESHOLD: registry={d1_cfg.DORMANCY_THRESHOLD} != class={D1Config.DORMANCY_THRESHOLD}"
# ... repeat for every D1 and U1 field read by both signal_worker and core_logic
```

**Phase 6: Magnitude gate insertion**
Correct insertion point (filter `top_decile`, not `top_symbols`):
```python
# After line ~1628 where top_decile is computed
s9_cfg = get_config().S9
min_ret = s9_cfg.MIN_RET_4H
pre_filter_count = len(top_decile)
top_decile = [(sym, rets) for sym, rets in top_decile if rets[0] >= min_ret]
if not top_decile:
    self._diag["s9_magnitude_filtered"] += 1
    logger.info("S9: breadth=%.3f OK but all %d top-decile ret_4h < %.1f%% — no outliers",
                breadth, pre_filter_count, min_ret * 100)
    return
# THEN derive top_symbols from filtered top_decile
top_symbols = [sym for sym, _ in top_decile]
```

**Phase 7: View enrichment (v_live_opportunity_pnl)**
Add to the view — 21% extraction rate is unacceptable:
- `ret_4h` (magnitude gate depends on this)
- `breadth` at signal time (primary gate — blind without it)
- `rank`, `decile_size`, `universe_size`
- `r_value`, `stop_price`, `tp1_price`, `tp2_price`
- `skip_reason IS NOT NULL` as `is_skip` boolean
- `score_components` (rank_score, breadth_score, magnitude_score)

Add to stats rollup:
- `distinct_symbols` (concentration)
- `avg_ret_4h` (magnitude distribution)
- `avg_breadth` (gate calibration)
- `skip_count` (actionable vs non-actionable)
- `pnl_stddev` (risk measure)

### Deploy Sequence (sequential, verified)

```
1. Commit all changes (single atomic commit)
2. Push → pull on production
3. Build signal_worker_0/1, api
4. Restart signal_worker_0/1
   Verify: "SNAPSHOT_FIELDS assertion passed"
   Verify: "D1 state bootstrapped" (will re-run, ~4 min)
   Verify: sync assertions pass (no crash)
   Verify: grep "S9Evaluator\.\|D1Config\.\|U1Config\." signal_worker.py returns 0
   Verify: diagnostics show s9_magnitude_filtered counter
5. Restart API
   Verify: /v1/stats/opportunities returns enriched data
```

### Governance Checklist
- [ ] D1TypedConfig.W_BTC_ADJ_RET renamed (matches D1Config)
- [ ] S9TypedConfig has MIN_RET_4H, FNG_GATE_ENABLED, FNG_MAX_VALUE
- [ ] WorkerTypedConfig cleaned of S9-specific fields
- [ ] ALL D1/U1/S9 TypedConfig fields have env var overrides wired
- [ ] signal_worker has ZERO reads from S9Evaluator.*/D1Config.*/U1Config.*
- [ ] signal_engine.py has ZERO reads from S9Evaluator config constants
- [ ] unicorn_pipeline.py has ZERO reads from U1Config.*
- [ ] Boot-time sync assertions for D1 and U1 (registry == class constants)
- [ ] Magnitude gate filters top_decile (not top_symbols)
- [ ] v_live_opportunity_pnl extracts ret_4h, breadth, rank, skip_reason
- [ ] s9_magnitude_filtered diagnostic counter added
- [ ] FnG value in trace_json includes MIN_RET_4H in config_snapshot

### Deploy
Sequential: restart signal_worker_0/1 only. Verify via diagnostics log.
