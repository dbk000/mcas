# R6 — Tone-Drift Monitor — Findings

**Item:** R6 (playbook §5)
**Outputs shipped:**
- `Backend/governance/equities/tone_drift_monitor.py` — `ToneDriftMonitor` class + `compute_kl_divergence` + `trigger_active` + `load_baseline` + `build_baseline_artifact`
- `Backend/scripts/equities_tone_drift_report.py` — scheduled entry point (`--build-baseline` / `--report`)
- `Backend/tests/governance/test_tone_drift.py` — 17 unit tests, all passing

**Validation:**
- pytest: **17 / 17 passed** (see `pytest_test_tone_drift.log`)
- ci_gates: **CLEAN** on all three files (see `ci_gates_tone_drift.log`)

## Decisions made

1. **KL binning = 21 bins of width 0.1 across [-1.0, +1.0].** `score_title_tone` clamps output to that range. Central bin (-0.05, +0.05] captures the dominant zero-score mass from titles with no keyword hits. See `kl_divergence_derivation.md` for the full derivation and rejected alternatives (JSD, EMD, chi-squared).
2. **Laplace smoothing with α=1.** Eliminates log-of-zero failure modes while shifting mass ≤ 5% for N ≥ 420 events (well under typical rolling-window sample sizes of ~2k–10k events).
3. **`baseline_kl` computed from 6 held-out disjoint chunks of the training window.** Rather than the degenerate self-KL (which is 0), the baseline stores the average KL of non-overlapping training chunks against the pooled training distribution. This gives the trigger a realistic yardstick: 2× typical KL between similar-distribution samples. Chunk count of 6 balances chunk-size noise against chunk-count stability.
4. **`MIN_TRIGGER_ABS_KL = ln(2) / 100 ≈ 0.00693 nats` floor.** Safety net for the case where `baseline_kl = 0.0` (no held-out chunks provided or pool too small). Review §2.6 says "2× baseline"; if baseline is 0 then 2× is also 0 and any noise triggers. The floor represents a 1% relative entropy shift over 21 bins — the minimum drift we care about detecting.
5. **Brief data-source deviation.** Playbook §5 step 2 says "reads last 30 days of tone scores from `equities_feature_events`". Tone scores are **not persisted** in `equities_feature_events` — that table's `features` JSONB contains `sentiment_score`, `event_count_*`, `anti_bot_mean_*`, but no tone column. Tone is computed on-the-fly inside `Backend/governance/equities/features.py:318` from `equities_events.title`. R6 honours the brief's intent by re-scoring titles on demand from `equities_events`. This is a documented deviation, not a silent swap.
6. **Facade class `ToneDriftMonitor` + pure helpers.** The module exposes both a pure functional API (`compute_kl_divergence`, `trigger_active`, `load_baseline`, `build_baseline_artifact`) and a small stateful facade (`ToneDriftMonitor.from_path(path)`). The facade matches the playbook's `ToneDriftMonitor(BASELINE_PATH=...)` brief; the pure functions enable the test harness to avoid JSON round-trips on every assertion.
7. **Baseline artifact schema v1.0.** Required fields: `schema_version`, `baseline_kl`, `bin_edges`, `distribution`, `n_samples`, `built_at`. `load_baseline` validates all six; missing → `ValueError` with detail. Mismatched `schema_version` → `ValueError`. Mismatched `bin_edges` → `ValueError`. No silent migrations.
8. **Trigger reads tail of history.** `trigger_active(history, baseline)` only inspects the final `CONSECUTIVE_DAYS_TO_TRIGGER=5` entries. Shorter history → False (cannot trigger on insufficient observations). Mixed history (any dip below threshold in the tail) → False.
9. **Alert row shape.** When the trigger fires, the script writes one row to `equities_pipeline_events`: `actor='tone_drift_monitor'`, `event_type='TONE_DRIFT_ALERT'`, `message` summarising threshold breach, `data` JSONB containing baseline_kl, threshold, recent_kls, n_samples_today, schema_version. Format aligns with the existing 136K-row `equities_pipeline_events` audit log.
10. **`# EXEMPT:` comment on `sys.path.insert` in the script.** The CI gate rule #17 (SYS_PATH_ACCESS) fires on `sys.path` manipulation. Scripts legitimately need to insert `Backend/` for standalone invocation; the exemption is explicit and inline, matching the existing precedent in `scripts/s9_paper_reconcile.py`, `scripts/sweep_s10_full_matrix.py`, etc.

