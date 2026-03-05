# Bug Trial Log

Purpose: record attempted fixes, outcomes, and regressions so later sessions avoid repeating dead ends.

## Entry Format
- Date (UTC):
- Area:
- Change:
- Commit:
- Outcome:
- Notes / Evidence:

---

## 2026-03-04 (UTC) - Expired Opportunity Survey Chart Blank/Flicker

- Date (UTC): 2026-03-04
- Area: Survey UI (`OpportunitySurvey` / `OpportunityCurveChart`)
- Change: Added top panel collapse and stronger temporal markers.
- Commit: `cfe0300`
- Outcome: Kept. UX improvement only; did not address blank-chart root cause.
- Notes / Evidence: User confirmed controls collapse was useful.

- Date (UTC): 2026-03-04
- Area: Survey presentation layout
- Change: Presentation overflow/layout cleanup.
- Commit: `dd6eff1`
- Outcome: Kept. Fixed haywire panel overflow; did not fix candle disappearance.
- Notes / Evidence: Reduced layout breakage in presentation mode.

- Date (UTC): 2026-03-04
- Area: Chart styling/overlays
- Change: Classic style + MA + volume + multi-exit visual bars.
- Commit: `f3382c1`
- Outcome: Partial then regressed. Improved look, but blank-chart reports continued.
- Notes / Evidence: User screenshots still showed purple panes with no visible candles.

- Date (UTC): 2026-03-04
- Area: Volume pane robustness
- Change: Guarded histogram pane creation/update failures.
- Commit: `4564eb0`
- Outcome: Partial. Prevented hard failure when histogram panel errors; blank issue persisted.
- Notes / Evidence: Some rows rendered; others remained blank.

- Date (UTC): 2026-03-04
- Area: Price precision
- Change: Dynamic precision/minMove adjustments for tiny-price symbols.
- Commit: `97137ac`
- Outcome: Not sufficient. Did not eliminate flicker-to-blank behavior.
- Notes / Evidence: User still observed brief render then disappearance.

- Date (UTC): 2026-03-04
- Area: Time-scale zoom
- Change: Forced larger visible-range window (`minVisibleBars` path).
- Commit: `9126107`
- Outcome: Replaced. Helped over-zoom symptom but unstable with mixed symbol scales.
- Notes / Evidence: Later superseded by safer spacing approach.

- Date (UTC): 2026-03-04
- Area: Time-scale control
- Change: Replaced logical-range forcing with bar-spacing control.
- Commit: `c61f82d`
- Outcome: Partial. Reduced over-zoom for some rows; did not solve blank rows globally.
- Notes / Evidence: Some batches still showed only empty purple panes.

- Date (UTC): 2026-03-04
- Area: Time-scale heuristics
- Change: Added price-tier bar-spacing lookup table.
- Commit: `f59e0c6`
- Outcome: Not sufficient. Heuristic did not remove blank/flicker behavior.
- Notes / Evidence: User screenshots still reported no candles.

- Date (UTC): 2026-03-04
- Area: Price precision rollback
- Change: Removed dynamic precision mutation; fixed high precision format.
- Commit: `05546fb`
- Outcome: Not sufficient.
- Notes / Evidence: Blank panes persisted after deploy.

- Date (UTC): 2026-03-04
- Area: Time-scale safety
- Change: Clamped bar spacing before `fitContent`.
- Commit: `47c9a9a`
- Outcome: Not verified in production due failed Cloudflare deploy for that commit.
- Notes / Evidence: Follow-up commit superseded this path.

- Date (UTC): 2026-03-04
- Area: Presentation-mode simplification
- Change: Disabled heavy overlays in presentation mode; added close-line fallback.
- Commit: `492a89b`
- Outcome: Partial. Improved visibility in some sessions; intermittent blank panes still reported.
- Notes / Evidence: User screenshots show occasional full render, then return to blank panes in later loads.

## Current Working Hypothesis (as of 2026-03-04 UTC)
- Candles exist and API returns valid windows for sampled opportunities.
- Failure is likely frontend render lifecycle/state interaction (post-paint update path), not missing data fetch.
- Candidate trigger areas:
  - per-update timescale/spacing mutations after initial draw,
  - level/price-line side effects on autoscale for certain marker values,
  - rapid remount/update interactions under virtualized rows.

