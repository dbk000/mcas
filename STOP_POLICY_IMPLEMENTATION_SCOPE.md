# Stop Policy Implementation Scope (Pinned)

You are Codex (implementation agent) working in the MCAS repo.

Goal:
Expand Pipeline Stop-Loss config so that when Pipeline is missing params/rows for existing setups, the system automatically “completes” Pipeline using canonical defaults already defined in the codebase — safely, deterministically, and with a full audit log of every change (from->to, reason, source).

Non-negotiables:
- Preserve existing behavior by default. No strategy’s effective stop policy may change unless an explicit Pipeline override is saved.
- Pipeline and Backend must link to the same first-source. The first-source is the canonical backend resolver/registry of defaults, not ad-hoc UI assumptions.
- No silent global bleed: editing one setup must not change other setups.
- Determinism: a run must resolve stop policy from the stored node_version snapshot, not “latest config”.
- Every automatic completion must be idempotent (safe to run repeatedly).
- Add auditability: log exactly what was added/changed, including before/after values, source of default, and where persisted.

Deliverables (implement in stages with tests):

## STAGE A — Canonical defaults registry
1) Create/extend a single canonical backend source for per-setup stop defaults (including dual-stop if applicable):
   - e.g., SetupRegistry / setup metadata / or a new stop_policy_defaults module.
   - It must be programmatically enumerable for all setup IDs.
   - It must express defaults in the same schema Pipeline uses (sl_r, stop_distance_factors keyed by setup_id, and any nested pre_tp1/post_tp1 fields).

2) Create a single resolver function contract used everywhere:
   resolve_stop_policy(setup_id, node_version_config, run_overrides=None) -> ResolvedStopPolicy
   - Resolution order:
     (1) explicit run overrides (if present)
     (2) node_version per-setup override (if present for setup_id)
     (3) canonical backend defaults for that setup_id (registry)
   - If a setup_id is unknown: hard error in sim/backtest; in paper/live emit error + refuse to run.
   - If node_version has a per-setup map but setup_id missing: do NOT use neutral 1.0; use canonical backend defaults AND emit a WARN that Pipeline is incomplete for that setup.

3) Wire simulation/backtest paths to use only this resolver (one path). Do not re-interpret stop logic in multiple places.

## STAGE B — Pipeline auto-completion (safe hydration)
4) Implement an API endpoint that returns the complete per-setup stop policy “template” derived from backend defaults:
   GET /v1/pipeline/stop_policy_template
   - Response includes all setup IDs and their canonical defaults in Pipeline schema.
   - Include as_of_ts.

5) Update the Pipeline Stop-Loss UI to:
   - Render rows for every setup from the template.
   - Clearly distinguish “default (from code)” vs “override (saved in node config)”.
   - Do NOT silently write anything to DB just by viewing. Viewing/hydration is read-only.

6) Add an explicit “Apply defaults to this node” action (button) that writes missing rows into the node config snapshot as explicit values IF AND ONLY IF user triggers it.
   - This is the safe bridge to shared-first-source: the node stores explicit snapshot values so future runs are deterministic.
   - This action must be idempotent: only fill missing entries; never overwrite existing overrides unless user confirms overwrite.
   - When applied, write a detailed audit log.

## STAGE C — Audit log + change report
7) Add an append-only audit log for stop-policy config changes:
   - Prefer DB table stop_policy_audit_events OR an existing event log mechanism if present.
   - Store: event_id, ts, actor (user/api key), node_id, node_version_id, setup_id, field_path, old_value, new_value, source ("default_registry" | "user_override" | "migration_fill"), reason.

8) For the “Apply defaults” action, generate a human-readable change report (also saved):
   - e.g., JSON blob plus a formatted text summary printed to logs.
   - Must include counts: setups filled, fields written, any skipped due to existing overrides.

