# Computation Trace / Lineage System — Plan v2 (Post-Review)

## Goal

Every opportunity carries a sealed audit trace: a structured record of every computation from the first candle used by any indicator through to the final promotion decision. The trace is attached at birth, travels with the opportunity forever, and is queryable via Postgres JSONB operators. A cryptographic hash over the sealed trace guarantees post-write tamper detection.

---

## Review Changelog (v1 -> v2)

All changes below come from the reconciled Gemini + Big Bro review. Gemini Point 1 (fast-fail trace leak) was discarded — already handled by the reconstruct-after-success design in section 7.1.

| # | Change | Source |
|---|--------|--------|
| 1 | `config_hash` -> 16 hex chars minimum (was 8 = 32 bits, collision-prone) | Big Bro |
| 2 | Add `trace_hash` over full sealed dict using `sort_keys=True, separators=(',',':')` | Big Bro + Gemini |
| 3 | Add `promotion_threshold` to score block | Big Bro |
| 4 | Add `state_is_hydrated: bool` to state block | Gemini |
| 5 | Define S9 cross-sectional trace schema explicitly | Big Bro |
| 6 | Add `TRACE_SCHEMA_VERSION = "1.0"` constant + bump docs | Big Bro |
| 7 | Add `indicator_params_hash` (sha256 of indicator_params block) for drift detection | Gemini |
| 8 | Expand `candle_window` to support multiple timeframe entries | Big Bro |

Open questions resolved:
- **Q1 (payload_json)**: Don't touch it. payload_json = Presentation Layer (frontend), trace_json = Provenance Layer (audit). Keep separate.
- **Q2 (failed evaluations)**: Yes, route to `signal_events` table, NOT opportunities. Phase 2+.
- **Q3 (simulation trace)**: No. simulation_results JOINs to opportunities via FK. No duplication needed.
- **Q4 (FeatureFactory params hash)**: Yes, mandatory. `indicator_params_hash` added to schema + seal().

---

## 1. Current State Analysis

### What already exists on opportunities

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
| FeatureFactory parameters + hash | n_atr=14, n_bb=20, etc. — assumed constant but not recorded; no drift detection |
| Config version/hash (collision-safe) | Can't determine if D1Config changed between two opportunities |
| Indicator computation timestamp | When were the indicators that fed this evaluation computed? |
| Sealed timestamp + integrity hash | When was the opportunity written to DB? Was the trace tampered with post-write? |
| State hydration provenance | Was D1State built from continuous observation or reconstructed from history on container restart? |
| Promotion threshold | What calibrated score threshold did the opp need to exceed? |
| Multi-timeframe candle windows | Some evaluations touch >1 timeframe (HTF context, funding rates) |

---

## 2. Architecture

### TraceContext Pattern

