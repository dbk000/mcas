# MCAS UI Audit Results — 2026-02-07

## Summary

| Metric | Count |
|--------|-------|
| Total features tested | 96 |
| PASS | 60 |
| DEGRADED | 17 |
| BROKEN | 4 |
| DEAD / NOT IMPLEMENTED | 15 |

---

## Section 1: App Shell & Navigation

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 1.1 | App loads | PASS | React StrictMode, ErrorBoundary, BrowserRouter, ApiProvider, ToastProvider all wired correctly |
| 1.2 | Navigation links | PASS | 7 nav items + Settings, all routes defined in App.jsx with correct paths |
| 1.3 | Active nav state | PASS | `isActive` logic with amber highlight + left border, handles nested routes |
| 1.4 | Page not found | DEGRADED | Catch-all `*` silently redirects to `/` — no 404 page shown to user |
| 1.5 | API key / auth | PASS | `X-API-Key` header on all requests, auto-redirect to `/settings` on 401, ProtectedRoute guards |
| 1.6 | Responsive layout | DEGRADED | Fixed 256px sidebar, no mobile breakpoints, no hamburger menu — will overflow on small screens |
| 1.7 | WebSocket connection | DEAD | No WebSocket implementation exists. All real-time data uses HTTP polling (5s–30s intervals) |
| 1.8 | Global error handling | PASS | ErrorBoundary with reload button, `window.onerror` + `unhandledrejection` listeners, toast system, `window.__mcas.dumpLogs()` devtools helper |

---

## Section 2: Dashboard

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 2.1 | Dashboard loads | PASS | 4-panel grid: SystemStatus, LatestScan, TopSignals, PaperTrading |
| 2.2 | Market count / stats | PASS | Health/auth/Redis/PG latency in SystemStatus; scan summary stats in LatestScan |
| 2.3 | Live price feed | DEGRADED | No real-time price ticker. Signals poll every 30s, account every 5s. No individual price streaming. |
| 2.4 | Signal summary | PASS | Top 3 signals with symbol, timeframe, setup_id, direction, score, entry price |
| 2.5 | Position summary | PASS | PaperTradingPanel shows capital, PnL, win rate, trade count, drawdown, profit factor |
| 2.6 | Refresh / auto-update | PASS | Signals poll 30s, account polls 5s, RefreshControl for manual refresh |
| 2.7 | Empty state | PASS | EmptyState components for each panel when no data |
| 2.8 | Timestamp display | DEGRADED | Shows `formatRelativeTime` for run finish time and last poll time. No `as_of_ts` from API displayed. |
| 2.9 | Click-through links | PASS | Links to `/runs/{id}`, `/opportunities`, `/paper`, `/control` |

---

## Section 3: Opportunities / Signals

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 3.1 | Page loads | PASS | Signal list renders with polling (15s) |
| 3.2 | Signal cards/rows | PASS | Symbol, setup_id, direction, score, entry/stop/TP, R-value, timeframe, reason |
| 3.3 | Ranking order | PASS | Default sort preserves server order. Canon 1.5D comment at line 118. |
| 3.4 | Score components | DEGRADED | `score_total` shown but individual `score_components` not broken down visually in card |
| 3.5 | Explainability | PASS | Reason text rendered, full JSON payload expandable |
| 3.6 | Timing metadata | PASS | `since_ts` shown as "Active X ago", `action_by_ts` shown as live countdown |
| 3.7 | Filter: setup type | PASS | Dropdown: All / S1 / S2 / U1, passed as API query param |
| 3.8 | Filter: timeframe | DEAD | No timeframe filter exists on Opportunities page |
| 3.9 | Filter: symbol search | DEAD | No symbol search on Opportunities page (only status/setup/sort/score/limit filters) |
| 3.10 | Filter: minimum score | PASS | Number input for `min_score`, passed to API |
| 3.11 | Filter: combination | PASS | All filters combine as API query params (intersection logic server-side) |
| 3.12 | Filter: reset/clear | DEGRADED | Each filter individually resettable but no "Clear All" button |
| 3.13 | Sort controls | PASS | Score (default=server order) / Newest / Expiring Soon. Canon 1.5D compliant. |
| 3.14 | Empty state (filtered) | PASS | "No opportunities found, try adjusting your filters or run a new scan" |
| 3.15 | Empty state (no signals) | PASS | Same EmptyState component |
| 3.16 | Auto-refresh | PASS | 15-second polling via `usePolling` |
| 3.17 | Signal detail click | PASS | Navigates to `/opportunities/{id}` with composite or numeric ID |
| 3.18 | Take action from signal | DEGRADED | Copy Trade Plan button exists, but no direct "paper trade this" button |
| 3.19 | as_of_ts display | BROKEN | Not displayed. Root cause: `ApiContext.jsx:226-228` strips envelope, discarding `meta.as_of_ts` |
| 3.20 | candle_ts display | BROKEN | Not displayed anywhere. Same root cause. |

