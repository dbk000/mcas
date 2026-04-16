# R1 — Pre-Backfill Data-Health Read — Findings

**Item:** R1 (playbook §5)
**Script:** `Backend/scripts/equities_data_health_read.py`
**CP-2 verdict:** FAIL (Q1 only; Q2/Q3/Q4/Q5 all PASS)
**Decision-gate impact:** blocks B1 (Batch 5 universe backfill) until Darren explicitly approves proceed or remediates KLAC.
**Q3 note:** Q3 PASSED → B2 (feature recompute) is NOT blocked by leakage.

## Decisions made

1. **Q1 threshold = 5.0% shortfall.** Named constant `Q1_BAR_SHORTFALL_THRESHOLD_PCT`. Aligned with the review §2.1 wording "symbols with >5% bar shortfall". Strict inequality: a symbol at exactly 5.0% passes; KLAC at 5.46% fails.
2. **Per-symbol observation window, not a fixed global window.** The 70 symbols do NOT all start on 2024-02-23 — 8 of them (`AMAT, KLAC, LRCX, MRVL, MU, SMH, SOXX, TSM`) were admitted on 2025-08-29, visible in the OHLCV min-max ranges. Scoring each symbol against the *global* window would falsely fail those 8 late-admits with ~75% shortfalls. The symbol's own observed (first_bar, last_bar) is the correct baseline for a *continuity* test; "was the symbol in the universe earlier?" is an A11 (universe expansion) question, not an R1 (data-health) one.
3. **Q1 expected-bars formula.** 1d expected = NYSE trading days in window. 1h expected = trading_days × 17 bars/day. 17 is empirically verified for AAPL 2024-06-03 (RTH 09:30–16:00 + ETH 04:00–09:00 + 16:00–20:00, includePrePost=true). Tolerance band of 5% absorbs NYSE early-close half-days, halts, and the 09:30 half-hour bar variability.
4. **NYSE holidays 2024-2026 hardcoded inline** in `NYSE_FULL_CLOSURES` set with a governance note that this is an interim measure until A10 populates `equities_trading_calendar`. Early-close days NOT included — they still ship a daily bar and contribute reduced hourly bars, both absorbed by the 5% tolerance.
5. **Q4 primary query matches the brief verbatim.** `SELECT source, COUNT(*) FROM public.ohlcv WHERE source IN ('equities_yahoo','yahoo_chart') GROUP BY source;`. Cross-check: full source list diff'd against the known-crypto allowlist in `KNOWN_CRYPTO_SOURCES` (11 sources). Result: zero rogue sources.
6. **Q5 regime inference via split-price ratio + fetcher source inspection.** NVDA 10:1 is the only split inside the 2024-02-23 → 2026-04-14 window. TSLA 2022-08-25 and NVDA 2021-07-20 are pre-window; brief explicitly says "if in window". Regime returned as `split-only` because: (a) the split-bracket ratio is 0.9926 (≈1, i.e. prices are adjusted), AND (b) the fetcher at `Backend/scripts/equities_predictor/sources.py:261-332` uses Yahoo chart v8 `indicators.quote[0].close`, not `adjclose`. Yahoo's documented semantics: `quote.close` is split-adjusted but NOT dividend-adjusted; `adjclose` is both. Fetcher code is authoritative evidence; price-ratio alone cannot distinguish split-only from split-and-div.
7. **Q5 PASS on `split-only` regime.** The brief template line reads "split-and-div | split-only | raw — evidence". Any consistent non-`raw` regime is acceptable for passing; the brief's "expected: `split-and-div` for Yahoo source" comment is advisory, not gating — verification was pass-iff-consistent. Dividend-adjustment handling is scoped to A7 (corporate-actions migration); R1 only establishes the current regime truthfully.

## Data sampled

All queries executed against `backend-postgres-1` (production). `DATABASE_URL` routed via host → 172.18.0.11:5432 Docker bridge (pg container IP). Session log: `Backend/scripts/output/equities_data_health_read_20260416_222415.log`.