```
Opportunity Evaluation Pipeline
         |
    TraceContext()              <- created at start of evaluation
         |
    IndicatorSnapshot read ---> trace.record_indicators(snapshot)
         |
    compute_state() ----------> trace.record_state(state, is_hydrated)
         |
    evaluate_trigger() -------> [returns Signal or None]
         |                      If None: trace is garbage-collected (no persistence)
    [Signal returned] --------> trace.record_gate(name, value, threshold, passed)
                                trace.record_gate(...)  (per gate, reconstructed)
         |
    Score computation --------> trace.record_score(components, raw, calibrated, promotion_threshold)
         |
    Promotion ----------------> trace.seal()  # produces trace_hash
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

### payload_json stays untouched

payload_json is the Presentation Layer — the React frontend (`OpportunityDetail.jsx`, `OpportunityCurveChart.jsx`) relies on its flat keys. trace_json is the Provenance Layer — for DB, auditor, backtester. No overlap removal, no migration.

### Feature flag

`TRACE_ENABLED` env var (default: `true`). When false, no TraceContext is created, no trace_json written. Zero overhead in the hot path when disabled.

---

## 3. Trace Schema

### TRACE_SCHEMA_VERSION

A constant `TRACE_SCHEMA_VERSION = "1.0"` lives in `trace_context.py`. All sealed traces embed this value as `"v"`. When the schema changes:
1. Bump `TRACE_SCHEMA_VERSION` to `"1.1"` (additive) or `"2.0"` (breaking)
2. Add a note to the constant documenting what changed
3. Query code handles both versions: `WHERE trace_json->>'v' = '1.0'`

### D1 / S10 / U1 Schema (per-symbol strategies)

```json
{
  "v": "1.0",
  "strategy": "D1",
  "symbol": "XYZUSDT",
  "timeframe": "1h",
  "eval_ts": 1709280000.0,

  "candle_window": [
    {
      "timeframe": "1h",
      "first_ts": "2024-01-31T01:00:00Z",
      "last_ts": "2024-03-01T08:00:00Z",
      "bar_count": 201
    }
  ],

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
  "indicator_params_hash": "a3f8c2d1e9b04f17",

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
    "bar_index": 201,
    "is_hydrated": false
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
    "calibrated": 0.50,
    "promotion_threshold": 0.30
  },

  "config_hash": "a3f8c2d1e9b04f17",
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
  },

  "trace_hash": "e4d909c290d0fb1ca068ffaddf22cbd0d3f5e6e57e7b2a06c24a68ea6ce1ab77"
}
```

### S9 Cross-Sectional Schema

S9 evaluates the whole universe and selects symbols from the top decile. The trace captures universe-level context at the top and per-symbol detail in a nested object.

```json
{
  "v": "1.0",
  "strategy": "S9",
  "symbol": "XYZUSDT",
  "timeframe": "1h",
  "eval_ts": 1709280000.0,

  "candle_window": [
    {
      "timeframe": "1h",
      "first_ts": "2025-06-08T00:00:00Z",
      "last_ts": "2025-06-08T04:00:00Z",
      "bar_count": 5
    },
    {
      "timeframe": "1s",
      "first_ts": "2025-06-08T04:00:01Z",
      "last_ts": "2025-06-08T04:05:00Z",
      "bar_count": 300,
      "note": "microstructure tape"
    }
  ],

  "cross_sectional_context": {
    "universe_size": 284,
    "breadth": 0.45,
    "breadth_threshold": 0.30,
    "decile_threshold_ret_4h": 0.042,
    "symbols_in_decile": 28,
    "eval_hour": "2025-06-08T03:00:00Z"
  },

  "indicator_snapshot": {
    "ret_4h": 0.068,
    "ret_24h": 0.12,
    "close": 0.00456,
    "rank": 3
  },

  "indicator_params": {
    "ret_4h_window": 4,
    "ret_24h_window": 24,
    "breadth_threshold": 0.30,
    "decile_pct": 0.10
  },
  "indicator_params_hash": "b7c3d4e5f6a01234",

  "state": {
    "is_hydrated": false
  },

  "gates": [
    {"gate": "breadth",    "value": 0.45,  "threshold": 0.30,  "op": ">",  "passed": true},
    {"gate": "top_decile", "value": 3,     "threshold": 28,    "op": "<=", "passed": true, "note": "rank <= decile_size"},
    {"gate": "ret_4h_positive", "value": 0.068, "threshold": 0.0, "op": ">", "passed": true}
  ],

  "microstructure": {
    "n_klines": 300,
    "tape_start_ts": "2025-06-08T04:00:01Z",
    "tape_end_ts": "2025-06-08T04:05:00Z",
    "entry_type": "limit",
    "suggested_entry_price": 0.00450
  },

  "score": {
    "components": {"ret_4h": 0.068, "rank": 3},
    "raw": 0.75,
    "calibration_mult": 1.0,
    "calibrated": 0.75,
    "promotion_threshold": 0.0
  },

  "config_hash": "c4d5e6f7a8b91234",
  "config_snapshot": {
    "BREADTH_THRESHOLD": 0.30,
    "DECILE_PCT": 0.10,
    "STOP_PCT": 0.10,
    "TP1_PCT": 0.05,
    "R_VALUE": 0.5
  },

  "promotion": {
    "setup_version": "S9_v1.0",
    "sealed_at": 1709280305.0
  },

  "trace_hash": "f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a2"
}
```

### What's NOT in the trace (by design)

- **Raw OHLCV bars**: 201 bars x ~100 bytes = 20KB per opp. Bars are in the `ohlcv` table. Trace records `candle_window` boundaries — replay from ohlcv table if needed.
- **HTF candles**: Same reasoning — but the candle_window array records which timeframes were used with their boundaries.
- **Duplicate payload fields**: indicator_snapshot in trace overlaps flat keys in payload_json. Both are kept — payload_json for frontend, trace_json for audit. No removal.
- **Failed evaluations**: Not in opportunities. Phase 2: route to signal_events table.
- **Simulation traces**: Not duplicated. simulation_results JOINs to opportunities via FK.

### Per-strategy trace variants

| Strategy | indicator_snapshot keys | state keys | gates | Extra |
|----------|----------------------|------------|-------|-------|
| D1 | dormancy_score, bb_width, volume_z, rvol, accum_divergence, donchian_*, atr, rsi | D1State.to_dict() + is_hydrated | 5-6 gates | — |
| S10 | macd_histogram, macd_prev_histogram, rvol, sma_20 | S10 state + is_hydrated | MACD crossover, volume, trend | — |
| U1 | rel_volume, compression_box_*, trigger_path specifics | U1 state + is_hydrated | U1 phase gates | — |
| S9 | ret_4h, ret_24h, close, rank | minimal (cross-sectional) | breadth, decile, ret_4h | cross_sectional_context, microstructure |

The TraceContext `record_indicator()` and `record_gate()` methods are generic — each setup calls them with its own keys.

---

## 4. Implementation: TraceContext Class

**New file: `Backend/worker/app/trace_context.py`**

```python
import time
import hashlib
import json
from dataclasses import dataclass, field

