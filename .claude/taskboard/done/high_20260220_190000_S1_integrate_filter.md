REQUESTED_BY: S1
TASK: Integrate the "no social mentions 30d" precision filter into the U1 unicorn ignition setup. The filter: before accepting an ignition signal, query social_features_1h for the symbol in the last 30 days. If ANY rows exist → reject the signal. If none → accept. Modify Backend/worker/app/setups/u1_unicorn_ignition.py. The check should happen in evaluate_trigger() or equivalent — after the existing score/vol_z threshold passes, apply this additional gate. Query must be lightweight (indexed lookup, not full scan). Add the filter as a configurable gate (ENV var to enable/disable, defaulting to enabled). Do NOT change existing scoring logic, feature weights, or thresholds — this is an additive filter only.
SCOPE: Backend/worker/app/setups/u1_unicorn_ignition.py
OUTPUT_TO: n/a (code change)
PRIORITY: high
CONSTRAINT: Do not modify signal scoring. Do not modify exit logic. Only add the social silence gate. Must include the DB query for social_features_1h. Must handle the case where social_features_1h table doesn't exist (graceful fallback to accepting the signal).
AGENT_TYPE: analysis
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 19:00
COMPLETED_AT: 2026-02-20 19:05
RESULT: Filter integrated into u1_unicorn_ignition.py + signal_worker.py. Env toggle U1_SOCIAL_SILENCE_FILTER=1 (default enabled). Cached 30min per symbol.
