# Backtest Punchlist

Open issues to close out before backtesting can be considered production-ready.

Items are grouped by severity. Each item references the file(s) involved.
Items tagged **[CANON]** are violations of the MCAS Canon (CLAUDE.md root guidance).

**Status key:** DONE = resolved, OPEN = not yet started, DEFERRED = intentionally postponed

---

## PC — Canon Violations (architectural invariants broken)

### C1. OHLCV table missing `as_of_ts` and `is_final` columns [CANON 4A, 8] — DONE
`mcas_schema.sql`, `models.py:RUNS_SCHEMA`

Added `as_of_ts TIMESTAMPTZ` and `is_final BOOLEAN DEFAULT TRUE` to the `ohlcv` table. Backtest loader filters to `is_final = TRUE` only.

### C2. Backtest results endpoints missing `as_of_ts` / `candle_ts` [CANON 5A] — DONE
`runs_api.py`, `run_executor.py`

Added `signal_bar_ts` to `RunTrade`. Response envelope already carries timestamp.

### C3. Signal timing metadata not exposed [CANON 5E] — DONE
`run_executor.py`, `models.py:RunTrade`

Added `since_ts` and `action_by_ts` fields to `RunTrade`. Backtest populates them from setup time-stop parameters.

### C4. Indicator library has no `indicator_id` or version governance [CANON 6] — DONE
`indicators.py`

Added `INDICATOR_LIBRARY_VERSION`, indicator registry with IDs/params/versions, and `get_indicator_library_fingerprint()`. Included in run fingerprint computation.

### C5. Explainability payload not formalized [CANON 7A] — DONE
`run_executor.py`, `setup_base.py`

Added `_build_why_items()` function that produces 3-8 explainability items. Validated in `_signal_to_trade()`. S1/S2 payloads generate meaningful why items.

### C6. Duplicate DDL across two schema files [CANON 10] — DONE
`mcas_schema.sql` vs `models.py:RUNS_SCHEMA`

Removed inline DDL from `models.py`. `RUNS_SCHEMA` now only creates the pgcrypto extension. All table DDL governed solely by `mcas_schema.sql`.

### C7. Backtest trades not reproducible from stored snapshots [CANON 2C] — DONE
`run_executor.py`, `DataSpec`

`_compute_ohlcv_checksum()` computes SHA256 over all OHLCV data before execution. Stored in `data_json`. `data_version` set per backtest.

### C8. Setup modules missing concurrency rules and liquidity tiers [CANON 7A] — DONE
`setups/s1_bb_squeeze.py`, `setups/s2_golden_cross.py`

Added `max_concurrent`, `liquidity_tiers`, and `sizing_caps` to `SetupBase`. S1 and S2 declare their values.

---

## P0 — Blockers (backtests produce no trades today) — ALL DONE

### 1. Signal generation is stubbed out in the backtest loop — DONE
`run_executor.py`

Bar-by-bar loop now accumulates candle history, computes indicators via `FeatureFactory` after 201-bar warmup, and runs S1/S2 evaluators to generate signals.

### 2. No NEXT_OPEN pending-entry queue — DONE
`run_executor.py`

`pending_entries` list queues signals from bar N. At bar N+1, entries fill at open price with slippage, sorted by score (best first).

### 3. Position sizing is hardcoded to 1.0 — DONE
`run_executor.py`

Position size = `risk_amount / r_value` where `risk_amount = capital * max_risk_per_trade_pct / 100`. Notional capped at `max_exposure_pct` of capital.

### 4. No virtual account / equity tracking during backtest — DONE
`run_executor.py`, `models.py:RunSummary`

Virtual account with `starting_capital`, bar-by-bar equity tracking, peak/drawdown, daily PnL for circuit breaker, equity curve stored in `RunSummary`.

---

## P1 — Correctness (results would be misleading) — ALL DONE

### 5. OHLCV bar lookup is O(N) per symbol per bar — DONE
`run_executor.py`

Pre-indexed into `bar_index[key] = {bar["open_time"]: bar}` dict for O(1) lookup.

### 6. open_time type mismatch [CANON 4A, 10] — DONE
Resolved alongside C1/C6. Schema standardized on `TIMESTAMPTZ`.

### 7. Risk limits defined but never enforced — DONE
`run_executor.py`

