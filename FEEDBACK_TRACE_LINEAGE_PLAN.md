<START OF GEMINI FEEDBACK>
This is an institutional-grade engineering plan. Moving from "description strings in a payload" to a cryptographically hashed, structured lineage object is exactly how you transition a research project into a verifiable, audit-compliant trading engine.

Reviewing this strictly against the current MCAS architecture (specifically setup_base.py, d1_dormancy_ignition.py, and your Postgres schema), the structure is highly sound. However, there are a few micro-architectural traps in how this will interact with your existing execution flow.

Here is the SME-level adversarial review of the plan, along with direct answers to your open questions.
1. The "Fast-Fail" Trace Leak

The Context: In d1_dormancy_ignition.py, your evaluation logic is heavily optimized to fail fast. You evaluate a gate, and if it fails, you immediately return None (or return [] for cross-sectional setups).
The Trap: The plan proposes recording gates sequentially (self._record_gate(...)). If an asset fails at Gate 3, the ComputationTrace object will contain Gates 1, 2, and 3, but because the function returns None, the Opportunity is never created, and the partially built ComputationTrace is instantly garbage-collected by Python.
The Fix: You must explicitly define where failed traces go. If an opportunity does fire, the trace goes to the opportunities table. If an opportunity fails, the trace should be dumped into your existing signal_events table (which was added in the 20260207_add_signal_events.sql migration for this exact reason). Do not pollute the opportunities table with failed signals.
2. The Deterministic JSON Hashing Trap

The Context: The plan calls for creating a trace_hash (SHA-256) of the trace_json to seal the opportunity.
The Trap: Python dictionaries do not guarantee deterministic key ordering across different environments, Python versions, or JSON serialization calls. If you just call hashlib.sha256(json.dumps(trace_dict).encode()), Worker A might generate a different hash than the API Server for the exact same data due to whitespace or key order.
The Fix: You must enforce strict cryptographic serialization in your Trace model:
Python

# Must use sort_keys and remove whitespace
trace_string = json.dumps(trace_dict, sort_keys=True, separators=(',', ':'))
trace_hash = hashlib.sha256(trace_string.encode('utf-8')).hexdigest()

3. The D1State Ephemerality

The Context: D1 tracks state via an in-memory dictionary (self.states[symbol] = D1State(...)).
The Trap: When the signal fires, the plan captures phase and dormancy_confirmed_at_bar. But remember that signal_worker.py runs amnesiac loops if the Docker container restarts. If a container bounces between the dormancy phase and the ignition phase, that state is lost/rebuilt.
The Fix: The trace must log whether the state was purely continuous in-memory, or if it was re-hydrated/inferred from historical lookback on boot. Add a boolean to the trace: state_is_hydrated: bool.
Answers to Your Open Questions

1. Should payload_json slim down?
Answer: DO NOT TOUCH payload_json (Option A).
payload_json is currently acting as your Presentation Layer. Your React frontend (specifically OpportunityDetail.jsx and OpportunityCurveChart.jsx) relies heavily on those flat keys to render the UI. trace_json is your Provenance Layer (for the database and backtester). If you strip the frontend payload to save a few bytes, you will break 5 different React components. Keep them separate: Payload for UI, Trace for Audit.

2. Failed evaluations: Should we trace signals that DIDN'T fire?
Answer: YES, but route them to signal_events.
As mentioned above, do not put them in opportunities. Having a structured JSON trace of why an asset failed Gate 4 at exactly 10:00 AM is incredibly valuable for debugging "missed trades" (like your WS1-missed-trades-autopsy.md), but it belongs in the telemetry/events schema.

3. Trace on simulation results: Should simulation_results carry a trace?
Answer: NO.
simulation_results and run_trades already carry a foreign key pointing to opportunity_id. Duplicating a large JSONB trace into the simulation tables will exponentially bloat your database for zero analytical gain. Just perform a SQL JOIN opportunities ON opportunities.id = run_trades.opportunity_id when you need to view the trace of a simulated trade.

4. FeatureFactory params versioning: Should we hash the FeatureFactory params?
Answer: YES (Mandatory).
This is the most important open question you asked. If you change the underlying ATR window in indicators.py from 14 to 20, but leave the D1Config version the same, your backtest will drift silently. You must serialize the IndicatorConfig or FeatureFactory parameters and inject them into the trace_json.parameters block before hashing.

<END OF GEMINI FEEDBACK>