TRACE_SCHEMA_VERSION = "1.0"
# v1.0 — initial schema. If you add fields, bump to 1.1 (additive).
# If you change field semantics or remove fields, bump to 2.0 (breaking).
# Query code must handle all live versions.


def _deterministic_json(d: dict) -> str:
    """Canonical JSON serialization for cryptographic hashing.

    Guarantees identical output across Python versions, environments,
    and serialization calls for the same data.
    """
    return json.dumps(d, sort_keys=True, separators=(',', ':'))


@dataclass
class TraceContext:
    """Computation provenance collector for opportunity lineage.

    Created at the start of each opportunity evaluation. Each pipeline
    step pushes its breadcrumb. Sealed at promotion time -> serialised
    to JSONB -> stored on the opportunity row. A SHA-256 hash over the
    sealed dict provides tamper detection.
    """
    strategy: str = ""
    symbol: str = ""
    timeframe: str = ""
    eval_ts: float = field(default_factory=time.time)

    _candle_windows: list = field(default_factory=list)
    _indicators: dict = field(default_factory=dict)
    _indicator_params: dict = field(default_factory=dict)
    _state: dict = field(default_factory=dict)
    _gates: list = field(default_factory=list)
    _score: dict = field(default_factory=dict)
    _config: dict = field(default_factory=dict)
    _config_hash: str = ""
    _cross_sectional_context: dict = field(default_factory=dict)
    _microstructure: dict = field(default_factory=dict)
    _promotion: dict = field(default_factory=dict)
    _sealed: bool = False

    def record_candle_window(self, first_ts, last_ts, bar_count: int,
                             timeframe: str, note: str = None):
        entry = {
            "timeframe": timeframe,
            "first_ts": str(first_ts),
            "last_ts": str(last_ts),
            "bar_count": bar_count,
        }
        if note:
            entry["note"] = note
        self._candle_windows.append(entry)

    def record_indicator(self, name: str, value):
        self._indicators[name] = value

    def record_indicators_bulk(self, snapshot_dict: dict):
        self._indicators.update(snapshot_dict)

    def record_indicator_params(self, params: dict):
        self._indicator_params = params

    def record_state(self, state_dict: dict, is_hydrated: bool = False):
        self._state = state_dict
        self._state["is_hydrated"] = is_hydrated

    def record_gate(self, gate: str, value, threshold, op: str,
                    passed: bool, note: str = None):
        entry = {"gate": gate, "value": value, "threshold": threshold,
                 "op": op, "passed": passed}
        if note:
            entry["note"] = note
        self._gates.append(entry)

    def record_score(self, components: dict, raw: float,
                     calibration_mult: float, calibrated: float,
                     promotion_threshold: float = 0.0):
        self._score = {
            "components": components,
            "raw": round(raw, 6),
            "calibration_mult": round(calibration_mult, 4),
            "calibrated": round(calibrated, 6),
            "promotion_threshold": round(promotion_threshold, 4),
        }

    def record_config(self, config_dict: dict):
        self._config = config_dict
        raw = _deterministic_json(config_dict)
        self._config_hash = hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]

    def record_cross_sectional_context(self, ctx: dict):
        self._cross_sectional_context = ctx

    def record_microstructure(self, micro: dict):
        self._microstructure = micro

    def seal(self, setup_version: str) -> dict:
        """Seal the trace at promotion time. Returns the trace dict
        with a SHA-256 integrity hash over the full contents."""
        self._sealed = True
        self._promotion = {
            "setup_version": setup_version,
            "sealed_at": time.time(),
        }

        # Build indicator_params_hash for drift detection
        indicator_params_hash = ""
        if self._indicator_params:
            raw = _deterministic_json(self._indicator_params)
            indicator_params_hash = hashlib.sha256(
                raw.encode('utf-8')).hexdigest()[:16]

        trace = {
            "v": TRACE_SCHEMA_VERSION,
            "strategy": self.strategy,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "eval_ts": self.eval_ts,
            "candle_window": self._candle_windows,
            "indicator_snapshot": self._indicators,
            "indicator_params": self._indicator_params,
            "indicator_params_hash": indicator_params_hash,
            "state": self._state,
            "gates": self._gates,
            "score": self._score,
            "config_hash": self._config_hash,
            "config_snapshot": self._config,
        }

        # Include optional cross-sectional / microstructure blocks
        if self._cross_sectional_context:
            trace["cross_sectional_context"] = self._cross_sectional_context
        if self._microstructure:
            trace["microstructure"] = self._microstructure

        trace["promotion"] = self._promotion

        # Compute integrity hash over everything before trace_hash
        trace_string = _deterministic_json(trace)
        trace["trace_hash"] = hashlib.sha256(
            trace_string.encode('utf-8')).hexdigest()

        return trace