## STAGE D — Tests (must pass)
9) Add targeted tests:
   - Resolver order: overrides > node_version > registry defaults.
   - No cross-strategy bleed: editing S9 does not change U1/D1/S10 resolved config.
   - Pipeline incomplete: missing setup in node_version map resolves to registry defaults (not 1.0) and emits WARN.
   - Determinism: changing registry defaults after a run is created does not alter that run’s resolved policy if node_version stored explicit snapshot (verify via node_version_id binding).

10) Add frontend tests/guards where feasible:
   - UI shows default vs override distinctively.
   - “Apply defaults” does not overwrite overrides without explicit action.

Implementation constraints:
- Keep changes per stage small and reviewable.
- Update docs/specs/contracts if behavior changes.
- Add minimal logging, no spam: one summary line per run + WARNs only for incomplete pipeline setups.
- Ensure all new code paths preserve API-first: frontend never computes defaults; it requests them from backend.
- Include a final checklist in the PR description covering: determinism, resolver single-path, audit logging, idempotency.

Output:
- Provide the exact file list changed and a short rationale for each.
- Provide commands to run tests (pytest + frontend build).
- Provide a short “migration operator guide”: how to backfill defaults into existing nodes safely and how to read audit logs.

Proceed without asking me questions. If ambiguity arises, make explicit assumptions in a short INSIGHT block and continue.

---

## Tightening Edits (Accepted)
1. Determinism must be explicit in run records
   Store `stop_policy_node_version_id` and a `stop_policy_snapshot` on run creation, then resolve from that snapshot during execution. No runtime “latest” lookup.

2. Unknown setup handling should fail early
   Hard-fail at run creation/startup (not mid-run) for sim/backtest/live/paper if any setup lacks registry coverage.

3. Use a schema-versioned stop policy
   Add `schema_version` in config/template now so dual-stop (`pre_tp1`/`post_tp1`) can roll out without breakage.

4. Audit log should be field-level + append-only
   Dedicated `stop_policy_audit_events` table is better than generic logs for diffability and operator trust.

---

## Startup Gate (Mandatory)
Add `validate_stop_registry()` as a hard startup validation in both API and worker entrypoints:
- Ensure every active strategy in `STRATEGY_REGISTRY` has stop-policy defaults.
- Ensure schema validity (`schema_version`, required fields).
- If any mismatch: fail startup with a clear error list.

This prevents drift between strategy registry and stop registry.

---

## Intrabar Conflict Guardrail (Mandatory Before Dual-Stop Rollout)
When dual-stop (`pre_tp1` / `post_tp1`) is introduced, same-bar ambiguity must be explicit and deterministic.

- Add explicit intrabar policy to config/snapshot:
  - `intrabar_policy`: `STOP_FIRST` (default), `TP_FIRST`, `OHLC_HEURISTIC`
  - `one_event_per_bar`: `true` (default)
- Same-bar stop/TP1 conflicts must resolve via that policy (never implicit order-by-code-path).
- Policy must be stored in run snapshot so historical runs remain reproducible.
- Emit WARN/audit entries for same-bar conflicts with policy + chosen outcome.
- Keep conservative default for research integrity:
  - `STOP_FIRST`
  - `one_event_per_bar=true`
- Alternative policy versions are allowed and configurable via Pipeline Stop-Loss node:
  - `STOP_FIRST` (conservative)
  - `TP_FIRST` (optimistic)
  - `OHLC_HEURISTIC` (path guess)

---

## Implemented Guardrail Status (2026-03-05 UTC)
- Startup gate wired:
  - API entrypoint validates stop registry vs strategy registry and fails boot on mismatch.
  - Worker entrypoints validate required setup coverage and fail boot on mismatch.
- Append-only audit ledger wired:
  - `meta.stop_policy_audit_events` table + indexes.
  - Field-level diffs emitted on Stop-Loss node version saves.
  - Audit read endpoint available: `GET /v1/meta/stop_policy/audit`.
- Intrabar ambiguity guardrail wired:
  - Policy enum active (`STOP_FIRST`, `TP_FIRST`, `OHLC_HEURISTIC`).
  - Same-bar conflict logic symmetric long/short.
  - Policy + one-event flag carried in run snapshots and simulation stop-policy snapshot.