<START OF BIG BRO CLAUDE'S FEEDBACK>
My Review of CC's Plan
What's solid:
The TraceContext-as-observer pattern is exactly right. Non-invasive, feature-flagged, zero overhead when disabled, no evaluate_trigger signature changes — all correct calls. JSONB on opportunities (not a separate table) is the right storage decision for the lifecycle reasons CC stated. The phased execution plan is sensible. Excluding raw OHLCV bars and using pointer+replay is the right space/completeness tradeoff.
Gaps CC missed:
1. The config_hash is dangerously short. "a3f8c2d1" is 8 hex chars = 32 bits. At that length you'll get collisions within a few thousand configs. Should be 16 chars minimum (64-bit). This is in the schema example and will propagate into production as-is.
2. No whole-trace integrity hash. CC records a config_hash but never seals the entire trace with a hash. If you ever want to prove a trace hasn't been tampered with post-write, you need trace_hash = sha256(sort_keys=True, no whitespace) over the full sealed dict. Right now the trace is mutable after the fact with no way to detect it.
3. The promotion threshold is absent from the score block. The score block records raw, calibration_mult, and calibrated — but never records what threshold the calibrated score was compared against. If the promotion threshold changes between two batches, the trace alone can't explain why an opp fired in one run and not another. Add promotion_threshold: float to the score block.
4. S9 cross-sectional trace schema is underspecified. CC acknowledges S9 in section 5.3 but doesn't show the schema variant. S9 evaluates the whole universe and selects symbols — the symbol at trace root doesn't fit cleanly. This needs a universe_size, breadth, decile_threshold at the top level with per-symbol sub-objects. Leaving this vague means CC will make an ad-hoc decision during implementation that may not be queryable.
5. No trace schema version migration path. The "v": "1.0" field is there, but there's no plan for what happens when v1.1 is needed — no TRACE_SCHEMA_VERSION constant, no migration strategy. This bites you the first time you add a field.
6. Candle window records boundaries but not timeframe at indicator level. D1 uses daily bars but some indicators (funding rate, HTF context) may reference different timeframes. candle_window has a single timeframe field. If multiple timeframes contributed to the evaluation, the window is ambiguous.

On Gemini's Review
Gemini's four points are good, but one of them misreads CC's plan:
Gemini Point 1 (fast-fail trace leak) — marked as "the trap," but CC's section 7.1 explicitly solves it: gates are reconstructed after evaluate_trigger() returns a signal, not during. If evaluate_trigger returns None, no trace is written at all. Gemini either didn't read 7.1 carefully or is reviewing an older version of the plan. Not actually a problem.
Gemini Point 2 (deterministic hashing) — Valid and important. json.dumps(d) without sort_keys=True, separators=(',', ':') is non-deterministic across environments. Mandatory fix for the trace_hash to mean anything.
Gemini Point 3 (state_is_hydrated flag) — This is a genuine and subtle gap CC missed. D1State can be rebuilt from historical lookback on container restart. If that happens, dormancy_confirmed_at_bar is inferred, not observed. The trace doesn't currently distinguish between "I watched this dormancy build bar by bar" and "I reconstructed it from 200 bars of history on boot." For audit purposes that distinction matters.
Gemini Point 4 (FeatureFactory params versioning) — Correct and CC actually already addresses this in section 7.5 (indicator_params block in the schema). Gemini is flagging a gap that's already closed. But Gemini's specific recommendation to also store a indicator_params_hash for drift detection is a cheap addendum worth including.

Combined / Reconciled Plan — Delta Changes Only
Everything in CC's plan stands. Apply these additions:
#ChangeWhereSource1config_hash → 16 hex chars minimumtrace_context.py seal()Mine2Add trace_hash over full sealed dict using sort_keys=True, separators=(',',':')trace_context.py seal()Mine + Gemini3Add promotion_threshold to score blockSchema + record_score()Mine4Add state_is_hydrated: bool to state blockD1State.to_dict() + record_state()Gemini5Define S9 cross-sectional trace schema explicitly before implementationPlan section 5.3Mine6Add TRACE_SCHEMA_VERSION = "1.0" constant + doc on how to bumptrace_context.pyMine7Add indicator_params_hash (sha256 of indicator_params block)Schema + seal()Gemini8Expand candle_window to support multiple timeframe entries if evaluation touches >1 TFSchemaMine
The only Gemini point to discard is Point 1 (fast-fail leak) — it's already handled by CC's reconstruct-after-success design in section 7.1.
<END OF BIG BRO CLAUDE'S FEEDBACK>