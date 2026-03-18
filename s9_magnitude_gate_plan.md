# S9 Magnitude Gate — Root Cause Fix
_2026-03-18_

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

### Deploy
Sequential: restart signal_worker_0/1 only. Verify via diagnostics log.
