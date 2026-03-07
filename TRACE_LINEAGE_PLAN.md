# Computation Trace / Lineage System — Plan for Review

## Goal

Every opportunity carries a sealed audit trace: a structured record of every computation from the first candle used by any indicator through to the final promotion decision. The trace is attached at birth, travels with the opportunity forever, and is queryable via Postgres JSONB operators.

---

## 1. Current State Analysis

### What already exists on opportunities

The `payload_json` TEXT column already captures significant provenance data:

| Data | Where | Format |
|------|-------|--------|
| Indicator values at trigger (dormancy_score, bb_width, volume_z, rvol, accum_divergence) | payload_json | Flat keys |
| Score components + weights | payload_json.score_components, .scoring_weights | Dict |
| Gate descriptions | payload_json.gates | Strings like `"dormancy_score >= 0.75"` — no actual values |
| Exit config snapshot | payload_json.exit_config | Dict |
| Why items (explainability) | payload_json.why_items | List of strings |
| bar_start_ts, as_of_ts | payload_json | Epoch |
| Setup version | opportunities.setup_version | Text |
| Batch ID | opportunities.batch_id | Text |

### What's missing

| Gap | Why it matters |
|-----|---------------|
| Candle window boundaries | Can't answer "which bars contributed to this signal?" |
| Structured gate evaluations | Gates are currently description strings, not {value, threshold, passed} |
| D1State at trigger time | Phase, bar_index, dormancy_confirmed_at_bar — lost after evaluation |
| FeatureFactory parameters | n_atr=14, n_bb=20, etc. — assumed constant but not recorded |
| Config version/hash | Can't determine if D1Config changed between two opportunities |
| Indicator computation timestamp | When were the indicators that fed this evaluation computed? |
| Sealed timestamp | When was the opportunity actually written to DB? |

### Separate systems (not on opportunity row)

- `signal_events` table has `feature_snapshot JSONB` — but it's a separate table with no FK to opportunities
- `run_trades.trace_json` and `paper_positions.trace_json` exist — but trace is on the trade, not the opportunity

---

## 2. Architecture

### TraceContext Pattern (as specified)

```
Opportunity Evaluation Pipeline
         |
    TraceContext()              <- created at start of evaluation
         |
    IndicatorSnapshot read ---> trace.record_indicators(snapshot)
         |
    compute_state() ----------> trace.record_state(state)
         |
    evaluate_trigger() -------> trace.record_gate(name, value, threshold, passed)
                                trace.record_gate(...)  (per gate)
         |
    Score computation --------> trace.record_score(components, raw, calibrated)
         |
    Promotion ----------------> trace.seal()
         |
    _persist_signal_to_pg() --> opportunity.trace_json = sealed_trace
```

### Storage: JSONB column on opportunities

New column: `trace_json JSONB DEFAULT NULL`

Why JSONB not TEXT:
- Queryable: `WHERE trace_json->'gates' @> '[{"gate": "dormancy", "passed": false}]'`
- Indexable: GIN index on specific paths if needed later
- Consistent with `signal_events.feature_snapshot` and `run_trades.trace_json`

Why on opportunities (not a separate table):
- No join needed — trace travels with the opportunity
- Opportunity row is already the canonical record
- No lifecycle mismatch (trace and opportunity are born and expire together)

### Feature flag

`TRACE_ENABLED` env var (default: `true`). When false, no TraceContext is created, no trace_json written. Zero overhead in the hot path when disabled.

---

## 3. Trace Schema

