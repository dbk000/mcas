# R6 — Reproduction

## Run the unit tests

```bash
cd /home/darren/Desktop/MCAS/Backend
python3 -m pytest tests/governance/test_tone_drift.py -v
```

Expected: 17 passed, 0 failed. Runtime ~0.13 s. No DB required.

## Run ci_gates on all three new files

```bash
cd /home/darren/Desktop/MCAS/Backend
python3 -m governance.kernel.ci_gates \
    governance/equities/tone_drift_monitor.py \
    scripts/equities_tone_drift_report.py \
    tests/governance/test_tone_drift.py
```

Expected: `CI DOCTRINE GATES: CLEAN`.

## Build a production baseline (one-time)

```bash
export DATABASE_URL='postgresql://trading:mcas_local_dev_2026@172.18.0.11:5432/trading'
python3 Backend/scripts/equities_tone_drift_report.py \
    --build-baseline \
    --window-start 2026-02-01 \
    --window-end 2026-03-01
```

Writes `Backend/scripts/output/tone_baseline.json`. Log at `Backend/scripts/output/equities_tone_drift_report_<utc>.log`.

## Run the daily report

```bash
export DATABASE_URL='postgresql://trading:mcas_local_dev_2026@172.18.0.11:5432/trading'
python3 Backend/scripts/equities_tone_drift_report.py --report
```

Writes today's KL to `Backend/scripts/output/tone_drift_history.json` and, if the trigger fires, writes an alert row to `public.equities_pipeline_events`.