Daily loss circuit breaker checked before opening positions. Risk per trade enforced during position sizing.

### 8. Slippage column never populated in trade records — DONE
`run_executor.py`

Total slippage (entry + exit) computed in `_position_to_trade()` and stored in `RunTrade.slippage`.

### 9. R-multiple calculation uses gross PnL, not net — DONE
`run_executor.py`

Changed to `pos["pnl_net"] / (r_value * size)` — reflects actual returns after fees.

### 10. Stop gap-fill logic is inverted for longs — DONE
`run_executor.py`

Fixed to `bar["open"] if bar["open"] < stop else stop` for longs (gap-down fills at open). Mirrored for shorts.

### 11. End-of-backtest force-close uses wrong price — DONE
`run_executor.py`

Force-close now uses `last_bar["close"]` (the close price of the last bar for that symbol).

---

## P2 — Missing analytics — ALL DONE

### 12. No equity curve data stored — DONE
`run_executor.py`, `models.py:RunSummary`

Bar-by-bar `{ts, equity, drawdown_pct}` stored in `RunSummary.equity_curve`. Trimmed to 500 points for JSON size.

### 13. Summary missing key risk metrics — DONE
`run_executor.py`, `models.py:RunSummary`

Added: Sharpe ratio, Sortino ratio, Calmar ratio, max consecutive wins/losses, average holding period (bars), average win R, average loss R, payoff ratio, return %.

### 14. No per-setup or per-symbol breakdown in summary — DONE
`run_executor.py`, `models.py:RunSummary`

Added `per_setup` and `per_symbol` dicts: each entry has trades, wins, win_rate, pnl_net, total_r, avg_r. Per-symbol trimmed to top 20 by absolute PnL.

### 15. RunDetail frontend shows summary as raw JSON — DONE
`RunDetail.jsx`

Backtest OverviewTab now shows 6 rows of formatted stat cards (PnL, return, win rate, profit factor, drawdown, R-multiples, Sharpe/Sortino/Calmar, capital, costs). Per-setup and per-symbol tables with sortable columns. Non-backtest runs still show raw JSON.

---

## P3 — Data integrity

### 16. No OHLCV gap detection or handling — DONE
`run_executor.py:_load_backtest_data()`

Detects gaps by comparing consecutive timestamps against expected timeframe interval. Applies `gaps_policy` from `DataSpec`: `SKIP_ASSET` excludes symbol entirely, `FORWARD_FILL`/`DROP_BARS` log warnings and include. Max gap tracked and logged.

### 17. No data checksum verification before backtest — DONE
`run_executor.py`, `DataSpec`

Resolved with C7. Checksum computed and stored before execution begins.

### 18. No minimum bar count validation per symbol — DONE
`run_executor.py:_load_backtest_data()`

Symbols with fewer than `min_bars_required` (201) bars are skipped. Count logged and stored in `run.summary.assets_skipped`.

### 19. OHLCV data not auto-ingested before backtest — DONE
`control_api.py`

Added `POST /v1/control/data/ingest` endpoint: accepts symbols, timeframes, date range; fetches from Binance REST API with rate limiting; upserts into `ohlcv` table.

Also added `GET /v1/control/data/availability` for pre-flight data range checks per symbol/timeframe.

---

## P4 — Execution model gaps

### 20. Fill model from ExecutionSpec not used in exit checks — DONE
`run_executor.py:_check_position_exit()`

Now reads `exec_config.fills.stop_fill` and `exec_config.fills.target_fill`. THROUGH mode requires price to go past the level (strict inequality), TOUCH uses `<=`/`>=`.

### 21. Trailing stop not implemented — DONE
`run_executor.py:_check_position_exit()`

Tracks `high_water_mark` on each position. Activates after 1R profit — trails 1R below HWM for longs, 1R above HWM for shorts. Gap-fill logic applied to trail stop fills.

### 22. FAIL_FAST exit not implemented — DONE
`run_executor.py:_check_position_exit()`

Checks within first 2 bars if close is on wrong side of entry by >0.5%. Exits at close price with FAIL_FAST reason.

### 23. One-per-asset constraint not enforced — DONE
`run_executor.py`

Resolved in P0. Both signal generation and pending-entry fill check `one_per_asset` flag against open positions.

### 24. TP2 handling — no partial close at TP1 — DONE
`run_executor.py` (exit handling in bar loop)