```json
{
  "v": "1.0",
  "strategy": "D1",
  "symbol": "XYZUSDT",
  "timeframe": "1h",
  "eval_ts": 1709280000.0,

  "candle_window": {
    "first_ts": "2024-01-31T01:00:00Z",
    "last_ts": "2024-03-01T08:00:00Z",
    "bar_count": 201,
    "timeframe": "1h"
  },

  "indicator_snapshot": {
    "dormancy_score": 0.85,
    "bb_width": 0.0089,
    "volume_z": 3.2,
    "rvol": 5.8,
    "accum_divergence": 0.034,
    "donchian_high_20": 0.00456,
    "donchian_low_20": 0.00412,
    "atr": 0.00015,
    "rsi": 62.3,
    "sma_50": 0.00430,
    "ema_20": 0.00441
  },

  "indicator_params": {
    "n_atr": 14, "n_rsi": 14, "n_bb": 20,
    "k_bb": 2.0, "n_kc": 20, "n_volz": 20
  },

  "state": {
    "phase": "ACCUMULATING",
    "dormancy_score": 0.85,
    "bb_width": 0.0089,
    "dormancy_confirmed_at_bar": 142,
    "compression_box_high": 0.00456,
    "compression_box_low": 0.00412,
    "prev_compression_box_high": 0.00450,
    "prev_compression_box_low": 0.00410,
    "accum_divergence": 0.034,
    "volume_z": 3.2,
    "rvol": 5.8,
    "bb_width_prev": 0.0082,
    "btc_beta": 1.0,
    "bar_index": 201
  },

  "gates": [
    {"gate": "dormancy",       "value": 0.85,     "threshold": 0.75,   "op": ">=", "passed": true},
    {"gate": "compression",    "value": 0.0089,   "threshold": 0.02,   "op": "<",  "passed": true},
    {"gate": "volume_z",       "value": 3.2,      "threshold": 2.0,    "op": ">=", "passed": true},
    {"gate": "rvol",           "value": 5.8,      "threshold": 3.0,    "op": ">=", "passed": true},
    {"gate": "breakout",       "value": 0.00462,  "threshold": 0.00450, "op": ">", "passed": true, "note": "close > prev_compression_box_high"},
    {"gate": "bb_expanding",   "value": 0.0089,   "threshold": 0.0082, "op": ">",  "passed": true, "note": "bb_width > bb_width_prev"}
  ],

  "score": {
    "components": {
      "dormancy": 0.17, "vol_z": 0.12, "accum_div": 0.08,
      "bb_expansion": 0.10, "rvol": 0.15, "btc_adj_ret": 0.05
    },
    "raw": 0.67,
    "calibration_mult": 0.75,
    "calibrated": 0.50
  },

  "config_hash": "a3f8c2d1",
  "config_snapshot": {
    "DORMANCY_THRESHOLD": 0.75,
    "BB_WIDTH_COMPRESS": 0.02,
    "VOLUME_Z_SPIKE": 2.0,
    "RVOL_MIN": 3.0,
    "STOP_PCT": 0.015,
    "TP1_PCT": 0.10,
    "TP2_PCT": 0.25,
    "TIME_STOP_BARS_1H": 72
  },

  "promotion": {
    "setup_version": "D1_v1.2",
    "sealed_at": 1709280005.0
  }
}
```

### What's NOT in the trace (by design)

- **Raw OHLCV bars**: 201 bars x ~100 bytes = 20KB per opp. The bars are already in the `ohlcv` table indexed by `(symbol, timeframe, bar_start_ts)`. The trace records `candle_window.first_ts` and `candle_window.last_ts` — replay from ohlcv table if needed.
- **HTF candles**: Same reasoning — queryable from ohlcv table via window boundaries.
- **Duplicate payload fields**: indicator_snapshot in trace supersedes the flat keys in payload_json. Over time, payload_json can slim down, but no breaking change needed now.

### Per-strategy trace variants

Each strategy records its own indicator_snapshot, state, and gates:

| Strategy | indicator_snapshot keys | state keys | gates |
|----------|----------------------|------------|-------|
| D1 | dormancy_score, bb_width, volume_z, rvol, accum_divergence, donchian_*, atr | D1State.to_dict() | 5-6 gates |
| S10 | macd_histogram, macd_prev_histogram, rvol, sma_20 | S10 state fields | MACD crossover, volume, trend |
| U1 | rel_volume, compression_box_*, trigger_path specifics | U1 state | U1 phase gates |
| S9 | ret_4h, ret_24h, breadth, rank | S9State | breadth, decile, microstructure |

The TraceContext `record_indicator()` and `record_gate()` methods are generic — each setup calls them with its own keys.

---

## 4. Implementation: TraceContext Class

**New file: `Backend/worker/app/trace_context.py`**

