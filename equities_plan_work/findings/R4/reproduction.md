# R4 — Cold reproduction

## To verify the doc exists and is correct

```bash
cd /home/darren/Desktop/MCAS
test -f Backend/docs/OPS_equities_1m_backfill_trigger.md && echo EXISTS
wc -l Backend/docs/OPS_equities_1m_backfill_trigger.md    # expect 47
grep -n 'PF lift.*10\|2.*fee floor\|intraday timing' Backend/docs/OPS_equities_1m_backfill_trigger.md
```

Expected: 3 grep hits covering all three criteria verbatim.

## To re-derive from scratch

1. Read `equities_plan_work/EQUITIES_PLAN_REVIEW_47.md` §2.4 (lines 138–152).
2. Copy the three bullet criteria verbatim into a new one-page doc.
3. Add failure modes, hard cap, cross-references to §1.4 and §2.3.
4. Keep under 100 lines.

## To roll back

```bash
rm /home/darren/Desktop/MCAS/Backend/docs/OPS_equities_1m_backfill_trigger.md
```
