# MONDAY_LAUNCH_CARD_20260412

**Status:** DONE 2026-04-12
**Owner:** MONDAY-LAUNCH-CARD author (single agent, no sub-agents)
**Goal:** Standalone operator-facing xlsx covering only what's needed to launch/operate the Monday 2026-04-13 live cap=3 Binance autotrader (C2S, C2M, C4M, C4ML). Separate from the main build spec.

## Outputs

- xlsx: `/home/darren/Desktop/MCAS/MCAS_Monday_Launch_Card_20260413.xlsx` (31,102 bytes — well under 300KB limit)
- generator: `/home/darren/Desktop/MCAS/Backend/scripts/generate_monday_launch_card.py` (~470 LOC — runnable cold for future Monday launches)

## Sheet inventory (13, as specified)

| # | Sheet | Rows |
|---|---|---:|
| 1 | README | 48 |
| 2 | Strategies_Summary | 5 (1 hdr + 4 strategies) |
| 3 | Launch_C2S | 49 |
| 4 | Launch_C2M | 47 |
| 5 | Launch_C4M | 52 |
| 6 | Launch_C4ML | 42 |
| 7 | System_Parameters | 21 |
| 8 | Telegram_Commands | 8 |
| 9 | Monitoring_SQL | 9 |
| 10 | HIGH_BETA_Symbols | 56 (1 hdr + 55 symbols) |
| 11 | Cluster_Map_Operator | 11 (1 hdr + 10 multi-symbol clusters) |
| 12 | Shock_Event_Patterns_Historical | 21 (1 hdr + 20 events) |
| 13 | Changelog | 2 |

## Validation summary

- openpyxl load — OK
- Strategies_Summary has 4 rows — OK
- Every Launch_* sheet contains the 8 sections — OK
- Filter presence: C2S=8, C2M=1, C4M=7 indicator filters; C4ML indicator null (expected).
- Social filters: C2S=0 (null, expected), C2M=2, C4M=3, C4ML=1.
- HIGH_BETA count matches JSON — 55 / 55 OK
- Cluster_Map_Operator includes the 12-symbol FLOKI cluster at row 2 — OK
- File size 31,102 bytes ≤ 300KB — OK

## Notes

- Regenerate future launches via: `python3 Backend/scripts/generate_monday_launch_card.py --out <path> --launch-date <YYYY-MM-DD>`
- No commits made. No edits to the main build spec. Production DB not touched (read-only file work).
