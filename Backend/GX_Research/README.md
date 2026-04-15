# GX_Research — Momentum / Trend-Following Research Sandbox

Self-contained research sandbox. Does **not** import from `Backend/Playbook/`,
`Backend/core_logic/`, `Backend/governance/`, `Backend/indicators/`, or
`Backend/scripts/`. All compute in `lib/`; all sweeps in `scripts/`.

**Plan:** `../../GX_RESEARCH_PLAN.md` (v4).

## Universe

288 USDC pairs from `Backend/config/binance_spot_whitelist_usdc.txt`, frozen into
`universe.txt` at sandbox init. Because production OHLCV for USDC pairs only
goes back ~2 days, `lib/db.py::logical_to_data_symbol()` translates USDC symbols
to their USDT counterparts for the data fetch (user decision 2026-04-14).
Transportability from USDT back to USDC is checked explicitly at Stage F tail.

## Layout

```
GX_Research/
├── universe.txt                   288-symbol USDC universe (frozen)
├── GX_Research_Tracker.xlsx       Human audit trail (see §12a of plan)
├── evaluate_gx.sh                 6-stage wrapper
├── migrations/
│   └── 0001_gx_tables.sql         gx_opportunities, gx_sweep_cells, gx_sim_results (prod DB)
├── lib/                           indicators, exits, stats, slippage, regimes, db, storage, tracker
├── triggers/gx_trend_triggers.py  GX1-GX5 families + parameter grids
├── scripts/                       01..06 stage sweeps + report.py + init_tracker.py
└── output/
    ├── ohlcv_cache/               per-(symbol,timeframe,window).parquet
    ├── sweep_cells/               per-stage parquets of every cell evaluated
    ├── trades/                    per-cell parquet of simulated trades
    └── logs/                      per-stage stdout logs
```

## Run

**Unattended full pipeline (default):**
```bash
cd Backend/GX_Research
./evaluate_gx.sh --auto
```

**Interactive, stage-by-stage:**
```bash
./evaluate_gx.sh --interactive
```

**Resume from an intermediate stage (requires prior stages to have written their parquets):**
```bash
GX_BATCH_ID=gx_20260414T170000Z ./evaluate_gx.sh --from D
```

**Production DB access (per root CLAUDE.md §1.5):**
```bash
docker compose -f docker-compose.yml run --rm -T worker \
    /app/GX_Research/evaluate_gx.sh --auto
```

## Knobs (env vars)

| Var | Default | What it does |
|---|---|---|
| `GX_BATCH_ID` | `gx_<utc>` | Shared batch tag across all stage outputs |
| `GX_RESEARCH_START` | `2025-01-01T00:00:00Z` | Research window start |
| `GX_RESEARCH_END` | `2026-04-14T00:00:00Z` | Research window end |
| `GX_HOLDOUT_FRAC` | `0.15` | Fraction of window reserved as holdout |
| `GX_STAGE_B_TOP_N_PER_TF_DIR` | `15` | Per-(TF, direction) cap on Stage-A parents passed into Stage B. Replaces the old global `GX_STAGE_B_TOP_N`, which collapsed Stage B to whichever TF had the highest PF variance (usually 1d). |
| `GX_TIMEFRAMES` | `5m,15m,1h,4h,1d` | TF list for Stage-A sweep. Override only for smoke tests. |
| `GX_STAGE_B_EXITS` | `E1,E2,E3,E4,E5,E6` | Stage-B exit configs to sweep |

## Gates

| Stage | Sweep | Gate |
|---|---|---|
| A | 1,780 trigger-geometry cells at baseline exit | PF ≥ 0.5 AND n ≥ 500 |
| B | Top-N A survivors × stop grid × exit grid | PF ≥ 0.7 AND n ≥ 2000 |
| C | B survivors by UTC weekday × UTC hour | ≥1 bucket PF > 1, n > 100 |
| D | C survivors × filter grid, 4-fold WF, 1d embargo | Filter 3/4 folds + DSR > 0 |
| E | D survivors × cross-TF reference conditions | cross_tf_PF > baseline_PF AND n > 2000 AND DSR > 0 |
| F | E survivors on reserved 15% holdout, slip 1×/1.5×/2× | SHORTLIST gate (plan §11.1) |

Every cell — winner or loser — is persisted to `output/sweep_cells/`.

## Honest-reporting commitment

If no cell clears Stage F, the deliverable is the null result stated in plan §15
plus the full sweep-cell parquets as evidence.