When TP1 is hit and TP2 exists: closes 50% of position at TP1, creates remainder position with stop moved to breakeven (entry price), targeting TP2. Remainder flagged with `_is_remainder` to prevent double-split.

---

## P5 — Frontend / UX

### 25. Backtest form end-date defaults to 2024-12-31 — DONE
`ControlPlane.jsx`

End date now defaults to yesterday's date via `new Date()` with day decremented and formatted as ISO string.

### 26. No symbol autocomplete or validation in backtest form — DONE
`ControlPlane.jsx`

Added `SymbolMultiSelect` component with typeahead search against the markets endpoint. Chips for selected symbols, dropdown with checkmarks, filters as you type. Replaces free-text input. Also fixed pre-existing Tabs `id`/`value` mismatch and Toggle `checked`/`enabled` mismatch.

### 27. No data availability indicator before submitting backtest — DONE
`control_api.py`, `ControlPlane.jsx`, `useData.js`

Added `useDataAvailability` hook and `DataAvailabilityInfo` component. After selecting symbols, timeframes, and date range, shows green confirmation or amber warnings for missing/partial data coverage. Backend endpoint was already in place.

### 28. No backtest comparison view — DONE
`RunCompare.jsx` (new), `App.jsx`, `Runs.jsx`

New `/runs/compare` route with `RunCompare` page. Two run selectors filter to completed backtests. Side-by-side comparison table with 21 metrics, color-coded winners. Per-setup comparison table. "Compare" button added to Runs page header.

### 29. Trade list not sortable or filterable — DONE
`RunDetail.jsx`

TradesTab now renders a table with clickable sort headers (entry, stop, exit, PnL, R, score) and filter dropdowns (setup, side, exit reason). Shows count of filtered vs total.

### 30. No CSV/JSON export for trade list — DONE
`RunDetail.jsx`

Export CSV and Export JSON buttons added. CSV generates comma-separated file with 16 columns. JSON exports the full filtered trade array. Both trigger browser download.

---

## P6 — Operational

### 31. No OHLCV ingestion API endpoint — DONE
`control_api.py`

`POST /v1/control/data/ingest` accepts symbols, timeframes, start_date, end_date/days_back. Fetches from Binance in 1000-bar batches with 200ms rate limiting. Upserts into `ohlcv` table. Returns per-symbol bar counts.

### 32. No backtest progress reporting — DONE
`run_executor.py`

Progress events emitted every 10% of total bars. Includes bar count, percentage, and trade count so far.

### 33. Duplicate fingerprint constraint prevents re-running same params — DONE
`control_api.py`

Backtest fingerprint now includes `run_id` (a UUID) as a nonce, producing a unique fingerprint per run even with identical parameters.

### 34. No test coverage for backtest PnL math — DONE
`Backend/tests/test_backtest_pnl.py` (new), `Backend/tests/conftest.py` (new), `Backend/pytest.ini` (new)

pytest configured. 41 tests covering: `_calculate_trade_pnl` (8 tests: long/short win/loss, slippage, fees, R-multiples, zero R-value, explicit R override, zero size), `_check_position_exit` (19 tests: stop loss TOUCH/THROUGH, gap fill, FAIL_FAST long/short, TIME_STOP, trailing stop activation/multi-bar/short, TP1/TP2 TOUCH/THROUGH, priority ordering), position sizing (4 tests: basic, exposure cap, zero R, small capital), summary calculations (10 tests: Sharpe, Sortino, consecutive W/L, payoff ratio, edge cases). All 41 pass.

---

## Resolution Summary

| Group | Total | Done | Deferred | Partial |
|-------|-------|------|----------|---------|
| PC (Canon) | 8 | 8 | 0 | 0 |
| P0 (Blockers) | 4 | 4 | 0 | 0 |
| P1 (Correctness) | 7 | 7 | 0 | 0 |
| P2 (Analytics) | 4 | 4 | 0 | 0 |
| P3 (Data) | 4 | 4 | 0 | 0 |
| P4 (Execution) | 5 | 5 | 0 | 0 |
| P5 (Frontend) | 6 | 6 | 0 | 0 |
| P6 (Operational) | 4 | 4 | 0 | 0 |
| **Total** | **42** | **42** | **0** | **0** |

All 42 items resolved.
