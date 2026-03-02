# Migration Stubs — Pipeline Mining Waves

These are placeholder files for migrations required by the pipeline mining plan.
Each stub has its advisory lock ID reserved and dependencies documented.
Implement the actual SQL when the corresponding wave item is ready.

## Dependency Chain

```
M1 (output_hash) ─── standalone
M2 (staleness_state) ─── standalone
M3 (causal_type) ─── standalone
M4 (node_current_state TABLE) ─── depends on node + node_version + version_event
M5 (node_current_state TRIGGER) ─── depends on M4
M6 (extend event_type) ─── code change in meta_api.py, not SQL migration
M7 (meta.dataset TABLE) ─── depends on meta.node
```

## Lock ID Registry

| Lock ID | Migration | Wave |
|---------|-----------|------|
| 2026030201 | proactive_indices | 0.5 |
| 2026030301 | M1: output_hash | 3.2 |
| 2026030302 | M2: staleness_state | 3.1 |
| 2026030303 | M3: causal_type | 2.11 |
| 2026030304 | M4: node_current_state table | 2.9 |
| 2026030305 | M5: node_current_state trigger | 2.9 |
| 2026030306 | M7: meta.dataset table | 5.5 |

## Deployment Protocol

1. Apply M1-M3 in any order (standalone)
2. Apply M4, verify table exists
3. Test M5 on dev by inserting a version_event
4. Apply M5 to production
5. M6 is a code change, not a migration
6. M7 is Wave 5 (deferred)