## 2026-03-04 (UTC) - Root Cause Audit + Stabilization Pass

- Date (UTC): 2026-03-04
- Area: Survey chart lifecycle
- Change: Full code-path audit identified chart remount churn as primary blank/flicker trigger.
- Commit: `1862ee4`
- Outcome: Confirmed root cause candidate.
- Notes / Evidence:
  - `OpportunityCurveChart` init effect depended on unstable values (`syncMarkerCoords`, `maPeriods`) and could `chart.remove()`/recreate during normal rerenders.
  - Data effect could miss repopulation after recreate in some dependency combinations.

- Date (UTC): 2026-03-04
- Area: Survey chart stabilization
- Change:
  - Stabilized marker sync via refs to prevent create-effect churn.
  - Removed unstable dependency path (`maPeriods`) from chart init effect.
  - Added safer width floor + fit-content on resize.
  - Filtered invalid datapoints before `setData`.
  - Ignored non-positive level prices for rendered SL/TP/entry/exit lines.
  - Added responsive presentation columns (fallback to 1 column when viewport too narrow).
  - Removed unnecessary `movingAveragePeriods` prop from survey row (MAs disabled there).
- Commit: `1862ee4`
- Outcome: Build passes locally (`Frontend npm run build`) and Cloudflare Pages deploy succeeded (`b93d15e` -> `https://85e8a1f7.mcas.pages.dev`).
- Notes / Evidence: This pass targets exact “brief render then blank” behavior reported in screenshots. User runtime validation still required.

## 2026-03-05 (UTC) - Presentation Mode Policy Decision

- Date (UTC): 2026-03-05
- Area: Survey layout behavior
- Change: Removed responsive fallback in presentation mode; presentation mode now enforces 2 columns at all viewport widths by design.
- Commit: `cf7a4f9`
- Outcome: Shipped and deployed (Cloudflare success: `https://f8b2d218.mcas.pages.dev`).
- Notes / Evidence: User explicitly requested fixed 2-column behavior for cognition/training workflow.

## 2026-03-05 (UTC) - Macro Context Scale (Survey)

- Date (UTC): 2026-03-05
- Area: Survey data windowing
- Change: Increased survey pre-entry fetch window from 1h (`pre_s=3600`) to 6h (`pre_s=21600`) to show materially more 5m candles in stack rows for macro pattern context.
- Commit: `22413ee`
- Outcome: `Frontend npm run build` passes. Runtime validation pending after deploy.
- Notes / Evidence: User requested broader trend visibility (higher candle count visible before entry/exit training moments).

## 2026-03-05 (UTC) - Shared Chart Presets Centralization

- Date (UTC): 2026-03-05
- Area: Frontend chart tuning architecture
- Change: Added shared chart preset module and migrated survey row, expired-opportunity modal, and market collector chart to consume centralized presets.
- Commit: `22413ee` (feature), `564cc71` (log sync)
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://5e82e1aa.mcas.pages.dev`).
- Notes / Evidence: Reduces duplicated chart-prop tuning logic and makes future adjustments one-source-of-truth.

## 2026-03-05 (UTC) - Survey UI Noise Reduction + Global Window Controls

- Date (UTC): 2026-03-05
- Area: Survey stack row UI and reveal controls
- Change:
  - Removed per-row Commit / Predict Winner / Predict Loser panel.
  - Added top-level global controls to set chart window to `Up To Entry`, `Up To Exit`, and `Exit + N hours`.
  - Kept one global reveal offset slider for fine adjustment across all charts.
- Commit: pending
- Outcome: `Frontend npm run build` passes. Runtime validation pending after deploy.
- Notes / Evidence: User requested cleaner visual focus with global chart-window controls and no right-side prediction noise.

## 2026-03-05 (UTC) - Stack View Border Cleanup

