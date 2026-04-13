# A2-D2 BLOCKED — awaiting social backfill decisions

**Blocking auditor:** A4
**Audit artefact:** `/home/darren/Desktop/MCAS/Backend/scripts/output/social_coverage_audit_20260412_180000.md`
**Backfill ledger:** `/home/darren/Desktop/MCAS/.claude/taskboard/pending/A4_social_backfill_needed.md`

---

## Verdict

**A2-D2 regression is blocked until backfill decisions are made.**

## Affected opps

**46,983 of 46,983 (100.0%)** — every C-series opp in `gated_v1_20260411` has at least one social channel missing (GAP) within its 24h pre-trigger lookback window.

Broken down by which channel's absence is confounding:

| Channel | Opps with zero 24h data (structural, not empirical zero) | % of batch |
|---|---|---|
| NEWS | 26,164 | 55.7% |
| REDDIT | 26,164 | 55.7% |
| COMMUNITY (discord + live telegram) | 30,355 | 64.6% |
| WHALE | 9,536 | 20.3% |

## Affected categories (A2 spec uses these)

- **GEOPOLITICAL, MONETARY, REGULATORY, STRUCTURAL** (macro keyword clusters): heavily biased during 2026-02-20 → 2026-03-07 dead zone. Regression will pick up ingestion-schedule signal, not social-predictive signal.
- **WHALE** (governance/social_features.py category): 17-day structural gap hits 20% of opps. Partial — fixable via telegram rescrape.
- **NEWS, COMMUNITY, REDDIT** (governance/social_features.py content categories): each absent for >55% of batch. Without backfill, any coefficient A2 estimates for these categories is a 2026-02-19-or-2026-03-07 step function in disguise, not a causal social effect.

## Minimum unblock condition

Either:

1. **Re-ingest path (preferred):** complete scripts #1, #2, #3, #4 from `A4_social_backfill_needed.md`. Expected wall time ~4-7h. After: NEWS/WHALE span full window, REDDIT has partial-but-meaningful recovery, DISCORD gaps narrow to structural Epoch 1 only. A2-D2 runs on NEWS/WHALE for full batch; COMMUNITY scoped to 2026-03-08+.

2. **Narrow-scope path (fallback):** restrict A2-D2 to opps with `created_ts >= 2026-03-08` (16,628 opps = C4ML/C2S subset). All Epoch 2 sources active. No re-ingest required but sample size falls to ~35% of batch and DSR corrections tighten. Write-up must state "social regression cannot be validated on Epoch 1 data."

## Recommendation

Path #1. The backfill wall is small (~4-7h parallel) relative to the sprint timeline (D2 = 2026-04-14). Scripts should be written today; backfill runs overnight; A2-D2 regression starts D2 morning with clean data.

## What A2 should do RIGHT NOW

- Do NOT start the per-strategy regression over the 46,983 opps.
- DO extract `Backend/governance/regime/keyword_clusters.py` (A2's day-1 handoff task to A1) — not blocked by social coverage.
- DO write the A2-D2 harness (walk-forward + DSR scaffold) against placeholder feature tensors — not blocked.
- WAIT for backfill completion signal on `.claude/taskboard/pending/A4_social_backfill_needed.md` before invoking feature extraction over social data.

Posted 2026-04-12 by A4.