## Data sampled

### `tone_scorer.py` fully read

See `tone_scorer_summary.md` in this directory. Key facts:
- Output range: **[-1.0, +1.0]** (explicitly clamped on line 213).
- 4 categories (ANALYST, EARNINGS, GUIDANCE, INSIDER) with weights 1.0/0.9/0.9/0.8 and ±1 polarity.
- Filters: non-OPEN_NEWS → 0.0; anti_bot > 0.5 → 0.0; < 3 tokens → 0.0; init failure → 0.0.
- Negation inverts polarity within 3-token preceding window.
- Expected empirical distribution: heavy zero mass (60–90%), then discrete values at ±0.8/±0.9/±1.0 plus clamped saturation at ±1.0.

### pytest: 17 / 17 passed

```
test_kl_of_identical_distribution_is_zero            PASSED
test_kl_increases_with_drift                         PASSED
test_kl_clamped_nonnegative                          PASSED
test_kl_bin_edge_mismatch_rejected                   PASSED
test_kl_schema_version_mismatch_rejected             PASSED
test_trigger_fires_on_five_consecutive_days          PASSED
test_trigger_does_NOT_fire_on_four_days              PASSED
test_trigger_does_NOT_fire_on_mixed_history          PASSED
test_trigger_uses_tail_of_history                    PASSED
test_trigger_on_empty_history_is_false               PASSED
test_build_baseline_artifact_shape                   PASSED
test_build_baseline_with_heldout_chunks_is_positive  PASSED
test_build_baseline_rejects_tiny_window              PASSED
test_load_baseline_missing_file_raises               PASSED
test_load_baseline_missing_fields_raises             PASSED
test_load_baseline_roundtrip                         PASSED
test_monitor_facade_delegates                        PASSED
```

Raw log: `pytest_test_tone_drift.log`.

### ci_gates: CLEAN

```
CI DOCTRINE GATES: CLEAN
```

Raw log: `ci_gates_tone_drift.log`. Confirms no `...` / `pass` bodies (V19),
no sys.path violations (V17), no `os.getenv` outside bootstrap (V1), no
silent getattr/hasattr/setattr, no inspect/eval/exec/compile/__import__.

## Cross-checks

1. **Self-KL is ≈0 at N=5000 samples.** `test_kl_of_identical_distribution_is_zero` asserts `< 1e-4`; observed values typically ~1e-6. Confirms the maths + smoothing don't introduce systematic bias.
2. **Drifted distribution crosses threshold.** `test_kl_increases_with_drift` asserts `kl_drifted > 10 × kl_self` AND `kl_drifted > 2 × MIN_TRIGGER_ABS_KL`. Both satisfied with bullish-skew drift distribution.
3. **5-day drift triggers; 4-day does not.** Explicit positive and negative tests (`test_trigger_fires_on_five_consecutive_days`, `test_trigger_does_NOT_fire_on_four_days`). Guards against the off-by-one bug of triggering on 4 days.
4. **Tail-only evaluation.** `test_trigger_uses_tail_of_history` feeds 7 entries (2 low + 5 high) and asserts the trigger fires — confirms only the last 5 matter.
5. **Mixed history suppresses trigger.** `test_trigger_does_NOT_fire_on_mixed_history` feeds 3 high + 2 low and asserts False — confirms the threshold must be breached for each of the tail 5.
6. **Baseline round-trip preserves shape.** `test_load_baseline_roundtrip` builds a payload, writes to tmp JSON, reloads, asserts bin_edges + distribution + schema preserved bit-for-bit.
7. **Baseline rejection paths.** Schema version mismatch, bin_edges mismatch, missing fields, file not found → each raises a distinct exception with a remediation hint in the message. Verified in 4 separate tests.
8. **Module importability via subprocess.** Smoke-tested that `python3 -c "from governance.equities.tone_drift_monitor import *"` succeeds with no side effects.