- Date (UTC): 2026-03-05
- Area: Survey stack container styling
- Change: Removed redundant inner scroll-container border in Stack View; keep only outer `Card` border.
- Commit: pending
- Outcome: `Frontend npm run build` passes. Runtime validation pending after deploy.
- Notes / Evidence: User flagged duplicate border lines around stack region.

## 2026-03-05 (UTC) - Stack Layout De-Containerized + Rich Event Info

- Date (UTC): 2026-03-05
- Area: Survey stack layout and left-side metadata panel
- Change:
  - Removed redundant `Stack View / N curves` header from stack card.
  - Removed inner stack-container scroll and virtualization wrapper; stack now flows to page length and uses normal page scroll.
  - Expanded per-chart left metadata into a compact trade-event box showing price + timestamp for entry/TP/stop/final exit and extra exit markers.
- Commit: pending
- Outcome: `Frontend npm run build` passes. Runtime validation pending after deploy.
- Notes / Evidence: User requested full-page stack flow and richer event-level text details.

## 2026-03-05 (UTC) - Event Table Compact Format + Direction Inline

- Date (UTC): 2026-03-05
- Area: Survey left metadata panel readability
- Change:
  - Converted event detail block to compact mini-table (`Event | TS | Price`) to prevent wrapping/noise.
  - Moved direction (`LONG/SHORT`) onto same row as symbol.
  - Filtered invalid placeholder event values (`0`, epoch `1970`) to render as missing (`—`) instead of misleading values.
- Commit: pending
- Outcome: `Frontend npm run build` passes. Runtime validation pending after deploy.
- Notes / Evidence: User requested mini-table format similar to reference screenshot and cleaner symbol row.

## 2026-03-05 (UTC) - Event Color Key + Label-Free Chart Lines

- Date (UTC): 2026-03-05
- Area: Survey chart readability (stack rows)
- Change:
  - Removed survey chart level axis labels and level legend chips (line labels off).
  - Introduced shared event color palette (`chartEventPalette`) for ENTRY/TP1/TP2/STOP/FINAL EXIT.
  - Applied the same palette to per-row event mini-table text so the table is the primary implicit legend.
- Commit: `49c9541`
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://0231e2fc.mcas.pages.dev`).
- Notes / Evidence: User requested removing on-chart line labels and color-coding tabular event text for faster pattern inference.

## 2026-03-05 (UTC) - Remove Redundant Final Exit Label + Table Headers

- Date (UTC): 2026-03-05
- Area: Survey left metadata panel noise reduction
- Change:
  - Removed standalone `Final Exit:` line above the event table (now fully represented inside table rows).
  - Removed `Event / TS / Price` header row as redundant for this single-user workflow.
- Commit: `b986b3f`
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://fba4cf01.mcas.pages.dev`).
- Notes / Evidence: User requested cleaner, implicit layout with less repetitive labeling.

## 2026-03-05 (UTC) - Inline Setup Token in Survey Header

- Date (UTC): 2026-03-05
- Area: Survey row left metadata header
- Change:
  - Removed `Setup:` prefix text.
  - Moved setup token inline to the top row between symbol and direction (`SYMBOL  S9  SHORT`).
- Commit: `a909102`
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://51d365ac.mcas.pages.dev`).
- Notes / Evidence: User requested tighter header semantics with setup as an inline token.

## 2026-03-05 (UTC) - Entry Candle Alignment Pass (Backend + Survey Reveal)

- Date (UTC): 2026-03-05
- Area: Price-window marker consistency and survey entry visibility
- Change:
  - `/v1/opportunities/{opp_id}/price-window` now prefers `simulation_results` timestamps/prices over payload values when present.
  - Price-window OHLCV query now filters `is_final = TRUE` to match simulator bar semantics.
  - Survey default/`Up To Entry` reveal offset moved from `0m` to `+5m` to include the entry candle in view.
- Commit: `8fee4a3`
- Outcome: `Frontend npm run build` passes; backend tests pass in container (`4 passed` on `test_opportunity_price_window.py`); Cloudflare Pages deploy success (`https://ac39af3b.mcas.pages.dev`).
- Notes / Evidence: User reported entry candle appearing missing/disconnected from event table.

