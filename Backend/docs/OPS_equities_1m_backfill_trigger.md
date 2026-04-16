# OPS — Equities 1m Backfill Trigger Criteria

**Source of truth.** `equities_plan_work/EQUITIES_PLAN_REVIEW_47.md` §2.4.
**Scope.** Equities pipeline only. Applies to per-strategy targeted 1m backfill on promoted universe slices.
**Rule.** All three criteria must pass. Failing any one → no 1m backfill.

---

## Three-of-three trigger

A strategy is eligible for targeted 1m backfill on its promoted universe slice iff **all** of:

1. **Hourly-bar sweep pass at some capital tier.** The strategy passes the SAFE sweep at some capital tier with PF lift ≥ 10%, 4/5 folds, DSR > 0.
2. **Per-trade edge tight enough to be timing-sensitive.** Per-trade edge after hourly modelled slippage (per review §1.4, max-of-three absolute floor) < 2× the fee floor for the active tier.
3. **Explicit intraday timing gate.** Entry or exit gate explicitly references intraday timing (opening range, VWAP deviation, gap-fade, closing auction).

Failing any one → **no 1m backfill.**
Passes all three → queue the 1m backfill for the strategy's candidate universe before final promotion.

---

## Failure modes (any one fails → stop)

- **Criterion 1 fails** (no tier passes hourly sweep): strategy has no demonstrated edge on hourly data; 1m backfill cannot rescue what hourly did not find. Drop.
- **Criterion 2 fails** (edge is comfortably above 2× fee floor on hourly): edge is robust at hourly granularity; 1m offers no incremental signal justifying the fetch cost. Promote on hourly as-is.
- **Criterion 3 fails** (no intraday-timing gate in entry/exit): strategy is not structurally sub-hourly; 1m data does not bear on its decision boundary. Promote on hourly as-is.

---

## Hard cap

**100 symbols × 2y per strategy.** No exceptions. Larger universes or longer horizons require a separate playbook item with an explicit scope-creep review.

---

## Operational notes

- The trigger is evaluated **once per strategy**, post-hourly-sweep, pre-final-promotion.
- The candidate universe is the strategy's promoted slice at its lowest viable tier (see `strategy_tier_viability_floor`, review §2.3).
- The 1m fetch cost is real (Polygon paid plan or equivalent); this gate is the scope-protection mechanism.
- Backfill writes go through the standard ingestor pipeline; no bespoke 1m-only path.

---

## Why this exists

"Flags need" in the 4.6 draft was a vibe, not a criterion. This trigger makes the 1m-backfill decision reversible on clean data. It also protects scope — strategies without an explicit sub-hourly gate don't trigger 1m fetch cost, even if they pass criteria 1 and 2.
