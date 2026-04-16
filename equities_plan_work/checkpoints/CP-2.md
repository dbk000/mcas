# CP-2 — Pre-Backfill Data-Health Read

Generated: 2026-04-16T22:35:45.602645+00:00
Script: `Backend/scripts/equities_data_health_read.py`
Log: `Backend/scripts/output/equities_data_health_read_20260416_222415.log`

## Verdicts

Q1 OHLCV continuity (70 sym): FAIL — 1 pairs failed: KLAC/1h: 2491/2635 (5.5% short)
Q2 feature-event coverage: PASS — 70 symbols all ≥ 100 feature-events
Q3 available_at_ts integrity: PASS — 0 rows with available_at_ts <= bar_start_ts
Q4 source-column consistency: PASS — equity sources exactly {equities_yahoo, yahoo_chart}; no rogue crypto/equity drift
Q5 adjustment regime: PASS — split-only — split-only (Yahoo indicators.quote path) — consistent across evaluated splits; dividend-adjustment will be handled by A7 corporate-actions migration

OVERALL VERDICT: FAIL

## Evidence Artifacts

- Q1: `equities_plan_work/findings/R1/q1_continuity_output_20260416_222415.csv`
- Q2: `equities_plan_work/findings/R1/q2_feature_coverage_20260416_222415.csv`
- Q3: `equities_plan_work/findings/R1/q3_leakage_check_20260416_222415.txt`
- Q4: `equities_plan_work/findings/R1/q4_source_counts_20260416_222415.csv`
- Q5: `equities_plan_work/findings/R1/q5_adjustment_regime_evidence_20260416_222415.txt`

## Escalation Notes

- Q1 FAIL: 1 pairs failed: KLAC/1h: 2491/2635 (5.5% short)