---

## Section 4: Paper Trading

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 4.1 | Page loads | PASS | 4-tab layout: Account / Positions / Trades / Performance. **BUT see bug below.** |
| 4.2 | Open positions list | PASS | Cards with entry/stop/TP/current prices, PnL, R-multiple, status filter |
| 4.3 | Closed positions list | PASS | Same grid with CLOSED filter, exit reason shown |
| 4.4 | Position detail | DEGRADED | Positions shown as cards in grid, no dedicated full-detail page |
| 4.5 | Enter new position | DEAD | No manual position entry form — positions created only via automated pipeline |
| 4.6 | Validation: missing fields | DEAD | No entry form exists |
| 4.7 | Validation: invalid values | DEAD | No entry form exists |
| 4.8 | Position created | DEAD | Only via signal->paper trade pipeline from ControlPlane |
| 4.9 | Live PnL updates | PASS | Positions poll every 5s via `usePolling` |
| 4.10 | Manual close | PASS | Close button with confirmation modal, POST to `/v1/trading/positions/{id}/close` |
| 4.11 | Stop loss hit | DEGRADED | Stop price shown on card but no proximity indicator or progress bar |
| 4.12 | Target hit | DEGRADED | Targets shown but no visual indicator of which targets have been hit |
| 4.13 | Position sizing / risk | PASS | Account tab shows capital, risk, exposure bar |
| 4.14 | Trade history export | DEAD | No export button on PaperTrading page (RunDetail has export, PaperTrading does not) |
| 4.15 | Empty state | PASS | EmptyState per status filter, "No Active Session" when paper trading inactive |
| 4.16 | Concurrency | PASS | Multiple positions render correctly in grid |

**BUG: PaperTrading.jsx Tabs broken** — `PaperTrading.jsx:657-662` passes `{ id: 'account', ... }` but the `Tabs` component (`ui.jsx:291`) reads `tab.value`. Result: `onChange(undefined)` fires on every click, `activeTab === tab.value` never matches, all tabs appear inactive. The tab content still renders because `activeTab` state is initialized to `'account'` and clicks set it to `undefined`, making all tab content disappear after first click.

---

## Section 5: Run / Backtest

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 5.1 | Run list | PASS | Filterable list with type/status dropdowns, fingerprint search |
| 5.2 | Trigger new run | PASS | QuickScan button in header + full forms in ControlPlane |
| 5.3 | Run status tracking | PASS | Status shown with color-coded badges, created/started/finished timestamps |
| 5.4 | Run detail | PASS | 3-tab view: Overview (stats), Trades (sortable table + export), Events (filterable log) |
| 5.5 | RunCompare page loads | PASS | Side-by-side comparison with run selectors |
| 5.6 | Select runs to compare | PASS | Searchable list of successful backtests with fingerprint |
| 5.7 | Compare metrics | PASS | Color-coded metric rows with higher-is-better logic |
| 5.8 | Champion/Challenger | DEAD | No champion/challenger labels — just "Run A" and "Run B" |
| 5.9 | Backtest parameters | PASS | Full form: name, symbols, timeframes, dates, setups, slippage, fees |
| 5.10 | Backtest results | DEGRADED | SimulationResults loads but auto-refresh broken (see bug below) |
| 5.11 | Empty state | PASS | EmptyState on all sub-pages |