```

**Size estimate**: ~2-4KB per opportunity (no raw bars). S9 with cross_sectional_context: ~3-5KB. 100K opps = ~400MB max. Negligible relative to OHLCV table size.

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

        # --- TRACE: indicator params (FeatureFactory constants) ---
        if trace:
            trace.record_indicator_params({
                "n_atr": 14, "n_rsi": 14, "n_bb": 20,
                "k_bb": 2.0, "n_kc": 20, "n_volz": 20,
            })

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
        # is_hydrated = True if state was rebuilt from history on container restart
        if trace:
            is_hydrated = getattr(state, '_hydrated', False)
            trace.record_state(state.to_dict(), is_hydrated=is_hydrated)

        sig = self.d1_evaluator.evaluate_trigger(snapshot, state, symbol, tf)
        if sig is None:
            return  # trace is garbage-collected — no persistence

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

        # --- TRACE: score (with promotion_threshold) ---
        calib_mult = SETUP_SCORE_MULTIPLIERS.get("D1", 1.0)
        calibrated_score = min(sig.setup_score * calib_mult, 1.0)
        if trace:
            trace.record_score(sig.score_components, sig.setup_score,
                               calib_mult, calibrated_score,
                               promotion_threshold=0.0)

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

S9 is cross-sectional with microstructure. Additional trace methods used:

```python
# After computing universe-level metrics:
if trace:
    trace.record_cross_sectional_context({
        "universe_size": len(universe_symbols),
        "breadth": breadth,
        "breadth_threshold": S9Config.BREADTH_THRESHOLD,
        "decile_threshold_ret_4h": decile_threshold,
        "symbols_in_decile": len(top_decile),
        "eval_hour": eval_hour_iso,
    })

# For each promoted symbol, add microstructure:
if trace and micro_data:
    trace.record_microstructure({
        "n_klines": micro_data.get("n_klines"),
        "tape_start_ts": tape_start_iso,
        "tape_end_ts": tape_end_iso,
        "entry_type": micro_data.get("entry_type"),
        "suggested_entry_price": micro_data.get("suggested_entry_price"),
    })

# Candle window includes both 1h and 1s windows:
if trace:
    trace.record_candle_window(first_ts=..., last_ts=...,
                               bar_count=5, timeframe="1h")
    trace.record_candle_window(first_ts=tape_start, last_ts=tape_end,
                               bar_count=300, timeframe="1s",
                               note="microstructure tape")