```python
import time
import hashlib
import json
from dataclasses import dataclass, field

@dataclass
class TraceContext:
    """Computation provenance collector for opportunity lineage.

    Created at the start of each opportunity evaluation. Each pipeline
    step pushes its breadcrumb. Sealed at promotion time → serialised
    to JSONB → stored on the opportunity row.
    """
    strategy: str = ""
    symbol: str = ""
    timeframe: str = ""
    eval_ts: float = field(default_factory=time.time)

    _candle_window: dict = field(default_factory=dict)
    _indicators: dict = field(default_factory=dict)
    _indicator_params: dict = field(default_factory=dict)
    _state: dict = field(default_factory=dict)
    _gates: list = field(default_factory=list)
    _score: dict = field(default_factory=dict)
    _config: dict = field(default_factory=dict)
    _config_hash: str = ""
    _promotion: dict = field(default_factory=dict)
    _sealed: bool = False

    def record_candle_window(self, first_ts, last_ts, bar_count: int, timeframe: str):
        self._candle_window = {
            "first_ts": str(first_ts),
            "last_ts": str(last_ts),
            "bar_count": bar_count,
            "timeframe": timeframe,
        }

    def record_indicator(self, name: str, value):
        self._indicators[name] = value

    def record_indicators_bulk(self, snapshot_dict: dict):
        self._indicators.update(snapshot_dict)

    def record_indicator_params(self, params: dict):
        self._indicator_params = params

    def record_state(self, state_dict: dict):
        self._state = state_dict

    def record_gate(self, gate: str, value, threshold, op: str,
                    passed: bool, note: str = None):
        entry = {"gate": gate, "value": value, "threshold": threshold,
                 "op": op, "passed": passed}
        if note:
            entry["note"] = note
        self._gates.append(entry)

    def record_score(self, components: dict, raw: float,
                     calibration_mult: float, calibrated: float):
        self._score = {
            "components": components,
            "raw": round(raw, 6),
            "calibration_mult": round(calibration_mult, 4),
            "calibrated": round(calibrated, 6),
        }

    def record_config(self, config_dict: dict):
        self._config = config_dict
        raw = json.dumps(config_dict, sort_keys=True)
        self._config_hash = hashlib.md5(raw.encode()).hexdigest()[:8]

    def seal(self, setup_version: str) -> dict:
        """Seal the trace at promotion time. Returns the trace dict."""
        self._sealed = True
        self._promotion = {
            "setup_version": setup_version,
            "sealed_at": time.time(),
        }
        return {
            "v": "1.0",
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "eval_ts": self.eval_ts,
            "candle_window": self._candle_window,
            "indicator_snapshot": self._indicators,
            "indicator_params": self._indicator_params,
            "state": self._state,
            "gates": self._gates,
            "score": self._score,
            "config_hash": self._config_hash,
            "config_snapshot": self._config,
            "promotion": self._promotion,
        }
```

**Size estimate**: ~2-4KB per opportunity (no raw bars). 1500 D1 opps = ~5MB total. Negligible.

---

## 5. Injection Points

### 5.1 signal_worker._evaluate_d1() (lines 370-474)

This is the orchestrator. All trace recording happens here — the evaluator methods don't need signature changes.

```python
def _evaluate_d1(self, symbol, tf, snapshot, bar_start_ts):
    if is_d1_excluded(symbol):
        return
    try:
        # --- TRACE: create context ---
        trace = TraceContext(strategy="D1", symbol=symbol, timeframe=tf) \
                if TRACE_ENABLED else None

        # --- TRACE: candle window ---
        if trace and snapshot.ohlcv:
            trace.record_candle_window(
                first_ts=snapshot.ohlcv[0].timestamp,
                last_ts=snapshot.ohlcv[-1].timestamp,
                bar_count=len(snapshot.ohlcv),
                timeframe=tf,
            )

        # --- TRACE: indicator values ---
        if trace:
            trace.record_indicators_bulk({
                "dormancy_score": snapshot.dormancy_score,
                "bb_width": snapshot.bb_width,
                "volume_z": snapshot.volume_z,
                "rvol": snapshot.rvol,
                "accum_divergence": snapshot.accum_divergence,
                "donchian_high_20": snapshot.donchian_high_20,
                "donchian_low_20": snapshot.donchian_low_20,
                "atr": snapshot.atr,
                "rsi": snapshot.rsi,
            })

        state = self.d1_evaluator.compute_state(snapshot, prev_state=prev_state, ...)
        self._d1_prev_states[state_key] = state

        # --- TRACE: state after compute ---
        if trace:
            trace.record_state(state.to_dict())

        sig = self.d1_evaluator.evaluate_trigger(snapshot, state, symbol, tf)
        if sig is None:
            return

        # --- TRACE: gate evaluations (reconstructed from state + config) ---
        if trace:
            close = snapshot.ohlcv[-1].close
            trace.record_gate("dormancy", state.dormancy_score,
                              D1Config.DORMANCY_THRESHOLD, ">=", True)
            trace.record_gate("compression", state.bb_width,
                              D1Config.BB_WIDTH_COMPRESS, "<", True)
            trace.record_gate("volume_z", state.volume_z,
                              D1Config.VOLUME_Z_SPIKE, ">=", True)
            trace.record_gate("rvol", state.rvol,
                              D1Config.RVOL_MIN, ">=", True)
            trace.record_gate("breakout", close,
                              state.prev_compression_box_high, ">", True,
                              note="close > prev_compression_box_high")
            trace.record_gate("bb_expanding", state.bb_width,
                              state.bb_width_prev, ">", True,
                              note="bb_width > bb_width_prev")

        # --- TRACE: score ---
        calib_mult = SETUP_SCORE_MULTIPLIERS.get("D1", 1.0)
        calibrated_score = min(sig.setup_score * calib_mult, 1.0)
        if trace:
            trace.record_score(sig.score_components, sig.setup_score,
                               calib_mult, calibrated_score)

        # --- TRACE: config snapshot ---
        if trace:
            trace.record_config({
                "DORMANCY_THRESHOLD": D1Config.DORMANCY_THRESHOLD,
                "BB_WIDTH_COMPRESS": D1Config.BB_WIDTH_COMPRESS,
                "VOLUME_Z_SPIKE": D1Config.VOLUME_Z_SPIKE,
                "RVOL_MIN": D1Config.RVOL_MIN,
                "STOP_PCT": D1Config.STOP_PCT,
                "TP1_PCT": D1Config.TP1_PCT,
                "TP2_PCT": D1Config.TP2_PCT,
                "TIME_STOP_BARS_1H": D1Config.TIME_STOP_BARS_1H,
            })

        # Build payload (existing code unchanged)
        payload = { ... }

        # --- TRACE: seal ---
        trace_dict = None
        if trace:
            setup_version = self.SETUP_VERSIONS.get("D1", "D1_v1.0")
            trace_dict = trace.seal(setup_version)

        self._persist_signal_to_pg(symbol, tf, "D1", calibrated_score,
                                   reason, payload, trace_json=trace_dict)
```