**BUG: SimulationResults.jsx auto-refresh broken** — Line 51 passes `{ refreshInterval: 5000 }` to `useFetch()`, but `useFetch` only supports `skip` and `showError` options. `refreshInterval` is silently ignored. Page loads initial data but never auto-refreshes during running simulations. Manual refresh button works.

---

## Section 6: Markets / Universe

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 6.1 | Markets list | PASS | Grid of market cards grouped alphabetically by base asset |
| 6.2 | Market detail | PASS | Candlestick chart (lightweight-charts) + live snapshot + squeeze state + BTC correlation |
| 6.3 | Market search | PASS | Text filter on symbol/base in Markets page |
| 6.4 | Market status flags | DEGRADED | Liquidity tier shown, but no screened/monitored/tradable flags (Canon 1.4C) |
| 6.5 | OHLCV data display | PASS | Chart with configurable timeframe and candle limit (100/200/500) |
| 6.6 | Indicator values | DEGRADED | Squeeze state panel shown, but not full indicator values per market |
| 6.7 | Chart component | PASS | `lightweight-charts` with TF selector, manual refresh |
| 6.8 | Chart indicator overlays | DEAD | No BB/EMA/SMA overlays on chart |

---

## Section 7: Settings / Configuration

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 7.1 | Settings page loads | PASS | Base URL + API Key form with connection tester |
| 7.2 | API connection status | PASS | Health, auth, Redis/PG latency displayed after test |
| 7.3 | Timeframe selection | DEAD | No user-level timeframe preference setting |
| 7.4 | Setup enable/disable | DEAD | No per-setup toggle in Settings |
| 7.5 | Theme / display prefs | DEAD | Dark mode only, no theme toggle |
| 7.6 | Notification settings | DEAD | No notification controls |

---

## Section 8: API Contract Compliance (Canon 1.5)

| # | Check | Verdict | Notes |
|---|-------|---------|-------|
| 8.1 | `as_of_ts` present in UI | BROKEN | **Zero pages display `as_of_ts`.** Root cause: `ApiContext.jsx:226-228` unwraps `{data, meta}` envelope, returning only `data` and discarding `meta.as_of_ts`. |
| 8.2 | `candle_ts` present in UI | BROKEN | Not surfaced in any structured display. May be visible inside raw JSON payload on OpportunityDetail. |
| 8.3 | No client-side indicator math | PASS | Confirmed across all 22 files. Zero indicator computation in frontend. |
| 8.4 | No client-side ranking | PASS | Default sort preserves server order. Display reordering (newest/expiring) is documented Canon 1.5D. |
| 8.5 | Deterministic IDs only | PASS | All IDs from server. Composite keys (`S2:BTCUSDT:1m`) match server format. |
| 8.6 | `since_ts` in signals | PASS | Displayed in Opportunities.jsx and OpportunityDetail.jsx with relative time + absolute datetime |
| 8.7 | `action_by_ts` in signals | PASS | Displayed with live countdown timer in both pages |
| 8.8 | Error responses | PASS | Structured error handling: 401 redirect, error toasts, ErrorBoundary |
| 8.9 | Stale data indicator | DEGRADED | MarketDetail has 20s staleness check but uses client-side `lastUpdated`, not server `as_of_ts` |

---

## Section 9: Edge Cases & Stress