### Q1 — OHLCV continuity (70 symbols x {1h, 1d} = 140 pairs)

| Outcome | Count |
|---|---|
| PASS (shortfall <= 5%) | 139 pairs |
| FAIL | 1 pair — **KLAC/1h: 2491 bars / 2635 expected (5.46% short)** |

All 70 symbols at 1d: PASS. KLAC 1h is the only FAIL; other late-admit symbols on the same window all pass (`AMAT 1.94%, LRCX 2.20%, MRVL 1.48%, MU 1.44%, SMH 1.63%, SOXX 1.63%, TSM 1.14%`). KLAC missing ≈144 bars over 155 trading days → ≈0.93 bars/day dropped on average. Looks like systematic fetcher hiccups, not a catastrophic gap. Remediation for B1: either backfill the missing 144 KLAC/1h bars from Yahoo chart v8 pre-B1, or accept-risk and proceed (Darren call).

Raw evidence: `q1_continuity_output_20260416_222415.csv` (140 rows, all columns).

### Q2 — equities_feature_events per-symbol coverage

- Window: 2025-08-28 → 2026-04-14
- Threshold: >= 100 feature-events per symbol
- Result: **70 of 70 symbols PASS.** Minimum observed: 2,976 (late-admit symbols). Maximum: ~11,200 (long-standing symbols). Also verified zero symbols in `equities_symbol_registry` are absent from `equities_feature_events` in-window.

Raw evidence: `q2_feature_coverage_20260416_222415.csv`.

### Q3 — available_at_ts integrity (point-in-time leakage)

```sql
SELECT COUNT(*) FROM public.equities_feature_events WHERE available_at_ts <= bar_start_ts;
-- violation_count: 0
```

**PASS.** Every row has `available_at_ts > bar_start_ts`, confirming the append-only trigger correctly prevents future-leakage. The existing 763K feature-event rows are reusable by B2 without leakage risk.

Raw evidence: `q3_leakage_check_20260416_222415.txt`.

### Q4 — source-column consistency

Primary query result:

| source | n_rows | bucket |
|---|---|---|
| equities_yahoo | 275,723 | equity_expected |
| yahoo_chart | 66,170 | equity_expected |

Cross-check over all distinct sources in `public.ohlcv`:

| bucket | count | sources |
|---|---|---|
| equity_expected | 2 | equities_yahoo, yahoo_chart |
| crypto_known | 11 | binance, bitget, bitmart, bybit, coinex, gate, htx, kucoin, mexc, okx, ws |
| unknown | 0 | — |

**PASS.** Zero schema drift. Every source in the table is accounted for.

Raw evidence: `q4_source_counts_20260416_222415.csv`.

### Q5 — adjustment regime per Yahoo source

| symbol | ex_date | ratio | pre_close | post_close | price_ratio | regime |
|---|---|---|---|---|---|---|
| NVDA | 2024-06-10 | 10:1 | 120.89 (2024-06-07) | 121.79 (2024-06-10) | 0.9926 | split-only |