### 5.2 signal_worker._evaluate_s10() (lines 319-368)

Same pattern — create TraceContext, record S10-specific indicators/gates, seal, pass to persist.

### 5.3 signal_worker._evaluate_s9_cross_sectional() (lines 939-1097)

S9 is cross-sectional with microstructure. Trace includes:
- Universe size, breadth, decile threshold
- Per-symbol: ret_4h, ret_24h, rank, microstructure context
- Gates: breadth > threshold, in top decile

### 5.4 signal_worker._evaluate_u1() (lines 476+)

U1 follows the same pattern with its own indicator set and gates.

### 5.5 signal_worker._persist_signal_to_pg() (line 794)

Add `trace_json: dict = None` parameter. Store alongside payload_json:

```python
def _persist_signal_to_pg(self, symbol, tf, setup_id, score, reason,
                           payload, trace_json=None):
    payload_str = json.dumps(payload)
    trace_str = json.dumps(trace_json) if trace_json else None
    # ... existing UPSERT logic, add trace_json column
```

### 5.6 Backfill scripts

Each backfill script (d1_backfill, s9_backfill, u1_backfill) has its own opportunity creation path. These also need TraceContext injection — same pattern, different entry point. Can be done incrementally after the live path is working.

---

## 6. DB Migration

**File: `Backend/docs/db/migrations/20260307_add_trace_json.sql`**

```sql
BEGIN;
SELECT pg_advisory_lock(20260307);

-- Add JSONB trace column (nullable — existing opps have no trace)
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS trace_json JSONB DEFAULT NULL;

-- Comment for documentation
COMMENT ON COLUMN opportunities.trace_json IS
    'Sealed computation trace: candle window, indicators, gates, score, config at trigger time. Schema v1.0.';

SELECT pg_advisory_unlock(20260307);
COMMIT;
```

No index initially — we don't query by trace content in any hot path. Add GIN index later if analytical queries need it.

---

## 7. Design Decisions & Trade-offs

### 7.1 Gate recording: reconstruct vs pass-through

**Chosen: Reconstruct in _evaluate_d1() after evaluate_trigger() returns.**

The gates are recorded AFTER evaluate_trigger returns a signal (meaning all gates passed). Since evaluate_trigger returns None on any gate failure, we only trace successful evaluations. The actual values and thresholds are all available from `state` + `D1Config` — no need to pass TraceContext into evaluate_trigger.

Trade-off: We can't trace FAILED gate evaluations (which gate blocked a signal that never fired). But that's fine — we're tracing opportunities, not non-events. If we want failed-gate analytics later, signal_events already has feature_snapshot for that.

### 7.2 No evaluate_trigger signature changes

evaluate_trigger keeps its current signature: `(snapshot, state, symbol, tf) -> Optional[Signal]`. This means:
- Zero changes to setup evaluator code
- Zero changes to backfill scripts that call evaluate_trigger directly
- All 4 setup classes (D1, S10, U1, S9) are unaffected