| # | Feature | Verdict | Notes |
|---|---------|---------|-------|
| 9.1 | API timeout | PASS | Loading states + error toasts via `useFetch`/`usePolling` error handling |
| 9.2 | Empty API responses | PASS | `EmptyState` components on all data pages |
| 9.3 | Malformed API data | DEGRADED | Null checks with `?.` and `|| '—'` fallbacks, but no schema validation |
| 9.4 | Rapid navigation | PASS | React Router, polling cleanup via `useEffect` return, `RouteLogger` with correlation ID rotation |
| 9.5 | WebSocket disconnect | DEAD | No WebSocket exists — polling only |
| 9.6 | Large data set | DEGRADED | No virtual scroll. SimulationResults has pagination. Opportunities defaults to 50 limit. |
| 9.7 | Concurrent updates | PASS | Polling replaces state atomically, no observable flickering |
| 9.8 | Browser back/forward | PASS | React Router handles navigation state |
| 9.9 | Double-click protection | DEGRADED | Loading state disables buttons during API calls, but no explicit debounce for rapid clicks before async starts |

---

## BROKEN Features (fix required)

| ID | Feature | Root Cause | Fix Required |
|----|---------|-----------|-------------|
| 3.19 | `as_of_ts` not displayed | `ApiContext.jsx:226-228` strips envelope, discards `meta.as_of_ts` | Preserve `meta` from envelope and surface `as_of_ts` in UI (e.g., in RefreshControl or page headers) |
| 3.20 | `candle_ts` not displayed | Same root cause — `meta` discarded | Include `candle_ts` in signal/opportunity display |
| 8.1 | `as_of_ts` compliance | Same as 3.19 | Systemic fix: modify `apiRequest()` to return `{data, meta}` or store `meta` separately |
| 8.2 | `candle_ts` compliance | Same as 3.20 | Expose `candle_ts` in structured UI for signal/opportunity views |

**Additional confirmed bugs (not in audit spec but discovered during audit):**

| Bug | File | Root Cause | Impact |
|-----|------|-----------|--------|
| PaperTrading tabs broken | `PaperTrading.jsx:657-662` | Passes `{ id: ... }` but `Tabs` component reads `tab.value` | Clicking any tab sets `activeTab=undefined`, all tab content vanishes |
| SimulationResults no auto-refresh | `SimulationResults.jsx:51` | Passes `refreshInterval: 5000` to `useFetch()` which ignores it | Running simulations don't auto-update; user must manually click refresh |

---

## DEAD Features (not implemented)

| ID | Feature | Missing Component | Canon Reference |
|----|---------|-------------------|-----------------|
| 1.7 | WebSocket connection | No WS implementation anywhere in frontend | — (nice-to-have, polling works) |
| 3.8 | Filter: timeframe | No TF filter on Opportunities page | — |
| 3.9 | Filter: symbol search | No symbol search on Opportunities page | — |
| 4.5-4.8 | Manual position entry | No entry form — positions only via automated pipeline | — |
| 4.14 | Trade history export | No export button on PaperTrading page | — |
| 5.8 | Champion/Challenger labels | RunCompare uses "Run A"/"Run B" only | Canon 1.7 |
| 6.8 | Chart indicator overlays | No BB/EMA/SMA overlays on candlestick chart | — |
| 7.3-7.6 | Settings preferences | No TF selection, setup toggle, theme, or notifications | — |
| 9.5 | WebSocket disconnect handling | No WS exists | — |

---

## DEGRADED Features (fix recommended)