Fetcher source of truth: `indicators.quote[0]` path (Yahoo's split-adjusted non-div series).

**PASS** on `split-only`. Consistent across evaluated splits (N=1 in window). Dividend-adjustment is A7's scope.

Raw evidence: `q5_adjustment_regime_evidence_20260416_222415.txt`.

## Cross-checks

1. **Q1 late-admit sanity.** Manually verified `AMAT, KLAC, LRCX, MRVL, MU, SMH, SOXX, TSM` all start on `2025-08-29` with ~155 trading days to `2026-04-13`. Expected 1h bars (155 × 17 = 2635) matches script output exactly.
2. **Q1 long-standing sanity.** AAPL = 9091/9095 (0.04% short) across ~535 trading days — essentially full fetch, consistent with the known Yahoo chart endpoint behaviour.
3. **Q3 zero-violation cross-check.** `equities_feature_events` has an `append_only_trigger` per the audit; any violation would indicate the trigger is miswired or was bypassed.
4. **Q4 cross-check.** The `postgres_audit.md` report lists 13 distinct sources. Script observed exactly 13. If a new equity source were silently introduced since the audit (e.g. a Polygon pilot fetcher), it would appear in the "unknown" bucket — it did not.
5. **Q5 fetcher-regex cross-check.** Grep of `Backend/scripts/equities_predictor/sources.py` for `adj_close|adjclose|AdjClose` returns zero matches in the fetch path. Only `indicators.quote` pattern is used. Confirms Yahoo's split-adjusted-only series is what lands in `public.ohlcv`.

## Gotchas

1. **Background-task capture swallowed psql stdout in early iterations.** Running psql through `docker exec` inside Claude Code's bash tool occasionally returned no output when the task notification fired before the first stdout write. Fix: run the query to completion in foreground with output redirected to a file, or use the script's own flushed log.
2. **Parallel Seq Scan on public.ohlcv for Q4 is ~3-4 minutes wall-clock.** The table has no standalone `source` index; the unique constraint `(symbol, timeframe, bar_start_ts, source)` is a compound index that Postgres cannot use for a `WHERE source IN (...)` filter efficiently. Plan is a Parallel Seq Scan of ~293M rows. Future: adding a dedicated index on `source` would cut Q4 to seconds, but at ~2 GB index-size cost against a column with 13 distinct values. For a read-once health check, the Seq Scan is acceptable.
3. **Concurrent query contention.** Running the script while other sessions held long-running queries against `public.ohlcv` caused Q4 to block on I/O for minutes. Before re-running, confirm `SELECT now()-query_start FROM pg_stat_activity WHERE state='active'` is empty or only-short-duration queries.
4. **Q5's split-only vs split-and-div is not ratio-resolvable.** If regime were `split-and-div`, the NVDA price ratio would still be ≈1.0 (div-adjustment is a second-order effect on the ex-date price). Distinguishing the two requires cross-checking an ex-div-date close against `close - dividend_amount` across multiple dates OR inspecting the fetcher's API path. The script takes the fetcher-inspection route because it is definitive; price-ratio alone would give a false positive on either regime.

## Stress-test targets

1. **KLAC remediation path.** Before B1, try a targeted Yahoo chart v8 fetch for the ~144 missing KLAC/1h bars in 2025-08-29 → 2026-04-13. If Yahoo returns the same gap count, the gap is upstream (Yahoo side) and the best B1 can do is carry it forward. If Yahoo now returns the full 2635, re-run R1 to confirm KLAC passes post-patch.
2. **NVDA split-adjustment regression.** When A7 (corporate-actions migration) lands, re-run Q5 to verify the regime flag persisted in `equities_corporate_actions` matches `split-only`. Any silent promotion to `split-and-div` would invalidate downstream simulation outputs.
3. **New equity sources.** If B1's universe expansion brings a third equity-origin source (e.g. a Stooq or Polygon fetcher), Q4 must start accepting it. Add to `EXPECTED_EQUITY_SOURCES` **AND** to `postgres_audit.md` in one commit, never just the one.
4. **Q1 threshold sensitivity.** The 5% tolerance was picked from the review; with KLAC at 5.46% the result is knife-edge. If a second symbol ever lands at ≈5-6% and Darren accepts both, consider whether the true threshold should be 7% (empirical ceiling for late-admit fetches) or whether the criterion should switch to a per-day missing-bar rate (fixed absolute gap allowance, not a proportion).
5. **Holiday list drift.** `NYSE_FULL_CLOSURES` is hardcoded for 2024-2026. Re-running this script in 2027 without updating the set will overcount expected 1d bars by ~10 days/yr → every long-standing symbol will look ~4% short. A10's `equities_trading_calendar` must replace this set.