```

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

### 5.6 Backfill scripts (Phase 3)

Each backfill script (d1_backfill, s9_backfill, u1_backfill) has its own opportunity creation path. These also need TraceContext injection — same pattern, different entry point. Done incrementally after the live path is working.

---

## 6. State Hydration Detection

D1 tracks state via an in-memory dictionary (`self.states[symbol] = D1State(...)`). On container restart, the amnesiac loop rebuilds state from historical lookback. The trace must distinguish:

- **Continuous observation** (`is_hydrated: false`): State was built bar by bar from the live stream. `dormancy_confirmed_at_bar` is a true observation.
- **Reconstructed from history** (`is_hydrated: true`): State was rebuilt from OHLCV lookback on boot. `dormancy_confirmed_at_bar` is inferred, not observed.

Implementation: When signal_worker boots and reconstructs D1State from historical bars, set `state._hydrated = True`. States that transition through normal live evaluation keep the default `_hydrated = False`. The trace records this via `record_state(state.to_dict(), is_hydrated=state._hydrated)`.

---

## 7. DB Migration

**File: `Backend/docs/db/migrations/20260307_add_trace_json.sql`**

```sql
BEGIN;
SELECT pg_advisory_lock(20260307);

-- Add JSONB trace column (nullable -- existing opps have no trace)
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS trace_json JSONB DEFAULT NULL;

-- Comment for documentation
COMMENT ON COLUMN opportunities.trace_json IS
    'Sealed computation trace: candle window, indicators, gates, score, config at trigger time. Schema v1.0. Includes trace_hash (SHA-256) for tamper detection.';

