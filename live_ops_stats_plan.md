# Live Ops Stats — Implementation Plan
_Drafted 2026-03-18_

## Goal
Get all five strategies (S9, S10, U1, D1, D2) generating live signals and build a rolling
opportunity stats table + API endpoint to power the go-live UI.

---

## Current State

| Service | Status | Notes |
|---|---|---|
| `ingestor` | ✅ Healthy | 2/2 shards, 593 known, 390 monitored |
| `indicator_worker_0/1` | ✅ Running | Evaluating cleanly |
| `signal_worker_0/1` | ✅ Running | 1200–2100 evals/min — zero signals is market not triggering, not a broken pipeline |
| `unicorn_pipeline` | ✅ Running | 341 universe, 7 active embryos, cycling every 5m |
| `d2_paper_trader` | ✅ Running | Healthy, polling social_posts |
| `social_collector` | ❌ Crash-looping | `collector.py` missing — lost in repo move. File exists in old snapshots |

---

## Task 1 — Restore social_collector (fixes D2 data feed)

`social_collector/app/` was emptied by the repo move. Source of truth for recovery:
`/home/darren/Desktop/MCAS_ss-docs/MCAS/Backend/social_collector/app/`

Steps:
1. Compare snapshot vs prod to pick the most current version
2. Copy full `social_collector/app/` tree back into `Backend/social_collector/app/`
3. Commit → push → `git pull` on prod → rebuild + restart `social_collector`

```bash
cd /srv/mcas && git pull origin main
cd /srv/mcas/Backend && docker compose -f docker-compose.yml build social_collector \
  && docker compose -f docker-compose.yml up -d --no-deps social_collector
```

---

## Task 2 — DB migration: `live_opportunity_stats` view

Single DB view joining all three signal sources with 24h and 7d windows. Always fresh,
no background jobs needed.

File: `Backend/docs/db/migrations/YYYYMMDD_live_opportunity_stats_view.sql`

Columns:
- `asset_class` — `crypto` | `equities`
- `setup_id` — S9, S10, U1, D1, D2, EQ_*
- `window` — `24h` | `7d`
- `opp_count` — opportunities generated in window
- `closed_count` — subset that have exited
- `open_count` — still between entry and final exit
- `avg_pnl_pct` — closed only
- `total_pnl_usd` — closed only
- `avg_bars_held` — closed only
- `win_rate` — closed only
- `refreshed_at`

Data sources per asset class:
- **Crypto (S9/S10/U1/D1):** `opportunities` WHERE `batch_id IS NULL` (live) → join `simulation_results` for PnL (DISTINCT ON opportunity_id ORDER BY created_at DESC to get latest run)
- **D2:** `paper_positions` WHERE `setup_id = 'D2'` → `realized_pnl` directly
- **Equities:** `equities_opportunities` → join `equities_opportunity_outcomes` for `realized_return`

Run order: dev first (`mcas-dev-postgres-1`), verify, then production (`backend-postgres-1`).

---

## Task 3 — New API endpoint: `GET /v1/stats/opportunities`

Add to `api/app/main.py` or new `api/app/routers/stats.py`. Queries the view above,
cached 60s. Same `X-API-Key` auth gate.

Response shape:
```json
{
  "windows": {
    "24h": [
      {
        "setup_id": "D1",
        "asset_class": "crypto",
        "opp_count": 3,
        "closed_count": 1,
        "open_count": 2,
        "avg_pnl_pct": 2.1,
        "total_pnl_usd": 145.0,
        "avg_bars_held": 18,
        "win_rate": 1.0
      }
    ],
    "7d": []
  },
  "as_of_ts": "2026-03-18T13:05:00Z"
}
```

Deploy: rebuild + restart `api` container only.

---

## Task 4 — Open opportunity PnL (mark-to-market)

For opportunities between entry and final exit, two options:

- **Option A (ship first):** Return `pnl_usd = null`, `status = "open"` — UI shows "pending"
- **Option B (later):** Fetch current Binance price on the fly and compute
  `(current_price - entry_price) / entry_price`

**Implement Option A now.** Option B is a follow-on once the table is wired into the UI.

---

## Execution Order

1. Restore `social_collector` → deploy → verify crash-loop stops
2. Write migration SQL → run dev → verify → run prod
3. Write API endpoint → commit → push → deploy `api`
4. Smoke test: `curl -H "X-API-Key: ..." https://api.mcasui.com/v1/stats/opportunities`

**~4 files changed:** migration SQL, API router/main.py, restored social_collector tree.
