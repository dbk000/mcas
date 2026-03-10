# Glass Box Deployment — Status & Issues

**Date:** 2026-03-10
**Session:** Post-S9 honesty fixes deployment + 3-strategy simulation

---

## What Was Done

### Fixes Applied (commits on main)
1. **Pre-settled PnL computation** (`dc6322d`) — exit_settler opps were returning `$0 PnL` because the passthrough only looked for `pnl_after_costs_pct` (D2 path). S9/D1/U1 exit_settler payloads have `entry_price`+`exit_price` but no pre-computed PnL%. Fix: compute PnL from entry/exit prices with directional slippage + round-trip fees.

2. **Numeric string timestamp parsing** (`dc6322d`) — `bar_start_ts` stored as string `"1773010800000"` in JSON payload was parsed by Python's `datetime.fromisoformat()` as compact date YYYYMMDD → year 1773. 24% of S9 kline_lineage entries had garbage timestamps. Fix: detect numeric strings before ISO parsing.

3. **Pre-settled OHLCV skip** (`33e1adf`, `a8ae594`) — When ALL opps have `exit_settler`, skip expensive OHLCV backfill+load. Prevents OOM for large universes (850 symbols × 1m bars).

### Simulation Results

| Strategy | Run ID | Status | Results | Notes |
|----------|--------|--------|---------|-------|
| **S9** | `0bb05811` | SUCCESS | 940 trades | WR 38.32%, PF 0.4464, PnL -$13,957 |
| **U1** | `f1552772` | SUCCESS | 5 trades | Small sample (only 4 expired opps) |
| **D1** | `a910a148` | STUCK/RUNNING | 0 results | OOMed during OHLCV load — see below |

### S9 Before vs After Honesty Fixes
- **Before:** 927 trades, 356 breakeven ($0 PnL), WR 32.13%, PF 0.2315
- **After:** 940 trades, 0 breakeven, WR 38.32%, PF 0.4464
- The $0 PnL bug inflated the loss count. True WR is higher but PF still < 1.0.

---

## Outstanding Issue: D1 Simulation OOM

### Symptom
D1 simulation loads 1535 opps (all pre-settled with `exit_settler`). The `all_pre_settled` check should skip OHLCV loading, but returns `False` at runtime. This causes the worker to attempt loading 850 symbols × 1m bars → OOM → worker restarts → run stuck at RUNNING.

### What's Confirmed
- All 2136 D1 expired opps have `exit_settler = "expired_opportunity_exit_settler_v1"` in `payload_json`
- The `_has_exit_settler()` function works correctly when tested in isolation inside the container (returns True for all 2136 opps, and all 1535 after dedup)
- The deployed code (md5 checksums) matches source exactly across dev, production, and container
- The connection pool (`db_pool.py`) uses standard psycopg3 with no custom type adapters
- `payload_json` column is `text` type in Postgres, returned as Python `str` by psycopg3

### What's NOT Explained
- Why `all(_has_exit_settler(opp) for opp in opportunities)` returns `False` during actual worker execution, when the identical logic on the identical data returns `True` in a test script inside the same container
- The debug log from the first attempt (`ac269baf`) showed `[(91270, 'D1', False)]` — the same opp that returns True when tested directly

### Theories
1. **Connection pool vs direct connection:** The pool might return `payload_json` as a different type (e.g., `memoryview` or `bytes`) in some edge case. Testing used `psycopg.connect()` directly.
2. **Psycopg3 text adaptation:** Under load or with specific connection state, the `text` column might be returned differently.
3. **Race condition:** Some async interaction between dedup and the pre-settled check.

### Current Fix Attempt
- Added explicit count-based check with diagnostic logging (settler_count/total)
- Added `MemoryError` catch on OHLCV load as fallback — pre-settled opps will work with empty cache
- Need to rebuild worker + re-run

---

## Pending Deliverables

### File 1: PF Comparison (pre vs post honesty fixes)
- S9 data ready
- D1 needs successful run
- U1 complete (5 trades — small sample)
- Target: `post_s9_honesty/file1_pf_comparison.json`

### File 2: Champ/Challenger Output
- Run `s9_granularity_1m_vs_1s.py` script
- Target: `post_s9_honesty/file2_champ_challenger.json`

### File 3: Sample Kline Lineage ✓ DONE
- Written to `post_s9_honesty/file3_sample_kline_lineage.json`
- 3 samples from S9 run `0bb05811` with fixed timestamps + causality proof

---

## Other Completed Work (This Session)

### Relay System Bug Fixes (Agent)
- BUG 1: Auto-IDLE deadlock — cycle not incremented in `_timeout()`
- BUG 2: Bash file lock — `flock` wrapper in `mcas-loop.sh`
- BUG 3: asyncio anti-pattern — `create_task` → `await`
- BUG 4: Regex fragility — two-stage quote extraction
- Permissions fix: env file path → `/home/mcas-agent/`

### Remote Decisions Implementation Plan (Agent)
- Written to `REMOTE_DECISIONS_IMPLEMENTATION.md`

---

## Run Commands (for manual retry)

```bash
# Mark stuck D1 run as FAILED
docker exec backend-postgres-1 psql -U trading -d trading -c \
  "UPDATE runs SET status = 'FAILED' WHERE id = 'a910a148-f1ab-4702-bb25-a5c6161788c9'"

# Rebuild worker after code changes
cd /srv/mcas && git pull origin main
cd /srv/mcas/Backend && docker compose -f docker-compose.yml build worker && \
  docker compose -f docker-compose.yml up -d --no-deps worker

# Trigger D1 simulation
API_KEY=$(grep -m1 '^API_KEY=' /srv/mcas/Backend/.env | cut -d= -f2)
curl -s -X POST http://localhost:8002/v1/control/runs/simulation \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"name": "D1_v3_presettled", "setup_ids": ["D1"], "limit": 5000}'
```