## 2026-03-05 (UTC) - Canonical Final Exit Only (No Ambiguous EXIT Row)

- Date (UTC): 2026-03-05
- Area: Survey event semantics (`EXIT` vs `FINAL EXIT`)
- Change:
  - Normalized backend `exit_points` to enforce one canonical `FINAL EXIT`.
  - Kept only TP/STOP/PARTIAL intermediate exits; dropped ambiguous generic `EXIT/CLOSE` points when not canonical final.
  - Removed frontend fallback append of arbitrary extra exit rows beyond `ENTRY/TP1/TP2/STOP/FINAL EXIT`.
  - Added test coverage for stale payload `EXIT` conflicts to ensure only one final remains.
- Commit: `185ab56`
- Outcome: `Frontend npm run build` passes; backend tests pass in container (`4 passed` on `test_opportunity_price_window.py`); Cloudflare Pages deploy success (`https://37eaa66f.mcas.pages.dev`).
- Notes / Evidence: User reported conflicting `EXIT` and `FINAL EXIT` values in survey table.

## 2026-03-05 (UTC) - Entry/Final Timestamp Date + Table Spacing Tighten

- Date (UTC): 2026-03-05
- Area: Survey event mini-table legibility
- Change:
  - Entry and Final Exit timestamp now render `dd/mm/yy hh:mm` (UTC).
  - Tightened event/ts/price cell spacing and right-aligned price to reclaim unused horizontal space.
  - Kept TP/STOP timestamp format compact (`hh:mm`) for density.
- Commit: `184844b`
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://d340ff12.mcas.pages.dev`).
- Notes / Evidence: User requested date visibility on Entry/Final rows and tighter information packing.

## 2026-03-05 (UTC) - Per-Row Exit Reveal Controls

- Date (UTC): 2026-03-05
- Area: Survey reveal controls (`Up To Exit`, `Apply Exit + N`)
- Change:
  - Switched `Up To Exit` from batch-max offset behavior to per-row exit offset.
  - Switched `Apply Exit + N` to per-row `(exit + N hours)` behavior.
  - Added control-state label text to indicate offset mode vs per-row preset modes.
- Commit: `9451aa1`
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://0c18115c.mcas.pages.dev`).
- Notes / Evidence: User reported `Up To Exit`/`Exit + N` showing same chart extent and exit alignment drift.

## 2026-03-05 (UTC) - Remove Chart Corner Branding Mark

- Date (UTC): 2026-03-05
- Area: Chart rendering polish
- Change: Disabled chart corner attribution logo in `OpportunityCurveChart` layout options.
- Commit: `8da3acd`
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://fa32ed21.mcas.pages.dev`).
- Notes / Evidence: User requested removal of persistent bottom-left logo from every chart tile.

## 2026-03-05 (UTC) - Survey Filters (Strategy/Direction/Date) + Remove Hide Levels Toggle

- Date (UTC): 2026-03-05
- Area: Survey top control bar and batch filtering
- Change:
  - Added `Strategy` dropdown filter (derived from loaded batch `setup_id`).
  - Added `Direction` dropdown filter (`Long` / `Short` / `Unknown`).
  - Added `From` and `To` date filters (UTC day boundaries, based on entry timestamp with created/updated fallback).
  - Removed `Hide SL/TP Until Reveal` toggle and removed hide-level prop wiring from survey row charts.
  - Pagination now operates on the filtered set instead of raw candidates.
- Commit: `22413ee` (feature), `564cc71`/`b610d70` (log sync)
- Outcome: `Frontend npm run build` passes; Cloudflare Pages deploy success (`https://5e82e1aa.mcas.pages.dev`).
- Notes / Evidence: User requested strategy + direction + date filters and explicit removal of hide-level toggle.

## 2026-03-05 (UTC) - Pipeline Stop-Loss Node as Simulation Source-of-Truth

