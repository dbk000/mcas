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
- Commit: pending
- Outcome: Product preference captured to prevent future regressions back to responsive 1-column fallback.
- Notes / Evidence: User explicitly requested fixed 2-column behavior for cognition/training workflow.
