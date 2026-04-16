# R1 — Reproduction

Single command from repo root (`/home/darren/Desktop/MCAS`):

```bash
export DATABASE_URL='postgresql://trading:mcas_local_dev_2026@172.18.0.11:5432/trading'
python3 Backend/scripts/equities_data_health_read.py
```

Pre-flight (skip concurrent long-running ohlcv scans):

```bash
docker exec backend-postgres-1 psql -U trading -d trading -c \
  "SELECT pid, now()-query_start AS dur FROM pg_stat_activity WHERE state='active' AND pid<>pg_backend_pid();"
```

Runtime: ~5-8 minutes dominated by Q4's two full-table group-by scans on the 293M-row public.ohlcv.

Outputs:
- `equities_plan_work/checkpoints/CP-2.md` — verdict
- `Backend/scripts/output/equities_data_health_read_<utc>.log` — audit log
- `equities_plan_work/findings/R1/q*_<utc>.{csv,txt}` — raw evidence per question