| ID | Feature | Issue | Severity |
|----|---------|-------|----------|
| 1.4 | Page not found | Silently redirects to `/`, no 404 page | Low |
| 1.6 | Responsive layout | Fixed sidebar, no mobile breakpoints | Medium |
| 2.3 | Live price feed | No real-time streaming, polling only | Low |
| 2.8 | Timestamp display | No `as_of_ts` shown on dashboard | Medium |
| 3.4 | Score components | Score shown but components not visually broken down | Low |
| 3.12 | Filter: reset/clear | No "Clear All" button | Low |
| 3.18 | Take action from signal | Copy trade plan only, no direct paper trade button | Medium |
| 4.4 | Position detail | Cards only, no full detail page | Low |
| 4.11 | Stop loss proximity | Stop shown but no visual proximity indicator | Low |
| 4.12 | Target progress | Targets shown but no hit/progress indicator | Low |
| 5.10 | Backtest results auto-refresh | `refreshInterval` silently ignored by `useFetch` | Medium |
| 6.4 | Market status flags | Only liquidity tier, no screened/monitored/tradable | Low |
| 6.6 | Indicator values per market | Only squeeze state, not full indicator panel | Low |
| 8.9 | Stale data indicator | Uses client-side time, not server `as_of_ts` | Medium |
| 9.3 | Malformed API data | Null checks but no schema validation | Low |
| 9.6 | Large data set | No virtual scroll for 200+ items | Medium |
| 9.9 | Double-click protection | Loading state helps but no debounce | Low |

---

## Canon 1.5 Violations Found

| Endpoint/Component | Violation | Details |
|--------------------|-----------|---------|
| `ApiContext.jsx:226-228` | Envelope stripping discards `meta.as_of_ts` | All pages: backend sends `{data, meta: {as_of_ts}}` but frontend returns only `data`, making `as_of_ts` inaccessible to any component |
| All data pages | `as_of_ts` never displayed | User cannot determine backend data freshness — only sees client-side poll time |
| All data pages | `candle_ts` never displayed | Per-value candle timestamp not surfaced in structured UI |
| `PaperTrading.jsx` | `since_ts`/`action_by_ts` not shown for positions | Position cards omit signal timing metadata |
| `RunDetail.jsx` (Trades tab) | `since_ts`/`action_by_ts` not shown in trades table | Trade rows omit signal timing metadata |
| `RunDetail.jsx` (Trades tab) | `candle_ts`/`signal_bar_ts` not shown | No candle reference timestamp per trade |

---

## Recommended Fix Priority

### P0 — Broken (fix immediately)

1. **PaperTrading Tabs bug**: Change `{ id: 'account', ...}` to `{ value: 'account', ...}` in `PaperTrading.jsx:657-662`. One-line fix per tab entry. Without this fix, the Paper Trading page is unusable after the first tab click.

2. **Envelope `as_of_ts` preservation**: Modify `ApiContext.jsx:226-228` to preserve `meta` alongside `data`. Either:
   - Return `{ data: data.data, meta: data.meta }` and update hooks to expose `meta`, or
   - Store the latest `as_of_ts` in ApiContext state for consumption by components.
   This is the root cause of Canon 1.5 `as_of_ts`/`candle_ts` violations across the entire frontend.

### P1 — Degraded (fix this sprint)

3. **SimulationResults auto-refresh**: Replace `useFetch(..., { refreshInterval: 5000 })` with `usePolling(..., 5000)` on `SimulationResults.jsx:49-52`.

4. **Surface `as_of_ts` in RefreshControl**: After P0-2 is done, pass `meta.as_of_ts` to `RefreshControl` and display "Server: X ago" alongside the existing client poll time.

5. **Add `candle_ts` to signal displays**: Show the originating candle timestamp on Opportunities cards and OpportunityDetail.

6. **Opportunities page filters**: Add timeframe dropdown and symbol search filters (currently DEAD).

7. **Mobile responsive**: Add responsive breakpoints and collapsible sidebar for mobile.

### P2 — Dead features to implement (scheduled)

8. **WebSocket for real-time updates**: Replace polling with WS for price feed and signal updates.
9. **Chart indicator overlays**: Add BB/EMA/SMA overlays to the candlestick chart.
10. **Manual position entry**: Add form for manually entering paper trade positions.
11. **Trade export from PaperTrading**: Add CSV/JSON export button (exists on RunDetail but not PaperTrading).
12. **Champion/Challenger labels**: Add labeling in RunCompare.
13. **Settings preferences**: Timeframe selection, setup toggles, theme, notifications.
14. **404 page**: Show a proper "Not Found" page instead of silent redirect.