### 7.3 JSONB column, not separate table

- Opportunity and trace share a lifecycle (born together, expire together)
- No join overhead
- Queryable: `SELECT * FROM opportunities WHERE trace_json->>'config_hash' = 'a3f8c2d1'`
- Consistent with run_trades.trace_json pattern

### 7.4 Raw bars excluded

Candle window stores boundaries only (first_ts, last_ts, bar_count). Raw OHLCV is in the ohlcv table. Replay: `SELECT * FROM ohlcv WHERE symbol = X AND timeframe = Y AND bar_start_ts BETWEEN first_ts AND last_ts`.

### 7.5 indicator_params are per-FeatureFactory, not per-opportunity

FeatureFactory params (n_atr=14, n_bb=20, etc.) are compile-time constants. Recording them per-opportunity is redundant but cheap (~100 bytes) and makes the trace self-contained. If params ever become configurable per-setup, the trace already captures them.

---

## 8. Files Modified

| File | Change | Lines |
|------|--------|-------|
| `worker/app/trace_context.py` | NEW — TraceContext class | ~80 |
| `worker/app/signal_worker.py` | Add trace creation/recording in _evaluate_d1, _evaluate_s10, _evaluate_s9, _evaluate_u1. Add trace_json param to _persist_signal_to_pg | ~60 added |
| `docs/db/migrations/20260307_add_trace_json.sql` | NEW — Add trace_json JSONB column | ~10 |
| `docs/db/schema/mcas_schema.sql` | Add trace_json to opportunities CREATE TABLE | 1 line |

### Files NOT modified

- `d1_dormancy_ignition.py` — no evaluate_trigger signature change
- `s10_momentum_breakout.py` — same
- `u1_unicorn_ignition.py` — same
- `s9_short_reversion.py` — same
- `indicator_worker.py` — indicator computation is upstream, trace reads snapshot values
- `indicators.py` — FeatureFactory unchanged
- Backfill scripts — phase 2 (after live path works)

---

## 9. Execution Plan

### Phase 1: Core (live path)
1. Create `trace_context.py`
2. Run migration (dev first, then prod)
3. Wire TraceContext into `_evaluate_d1()` in signal_worker
4. Wire into `_persist_signal_to_pg()` — add trace_json to INSERT/UPDATE
5. Add `TRACE_ENABLED` env var (default true)
6. Deploy, verify traces appear on new D1 opportunities

### Phase 2: All strategies
7. Wire S10, U1, S9 evaluators (same pattern)
8. Verify all strategy types produce traces

### Phase 3: Backfill scripts
9. Wire trace into `backfill_d1_opportunities.py`, `s9_backfill_expired.py`, `backfill_u1_opportunities.py`
10. Re-backfill a small window to verify backfill traces match live traces

### Phase 4: Query utilities (optional, on demand)
11. API endpoint: `GET /v1/opportunities/{id}/trace` — returns formatted trace
12. Frontend: trace viewer panel on opportunity detail page

---

## 10. Tests

- `test_trace_context.py`:
  - `test_seal_produces_valid_schema` — create, record all fields, seal, validate keys
  - `test_gate_recording_order` — gates appear in recording order
  - `test_config_hash_deterministic` — same config → same hash
  - `test_trace_disabled_noop` — when TRACE_ENABLED=false, no trace created
- `test_trace_integration.py`:
  - `test_d1_opportunity_has_trace` — trigger D1 with synthetic data, verify trace_json populated
  - `test_trace_gates_all_passed` — all gates in trace have passed=true (since signal fired)
  - `test_trace_candle_window_matches_snapshot` — first_ts/last_ts match ohlcv boundaries

---

## Open Questions for Review

1. **Should payload_json slim down?** Once trace_json captures indicator values, score components, and gate evaluations, payload_json has significant overlap. Options: (a) leave both as-is (backward compatible), (b) remove redundant keys from payload_json in a follow-up, (c) migrate payload_json to JSONB while we're touching the table.

2. **Failed evaluations**: Should we trace signals that DIDN'T fire (gate failures)? Currently out of scope — signal_events covers this. But if you want "why didn't this symbol trigger D1 at this hour?", the trace system could optionally log failed evaluations to a separate table.

3. **Trace on simulation results**: Should `simulation_results` also carry a trace (or reference the opportunity's trace)? The sim already has `trace_json` on `run_trades` — might be sufficient.

4. **FeatureFactory params versioning**: Should we hash the FeatureFactory params and store as `indicator_params_hash` for quick drift detection? Cheap to add.
