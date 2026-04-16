# R4 — 1m backfill trigger criterion (ops doc)

**Item.** R4 (playbook §5).
**Owner.** W2 slot, batch execution 2026-04-16.
**Status.** PASS.

## Decisions made

- **Doc placement: `Backend/docs/OPS_equities_1m_backfill_trigger.md`.** Alongside existing ops/research docs (`STRATEGY_RESEARCH_PLAYBOOK.md`). `OPS_` prefix flags it as an operational runbook.
- **Three criteria written verbatim from review §2.4.** Numbers, operators, and scope phrases copied character-for-character: `PF lift ≥ 10%`, `4/5 folds`, `DSR > 0`, `< 2×`, and the specific examples of intraday gates (`opening range, VWAP deviation, gap-fade, closing auction`).
- **Added a failure-modes section that explicitly names what each criterion failure means operationally.** The review states "failing any one → no 1m backfill" as a rule; the ops doc spells out *why* for each, so the operator knows whether to drop the strategy, keep it on hourly, or re-scope it.
- **Hard cap reiterated at top level** (not buried).
- **Cross-reference to review §1.4** for the "hourly modelled slippage" phrase in criterion 2 (max-of-three absolute floor).
- **Cross-reference to review §2.3** for `strategy_tier_viability_floor` in the operational-notes section.
- **Length: 47 lines rendered.** Target <100; brief said "< 1 page".
- **No new code, no schema changes.** Documentation only.

## Data sampled

- Review §2.4 source text copied verbatim to `source_review_text.txt` (17 lines).
- Reviewed §1.4 (slippage floor) and §2.3 (strategy_tier_viability_floor) for cross-reference correctness.
- Confirmed no pre-existing 1m-backfill trigger doc anywhere under `Backend/docs/`.
- File line count: 47.
- `grep` validation: all three criteria present with their verbatim thresholds.

## Cross-checks

- **Verbatim test.** `grep -n 'PF lift.*10\|2.*fee floor\|intraday timing'` on the new file returns all three criteria, each with full verbatim phrasing intact.
- **Length test.** `wc -l` = 47 < 100.
- **Link target test.** Cross-reference to `EQUITIES_PLAN_REVIEW_47.md §2.4` points at an actual section at line 138.
- **No runtime impact.** Doc file only; no code imports, no config reads. Self-scan of `ci_gates` irrelevant.

## Gotchas

- **Criterion 2's "hourly modelled slippage" is defined in §1.4, not §2.4.** Cross-reference is load-bearing.
- **Criterion 1's "some capital tier" is load-bearing.** A strategy that passes only TIER_XL still satisfies it.
- **The 100 symbols × 2y cap is per strategy, not total.** If 10 strategies pass, 1000 symbol-2y slices.
- **"Before final promotion" means 1m backfill sits between sweep-pass and promotion-DB-write.** Not post-deployment.

## Stress-test targets

1. **Re-qualification.** If a strategy stops passing one criterion post-deployment, does 1m data stay or get purged? Not specified.
2. **Overlap between strategies.** Deduplication is a pipeline concern, not a trigger concern.
3. **Definition of "explicit" in criterion 3.** Borderline cases should escalate.
4. **Tier-dependent edge behaviour.** Criterion 2 can flip between tiers (fee floors differ 70/7/1.4/0.14 per tier).
5. **Backfill window drift.** yfinance hourly 730-day capped; cross-check against §2.5.

## Artifacts in this directory

- `findings.md` (this file)
- `source_review_text.txt` — verbatim review §2.4 for future cross-check
- `reproduction.md` — one-screen cold reproduction steps