- Date (UTC): 2026-03-05
- Area: Pipeline `Stop-Loss Config` params UI + simulation config resolution path
- Change:
  - Added dedicated per-strategy SL table to Pipeline params tab for `Stop-Loss Config` node (`stop_distance_factors` map).
  - Auto-seeds missing strategy rows from `/v1/strategies` (crypto strategies) using `sl_r` baseline.
  - Simulation run creation now omits stop-loss overrides unless explicitly supplied, so backend can resolve defaults from Pipeline.
  - Run executor now loads deployed `Stop-Loss Config` parameters from `meta.node_version` and uses:
    - `sl_r` as global default stop multiplier
    - `stop_distance_factors` as per-strategy overrides
  - Added safety rule in simulator stop-factor resolution: when per-strategy map exists, missing setup keys resolve to `1.0` (neutral) instead of inheriting global multiplier.
  - Added targeted test coverage for stop-factor resolution isolation (`test_stop_loss_policy_resolution.py`).
- Outcome: `python3 -m py_compile` passes for changed backend files; `Frontend npm run build` passes.
- Notes / Evidence: User required Pipeline + Backend to share first-source config, with per-strategy stop values editable in the existing Stop-Loss panel.

## 2026-03-05 (UTC) - Intrabar Conflict Policies + Pipeline-Configurable Alternatives

- Date (UTC): 2026-03-05
- Area: Exit semantics determinism for same-bar stop/TP conflicts (dual-stop readiness)
- Change:
  - Added explicit intrabar policy support in exit config: `STOP_FIRST` (default), `TP_FIRST`, `OHLC_HEURISTIC`.
  - Preserved conservative default behavior (`STOP_FIRST`) and one-event-per-bar semantics.
  - Wired execution spec/config fields (`intrabar_policy`, `one_event_per_bar`) so runs snapshot this assumption.
  - Added simulation request support for intrabar policy overrides and validation.
  - Extended Stop-Loss Pipeline params UI to expose intrabar policy + one-event-per-bar toggles as configurable defaults.
  - Added targeted backend tests for deterministic conflict outcomes by policy.
- Outcome: targeted pytest + frontend build run after patching (see latest turn for exact commands/results).
- Notes / Evidence: User requested pre-emptive handling of the known dual-stop same-bar ambiguity bug class.

## 2026-03-05 (UTC) - Startup Stop Registry Gate + Append-Only Stop Policy Audit Ledger

- Date (UTC): 2026-03-05
- Area: Production guardrails (drift prevention + field-level change auditability)
- Change:
  - Added canonical backend stop registry module with schema versioning + validation (`Backend/stop_policy_registry.py`).
  - Added hard startup validation in API and worker entrypoints; process fails fast on registry/default drift.
  - Added API template endpoint: `GET /v1/pipeline/stop_policy_template` sourced from backend registry.
  - Added append-only DB audit ledger for stop-policy changes (`meta.stop_policy_audit_events`), with migration and auto-ensure in `meta_api`.
  - Added field-level diff emission on Stop-Loss node version writes, including actor/source/reason and old/new values.
  - Added read endpoint for audit stream: `GET /v1/meta/stop_policy/audit`.
  - Bound simulation runs to persisted stop-policy snapshots (including `node_version_id`) for deterministic replay.
- Outcome: `python3 -m py_compile` passes; targeted pytest suite passes (`10 passed`); frontend build passes.
- Notes / Evidence: closes two explicit “not fully safe yet” gaps identified in review.

## 2026-03-05 (UTC) - Hotfix: Startup Gate Import Path Crash in Docker

- Date (UTC): 2026-03-05
- Area: API/worker startup (`main.py`, `worker.py`, `signal_worker.py`)
- Change:
  - Fixed crash-loop caused by `Path(__file__).resolve().parents[2]` in container paths (`/app/*.py` only has one parent depth before `/`).
  - Added `stop_policy_registry.py` into both runtime app trees:
    - `Backend/api/app/stop_policy_registry.py`
    - `Backend/worker/app/stop_policy_registry.py`
  - Removed brittle `sys.path` parent-hack imports and switched to local module import in each service runtime.
- Outcome: targeted stop-policy pytest suite passes locally (`11 passed`).
- Notes / Evidence: `/srv/mcas` deploy initially put `api`, `worker`, and `signal_worker_*` into restart loops with `IndexError: 2`; this hotfix restores startup compatibility with current Docker contexts.
