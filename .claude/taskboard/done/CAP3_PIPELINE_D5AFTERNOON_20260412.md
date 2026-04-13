STATUS: DONE
RESULT: cap3_pipeline module authored, 13/13 tests passing
OUTPUT:
  - /home/darren/Desktop/MCAS/Backend/governance/selection/cap3_pipeline.py
  - /home/darren/Desktop/MCAS/Backend/governance/selection/__init__.py
  - /home/darren/Desktop/MCAS/Backend/tests/test_cap3_pipeline.py
READY: D6 backfill sim consumes Cap3Pipeline.evaluate_batch

NOTES:
  - Gate order per plan §4: indicator_safe -> social_safe -> cluster_cap -> regime_enforcer
  - Short-circuit on first reject; batch API evolves open_positions on ACCEPT
  - Artefact re-read on mtime change (5s TTL) so A2-RESCOPE social filter refresh propagates without restart
  - social_velocity_fn is a stub injection point (D7 wiring deferred) — unwired => permissive pass with audit note
  - Measured evaluate() latency: 4µs (C4ML, no filters) to 9µs (C2S, 8 filters). Budget <5ms easily met.
