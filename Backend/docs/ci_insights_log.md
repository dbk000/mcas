# CI Insights Log

**Purpose:** Append-only log of correlation-driven parameter changes and their outcomes.

**Rules:**
- No edits to existing entries; corrections are new entries with `supersedes` reference
- Every claim must link to an `analysis_id` and `run_ids` window
- No speculative commentary unless it explains a measurable failure mode

**Format:** Each entry follows the structure:
- Timestamp (UTC)
- Scope (setup_id, timeframes, trade count)
- Observation (what the data showed)
- Hypothesis (why this implies a tweak)
- Change Made (exact tweak with before/after values)
- Expected Effect (what metric should move)
- Result (did it move, side effects)
- Decision (KEEP/REVERT/ITERATE with rationale)

---