## Gotchas

1. **Brief's baseline path ambiguity resolved.** The playbook §5 R6 escalation note pre-committed the baseline path to `Backend/scripts/output/tone_baseline.json`. The module uses `DEFAULT_BASELINE_PATH = REPO_ROOT / "Backend" / "scripts" / "output" / "tone_baseline.json"`. `tone_scorer.py` does **not** reference a baseline path at all (it's a pure scoring function); no conflict to surface.
2. **`baseline_kl=0` trap.** Without the `MIN_TRIGGER_ABS_KL` floor, any baseline built without held-out chunks would trigger on *any* rolling KL > 0, which is meaningless. The floor is intentional; any deployment must either supply held-out chunks (default: 6) OR accept the floor-based threshold.
3. **Zero-mass drift is real drift.** If OPEN_NEWS volume drops and the zero bin inflates, the KL will register that — correctly. A caller who interprets this as "no drift" is wrong; the distribution-of-scores changed.
4. **Smoothing bias on small rolling windows.** At rolling-window sample counts < 420, Laplace smoothing dilutes the distribution meaningfully (> 5% mass shift). `MIN_SAMPLES_FOR_KL = 30` in the script's report mode triggers an "insufficient samples" log line and skips KL computation for the day, rather than shipping a noisy value into history. The 30-day rolling window over `equities_events` typically yields thousands of titles, so this branch is a safety net, not a common path.
5. **Tone regex edits are detectable but lagging.** A silent revision to `tone_scorer._CATEGORY_DEFS` (e.g. adding new keywords) would change the score distribution and eventually trigger the monitor after 5 days. This is "working as designed" — the monitor catches drift from any source, including our own edits. If we want to suppress the alert for a known-safe revision, the operator rebuilds the baseline rather than disabling the monitor.
6. **Database I/O separated from monitor logic.** The monitor module has **zero DB dependencies** — pure functions + dataclasses + numpy. Only `equities_tone_drift_report.py` touches Postgres. This keeps the unit tests fast (0.13 s total) and keeps the monitor portable.

## Stress-test targets

1. **End-to-end report mode on live data.** Run `equities_tone_drift_report.py --build-baseline --window-start 2026-02-01 --window-end 2026-03-01` against prod, then `--report`. Manually inspect: does the rolling 30-day KL land reasonably close to the baseline KL on a normal week? If it produces a spurious trigger, the baseline window or heldout chunk count needs tuning.
2. **Keyword set revision regression.** After any deliberate edit to `_CATEGORY_DEFS` in `tone_scorer.py`, rebuild the baseline **before** the next `--report` run. Missing this step will produce 5 days of spurious alerts.
3. **Bin collapse under sparse data.** If `equities_events` stops writing for several days (upstream fetcher outage), the rolling window shrinks and the "insufficient samples" branch fires. Confirm this doesn't poison the history file — the correct behaviour is to skip today's entry entirely, not to append a zero KL.
4. **Very large event volumes.** At N >> 10k events/day, `score_title_tone` runs on every title inside the script. The compile-once regex design in `tone_scorer._compile_all()` should keep scoring cheap (~µs per title), but a monitor deployment needs to confirm the script completes within its scheduled window (typically ~1 min for a daily cron).
5. **Baseline drift over months.** Even without trigger, the `baseline_kl` slowly becomes stale. When a retrain fires, the new baseline should be built from a window that *includes the drift period*, not a fresh pre-drift window — otherwise the new baseline under-represents the distribution the scorer is actually producing. Operator must be aware of this when responding to an alert.
6. **Schema migration.** When `BASELINE_SCHEMA_VERSION` is bumped, loader rejects old artifacts. Deployment must ship a migration script or a "rebuild baseline first" operator procedure at the same time. Currently the schema is v1.0; no migration infrastructure needed yet.