SELECT pg_advisory_unlock(20260307);
COMMIT;
```

No index initially — we don't query by trace content in any hot path. Add GIN index later if analytical queries need it.

---

## 8. Design Decisions & Trade-offs

### 8.1 Gate recording: reconstruct vs pass-through

**Chosen: Reconstruct in _evaluate_d1() after evaluate_trigger() returns.**

The gates are recorded AFTER evaluate_trigger returns a signal (meaning all gates passed). Since evaluate_trigger returns None on any gate failure, we only trace successful evaluations. The actual values and thresholds are all available from `state` + `D1Config` — no need to pass TraceContext into evaluate_trigger.

Trade-off: We can't trace FAILED gate evaluations on the opportunity. Failed-gate analytics go to signal_events (Phase 2).

### 8.2 No evaluate_trigger signature changes

evaluate_trigger keeps its current signature: `(snapshot, state, symbol, tf) -> Optional[Signal]`. This means:
- Zero changes to setup evaluator code
- Zero changes to backfill scripts that call evaluate_trigger directly
- All 4 setup classes (D1, S10, U1, S9) are unaffected

### 8.3 JSONB column, not separate table

- Opportunity and trace share a lifecycle (born together, expire together)
- No join overhead
- Queryable: `SELECT * FROM opportunities WHERE trace_json->>'config_hash' = 'a3f8c2d1e9b04f17'`
- Consistent with run_trades.trace_json pattern

### 8.4 Raw bars excluded

Candle window stores boundaries only (first_ts, last_ts, bar_count, timeframe). Raw OHLCV is in the ohlcv table. Replay: `SELECT * FROM ohlcv WHERE symbol = X AND timeframe = Y AND bar_start_ts BETWEEN first_ts AND last_ts`.

### 8.5 indicator_params recorded + hashed

FeatureFactory params (n_atr=14, n_bb=20, etc.) are compile-time constants. Recording them per-opportunity is redundant but cheap (~100 bytes) and makes the trace self-contained. The `indicator_params_hash` enables quick drift detection: if the hash changes between two opps, the underlying indicator math changed.

### 8.6 Deterministic hashing

All hashes (config_hash, indicator_params_hash, trace_hash) use `json.dumps(d, sort_keys=True, separators=(',', ':'))` before SHA-256. This guarantees identical hashes across Python versions, environments, and serialization calls for the same data. No whitespace, sorted keys, UTF-8 encoding.

### 8.7 trace_hash seals the full trace

`trace_hash` is SHA-256 over the entire trace dict (excluding trace_hash itself). If any field is modified post-write, recomputing the hash from the stored trace will not match the stored trace_hash. This provides tamper detection without needing a separate signing infrastructure.

---

## 9. Files Modified

| File | Change | Lines |
|------|--------|-------|
| `worker/app/trace_context.py` | NEW — TraceContext class + TRACE_SCHEMA_VERSION + _deterministic_json | ~120 |
| `worker/app/signal_worker.py` | Add trace creation/recording in _evaluate_d1, _evaluate_s10, _evaluate_s9, _evaluate_u1. Add trace_json param to _persist_signal_to_pg. Add is_hydrated tracking on state rebuild. | ~80 added |
| `docs/db/migrations/20260307_add_trace_json.sql` | NEW — Add trace_json JSONB column | ~10 |
| `docs/db/schema/mcas_schema.sql` | Add trace_json to opportunities CREATE TABLE | 1 line |

### Files NOT modified

- `d1_dormancy_ignition.py` — no evaluate_trigger signature change
- `s10_momentum_breakout.py` — same
- `u1_unicorn_ignition.py` — same
- `s9_short_reversion.py` — same
- `indicator_worker.py` — indicator computation is upstream, trace reads snapshot values
- `indicators.py` — FeatureFactory unchanged
- `opportunity_simulator.py` — simulation traces not duplicated (JOIN via FK)
- Backfill scripts — Phase 3 (after live path works)

---

## 10. Execution Plan

### Phase 1: Core (live path)
1. Create `trace_context.py` with `TRACE_SCHEMA_VERSION`, `_deterministic_json`, `TraceContext`
2. Run migration (dev first, then prod)
3. Wire TraceContext into `_evaluate_d1()` in signal_worker
4. Wire into `_persist_signal_to_pg()` — add trace_json to INSERT/UPDATE
5. Add `TRACE_ENABLED` env var (default true)
6. Add `_hydrated` flag to D1State reconstruction path
7. Deploy, verify traces appear on new D1 opportunities
8. Verify trace_hash integrity: re-serialize stored trace, recompute hash, confirm match

### Phase 2: All strategies
9. Wire S10, U1, S9 evaluators (same pattern, with cross_sectional_context + microstructure for S9)
10. Verify all strategy types produce traces

### Phase 3: Backfill scripts
11. Wire trace into `backfill_d1_opportunities.py`, `s9_backfill_expired.py`, `backfill_u1_opportunities.py`
12. Re-backfill a small window to verify backfill traces match live traces

### Phase 4: Query utilities (optional, on demand)
13. API endpoint: `GET /v1/opportunities/{id}/trace` — returns formatted trace
14. Frontend: trace viewer panel on opportunity detail page
15. Trace integrity verification endpoint: recompute hash, compare to stored

---

## 11. Tests

- `test_trace_context.py`:
  - `test_seal_produces_valid_schema` — create, record all fields, seal, validate all keys present
  - `test_gate_recording_order` — gates appear in recording order
  - `test_config_hash_deterministic` — same config -> same hash, 16 hex chars
  - `test_config_hash_length` — config_hash is exactly 16 hex chars (64-bit)
  - `test_trace_hash_integrity` — seal, remove trace_hash, re-serialize, recompute sha256, confirm match
  - `test_trace_hash_detects_tampering` — modify one field post-seal, recompute hash, confirm mismatch
  - `test_indicator_params_hash` — same params -> same hash; different params -> different hash
  - `test_deterministic_json_ordering` — dicts with different insertion order produce identical serialization
  - `test_candle_window_multi_timeframe` — multiple record_candle_window calls produce list
  - `test_state_is_hydrated_flag` — is_hydrated appears in sealed state block
  - `test_promotion_threshold_in_score` — promotion_threshold present in score block
  - `test_schema_version_constant` — TRACE_SCHEMA_VERSION == "1.0" in sealed trace
  - `test_trace_disabled_noop` — when TRACE_ENABLED=false, no trace created
  - `test_cross_sectional_context` — S9-style trace includes cross_sectional_context block
- `test_trace_integration.py`:
  - `test_d1_opportunity_has_trace` — trigger D1 with synthetic data, verify trace_json populated
  - `test_trace_gates_all_passed` — all gates in trace have passed=true (since signal fired)
  - `test_trace_candle_window_matches_snapshot` — first_ts/last_ts match ohlcv boundaries
