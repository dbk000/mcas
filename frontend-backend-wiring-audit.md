# Frontend → Backend Wiring Audit

**Generated:** 2026-04-05
**Frontend:** 31K lines, 25 pages, ~52 unique endpoint calls
**Backend:** ~135 endpoints across 18 routers + inline
**MSW:** 88 handlers (5 authored + 83 generated), dev-only, `onUnhandledRequest: 'bypass'`

---

## 1. Page-by-Page Audit

### 1.1 Landing (`src/pages/Landing.jsx`)
| Field | Value |
|-------|-------|
| Route | `/` |
| Data hooks | None |
| Endpoints | None |
| Status | N/A — static marketing page |
| Displays | Marketing layout via `LayoutMagazine` |
| Current state | Renders fully (no backend dependency) |

### 1.2 Dashboard (`src/pages/Dashboard.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app` (index) |
| Data hooks | `useLatestRun('SCAN')`, `useTopSignals({ limit: 3 })`, `useTradingAccount()`, `useApi().get(...)` |
| Endpoints | `GET /v1/runs/latest`, `GET /v1/signals/top`, `GET /v1/trading/account`, `GET /v1/market/fear-greed` |
| Status | All **LIVE** |
| Displays | System status, latest scan, top signals, paper trading snapshot, Fear & Greed gauge |
| Current state | Fully functional against real backend |

### 1.3 Strategy Dashboard (`src/pages/StrategyDashboard.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/strategies` |
| Data hooks | `useStrategies()`, `useOpportunities(...)`, `usePublicJson(...)` |
| Endpoints | `GET /v1/strategies`, `GET /v1/opportunities` |
| Status | Both **LIVE**. Public JSON fallback for demo mode |
| Displays | Ranked strategy leaderboard with live opportunity counts |
| Current state | Fully functional; falls back to curated demo JSON if no live data |

### 1.4 Opportunities (`src/pages/Opportunities.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/opportunities` |
| Data hooks | `useOpportunities(params)`, `useStrategies()`, `useApi().request(...)`, `useApi().post(...)` |
| Endpoints | `GET /v1/opportunities`, `GET /v1/strategies`, `GET /v1/opportunities/csv`, `POST /v1/control/runs/simulation` |
| Status | All **LIVE** |
| Displays | Filtered/sorted opportunity list, simulation modal, curve viewer |
| Current state | Fully functional |

### 1.5 Opportunity Survey (`src/pages/OpportunitySurvey.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/opportunities/survey` |
| Data hooks | `useOpportunitySurveyData({ get, searchParams })` |
| Endpoints | `GET /v1/opportunities` (multiple calls with different params), `GET /v1/opportunities/{id}/price-window` |
| Status | All **LIVE** |
| Displays | Stacked survey of expired opportunities with reveal modes |
| Current state | Fully functional |

### 1.6 Opportunity Detail (`src/pages/OpportunityDetail.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/opportunities/:id` |
| Data hooks | `useOpportunity(id)`, `useLiveOpportunity(setup_id, symbol, tf)`, `usePublicJson(...)` |
| Endpoints | `GET /v1/opportunities/{id}`, `GET /v1/opportunities/live/{setup_id}/{symbol}/{tf}` |
| Status | Both **LIVE**. Public JSON for Glass Box walkthrough demo |
| Displays | Trade plan, indicators, entry/exit levels, Glass Box walkthrough |
| Current state | Fully functional |

### 1.7 Markets (`src/pages/Markets.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/markets` |
| Data hooks | `useMarkets()` |
| Endpoints | `GET /v1/markets` |
| Status | **LIVE** |
| Displays | Grid of trading pairs grouped by base symbol, filtered by liquidity tier |
| Current state | Fully functional |

### 1.8 Market Detail (`src/pages/MarketDetail.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/markets/:symbol` |
| Data hooks | `useTimeframes()`, `useLiveOHLCV(...)`, `useSnapshot(...)`, `useSqueezeState(...)`, `useBtcSqueezeStatus(...)`, `useApi().get(...)` |
| Endpoints | `GET /v1/timeframes`, `GET /v1/ohlcv`, `GET /v1/snapshot/minute`, `GET /v1/squeeze/state`, `GET /v1/btc/squeeze-status`, `GET /v1/symbols/{symbol}/seasonality` |
| Status | All **LIVE** |
| Displays | Live candlestick chart, market snapshot, squeeze state, BTC correlation, seasonality |
| Current state | Fully functional |

### 1.9 Runs (`src/pages/Runs.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/runs` |
| Data hooks | `useRuns(filters)`, `useApi().get(...)` |
| Endpoints | `GET /v1/runs`, `GET /v1/runs/fingerprint/{fingerprint}` |
| Status | Both **LIVE** |
| Displays | Run history with filters by type/status, fingerprint search |
| Current state | Fully functional |

### 1.10 Run Compare (`src/pages/RunCompare.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/runs/compare` |
| Data hooks | `useRuns(...)`, `useRun(idA)`, `useRun(idB)` |
| Endpoints | `GET /v1/runs`, `GET /v1/runs/{id}` (x2) |
| Status | All **LIVE** |
| Displays | Side-by-side backtest run comparison with metrics |
| Current state | Fully functional |

### 1.11 Run Detail (`src/pages/RunDetail.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/runs/:id` |
| Data hooks | `useRun(id)`, `useRunTrades(id, ...)`, `usePublicJson(...)`, `useApi().post(...)` |
| Endpoints | `GET /v1/runs/{id}`, `GET /v1/runs/{id}/trades`, `POST /v1/control/runs/{id}/cancel` |
| Status | All **LIVE**. Public JSON for demo metrics |
| Displays | Run overview, trade list, event log (tabbed) |
| Current state | Fully functional |

### 1.12 Simulation Results (`src/pages/SimulationResults.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/runs/:id/simulation` |
| Data hooks | `useSimulationResultsViewModel(...)` |
| Endpoints | `GET /v1/runs/{id}/simulation-results`, `GET /v1/runs/{id}/simulation-results/csv` |
| Status | Both **LIVE** |
| Displays | Simulation summary stats, per-setup breakdown, trade table with CSV export |
| Current state | Fully functional |

### 1.13 Control Plane (`src/pages/ControlPlane.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/control` |
| Data hooks | `useQueueStatus()`, `useMarkets()`, `usePaperStatus()`, `useStrategies()`, `useApi().post(...)` |
| Endpoints | `GET /v1/control/runs/queue`, `GET /v1/markets`, `GET /v1/control/paper/status`, `GET /v1/strategies`, `POST /v1/control/runs/scan`, `POST /v1/control/runs/backtest`, `POST /v1/control/runs/paper/start`, `POST /v1/control/runs/paper/stop`, `POST /v1/control/runs/{id}/cancel` |
| Status | All **LIVE** |
| Displays | Scan/backtest/paper trading forms, run queue status |
| Current state | Fully functional |

### 1.14 Paper Trading (`src/pages/PaperTrading.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/paper` |
| Data hooks | `useTradingAccount()`, child components: `usePositions(...)`, `useTrades(...)`, `usePerformance()` |
| Endpoints | `GET /v1/trading/account`, `GET /v1/trading/positions`, `GET /v1/trading/trades`, `GET /v1/trading/performance` |
| Status | All **LIVE** |
| Displays | Account overview, positions, trades, performance metrics |
| Current state | Fully functional |

### 1.15 Correlation Analysis (`src/pages/CorrelationAnalysis.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/analytics/correlations` |
| Data hooks | `useCorrelationAnalysis(...)`, `useFetch('/v1/runs', ...)` x2 |
| Endpoints | `GET /v1/runs` (filtered for BACKTEST + SIMULATION), `POST /v1/analytics/correlation`, `GET /v1/analytics/correlation/{id}` |
| Status | All **LIVE** |
| Displays | Correlation matrices for run comparison and indicator relationships |
| Current state | Fully functional |

### 1.16 Pipeline (`src/pages/Pipeline.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/pipeline` |
| Data hooks | `usePipelineGraph(topologyData)`, `usePipelineUrlState(...)`, `useApi().get(...)` |
| Endpoints | `GET /v1/meta/topology` |
| Status | **LIVE** |
| Displays | ReactFlow dependency graph with node details, search, filtering, side panels |
| Current state | Fully functional |

### 1.17 Strategy Builder (`src/pages/StrategyBuilder.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/strategies/builder` |
| Data hooks | `useApi()` direct calls |
| Endpoints | `GET /v1/user-strategies`, `GET /v1/indicators/menu`, `POST /v1/user-strategies`, `PUT /v1/strategies/{id}`, `DELETE /v1/strategies/{id}` |
| Status | GET + POST: **LIVE**. PUT + DELETE: **MISMATCH** (see Section 5) |
| Displays | Drag-and-drop strategy builder with condition templates |
| Current state | Can list and create strategies. Update and delete will fail — frontend calls `/v1/strategies/{id}` but backend expects `/v1/user-strategies/{strategy_id}` |

### 1.18 Heatmap (`src/pages/Heatmap.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/heatmap` |
| Data hooks | `useApi().get(...)` |
| Endpoints | `GET /v1/universe/heatmap?period={period}&tf=1h` |
| Status | **LIVE** |
| Displays | Market heatmap showing price changes across symbols |
| Current state | Fully functional |

### 1.19 Derivatives Dashboard (`src/pages/DerivativesDashboard.jsx`)
| Field | Value |
|-------|-------|
| Route | `/app/derivatives` |
| Data hooks | `useApi().get(...)` |
| Endpoints | `GET /v1/derivatives/dashboard` |
| Status | **LIVE** |
| Displays | Funding rates, open interest, long/short ratios |
| Current state | Fully functional |

### 1.20 Winners (`src/pages/Winners.jsx`)
| Field | Value |
|-------|-------|
| Route | `/winners` |
| Data hooks | None |
| Endpoints | None |
| Status | N/A — static marketing page |

### 1.21 Pricing (`src/pages/Pricing.jsx`)
| Field | Value |
|-------|-------|
| Route | `/pricing` |
| Data hooks | None |
| Endpoints | None |
| Status | N/A — static marketing page |

### 1.22 Upgrade (`src/pages/Upgrade.jsx`)
| Field | Value |
|-------|-------|
| Route | `/upgrade` |
| Data hooks | None |
| Endpoints | None |
| Status | N/A — static page (uses `VITE_STRIPE_PAYMENT_URL` env var) |

### 1.23 Checkout Success (`src/pages/CheckoutSuccess.jsx`)
| Field | Value |
|-------|-------|
| Route | `/checkout/success` |
| Data hooks | None |
| Endpoints | None |
| Status | N/A — static confirmation page |

### 1.24 Checkout Cancel (`src/pages/CheckoutCancel.jsx`)
| Field | Value |
|-------|-------|
| Route | `/checkout/cancel` |
| Data hooks | None |
| Endpoints | None |
| Status | N/A — static cancellation page |

### 1.25 Settings (`src/pages/Settings.jsx`)
| Field | Value |
|-------|-------|
| Route | `/settings` |
| Data hooks | `useApi()` context methods |
| Endpoints | `POST /v1/session` (via `createSession`), `GET /v1/session/bootstrap` (via `refreshSession`), `GET /health` (via `testConnection`) |
| Status | All **LIVE** |
| Displays | Backend connection config, session management, auth status |
| Current state | Fully functional |

---

## 2. Hook-by-Endpoint Map

### 2.1 Core Hooks (`src/hooks/useData.js`)

| Hook | Endpoint | Method | Polling | Used by pages | Schema match |
|------|----------|--------|---------|---------------|-------------|
| `useMarkets()` | `/v1/markets` | GET | No | Markets, ControlPlane | MATCH |
| `useTimeframes()` | `/v1/timeframes` | GET | No | MarketDetail | MATCH |
| `useStrategies()` | `/v1/strategies` | GET | No | StrategyDashboard, Opportunities, ControlPlane | MATCH |
| `useOpportunities(params, enabled)` | `/v1/opportunities` | GET | 15s | StrategyDashboard, Opportunities, OpportunitySurvey | MATCH |
| `useOpportunity(id)` | `/v1/opportunities/{id}` | GET | No | OpportunityDetail | MATCH |
| `useOpportunityPriceWindow(oppId, params)` | `/v1/opportunities/{oppId}/price-window` | GET | No | OpportunitySurvey | MATCH |
| `useLiveOpportunity(setup_id, symbol, tf)` | `/v1/opportunities/live/{setup_id}/{symbol}/{tf}` | GET | No | OpportunityDetail | MATCH |
| `useTopSignals(params, enabled)` | `/v1/signals/top` | GET | 30s | Dashboard | MATCH |
| `useRuns(params)` | `/v1/runs` | GET | No | Runs, RunCompare, CorrelationAnalysis | MATCH |
| `useLatestRun(runType)` | `/v1/runs/latest` | GET | No | Dashboard | MATCH |
| `useRun(id)` | `/v1/runs/{id}` | GET | No | RunDetail, RunCompare | MATCH |
| `useRunTrades(runId, params)` | `/v1/runs/{runId}/trades` | GET | No | RunDetail | MATCH |
| `useRunEvents(runId, params)` | `/v1/runs/{runId}/events` | GET | No | RunDetail | MATCH |
| `useQueueStatus()` | `/v1/control/runs/queue` | GET | 15s | ControlPlane | MATCH |
| `usePaperStatus(enabled)` | `/v1/control/paper/status` | GET | 5s | ControlPlane | MATCH |
| `useTradingAccount(enabled)` | `/v1/trading/account` | GET | 5s | Dashboard, PaperTrading | MATCH |
| `usePositions(params, enabled)` | `/v1/trading/positions` | GET | 5s | PaperTrading | MATCH |
| `useTrades(params)` | `/v1/trading/trades` | GET | No | PaperTrading | MATCH |
| `usePerformance()` | `/v1/trading/performance` | GET | No | PaperTrading | MATCH |
| `useLiveOHLCV(symbol, tf, limit, ms)` | `/v1/ohlcv` | GET | 5s | MarketDetail | MATCH |
| `useSnapshot(symbol, enabled)` | `/v1/snapshot/minute` | GET | 5s | MarketDetail | MATCH |
| `useSqueezeState(symbol, tf)` | `/v1/squeeze/state` | GET | No | MarketDetail | MATCH |
| `useBtcSqueezeStatus(tf)` | `/v1/btc/squeeze-status` | GET | No | MarketDetail | MATCH |
| `useDataAvailability(symbols, tfs)` | `/v1/control/data/availability` | GET | No | (ControlPlane child) | MATCH |

### 2.2 Specialized Hooks

| Hook | Endpoint | Used by | Schema match |
|------|----------|---------|-------------|
| `useLiveFeed()` (`src/hooks/useLiveFeed.js`) | `GET /v1/public/pipeline-feed` | Landing (LiveFeedTicker component) | MATCH |
| `useCorrelationAnalysis(...)` (`src/pages/correlation-analysis/`) | `POST /v1/analytics/correlation`, `GET /v1/analytics/correlation/{id}` | CorrelationAnalysis | MATCH |
| `useSimulationResultsViewModel(...)` (`src/pages/simulation-results/`) | `GET /v1/runs/{id}/simulation-results`, `GET /v1/runs/{id}/simulation-results/csv` | SimulationResults | MATCH |
| `useOpportunitySurveyData(...)` (`src/pages/opportunity-survey/`) | `GET /v1/opportunities`, `GET /v1/opportunities/{id}/price-window` | OpportunitySurvey | MATCH |

### 2.3 Context Providers

| Provider | Endpoints | Used by |
|----------|----------|---------|
| `SessionProvider` | `GET /v1/session/bootstrap`, `POST /v1/session`, `DELETE /v1/session` | App-wide (wraps all routes) |
| `ConnectionProvider` | `GET /health` | App-wide |
| `TelemetryClient` | `POST /v1/telemetry/frontend-logs` | App-wide (automatic) |

---

## 3. MSW Handler Inventory

### 3.1 Authored Overrides (`src/mocks/handlers.js`) — 7 handlers

| MSW path | Method | Has real backend route | Response shape matches | Notes |
|----------|--------|----------------------|----------------------|-------|
| `/v1/session/bootstrap` | GET | **Yes** | Yes | Simulates auth state; returns fixture `frontend_config` |
| `/v1/session` | POST | **Yes** | Yes | Simulates login; sets `hasSession = true` |
| `/v1/session` | DELETE | **Yes** | Yes | Simulates logout |
| `/v1/analytics/dashboard` | GET | **No** | N/A | **No backend route exists.** Returns curated fixture for strategy dashboard demo. Only consumed by MSW — no frontend page calls this path directly |
| `/v1/analytics/metrics` | GET | **No** | N/A | **No backend route exists.** Returns curated fixture for run metrics demo. Only consumed by MSW — no frontend page calls this path directly |
| `/v1/runs/trace/:id` | GET | **No** | N/A | **No backend route exists.** Returns Glass Box causality fixtures. Only consumed by MSW — no page calls this path |
| `/v1/control/data/availability` | GET | **Yes** | Yes | Returns fixture universe/freshness data |

**Verdict:** The 3 non-matching MSW handlers (`analytics/dashboard`, `analytics/metrics`, `runs/trace/:id`) are demo fixture endpoints. They exist only to power curation/demo states via `X-Fixture-State` headers and public JSON files. No frontend page calls these paths at runtime — the pages that consume this data use `usePublicJson()` to load the fixtures directly from static files instead. These MSW handlers could be removed without impact.

### 3.2 Generated Handlers (`src/mocks/generated/handlers.js`) — 83 handlers

All generated from `openapi.json` via `msw-auto-mock`. Every generated handler has a matching backend route. Response shapes are Faker-generated (seeded, deterministic) and structurally match the OpenAPI schema but contain nonsense data.

| Category | Count | All match backend? |
|----------|-------|-------------------|
| GET endpoints | 56 | Yes |
| POST endpoints | 27 | Yes |

**MSW activation:** Dev-only (`import.meta.env.DEV`). `onUnhandledRequest: 'bypass'` — in dev, real backend calls pass through MSW to the actual API. MSW only intercepts if a handler is registered for that exact path. Since authored handlers take precedence (loaded first), the 7 authored handlers override their generated counterparts for session/bootstrap.

---

## 4. Governed.js Polling Gaps

| Polling key | Interval | Frontend hook | Endpoint | Live? | Notes |
|-------------|----------|--------------|----------|-------|-------|
| `opportunities_ms` | 15s | `useOpportunities()` | `GET /v1/opportunities` | **Yes** | Used by StrategyDashboard, Opportunities |
| `positions_ms` | 5s | `usePositions()` | `GET /v1/trading/positions` | **Yes** | Used by PaperTrading |
| `trading_account_ms` | 5s | `useTradingAccount()` | `GET /v1/trading/account` | **Yes** | Used by Dashboard, PaperTrading |
| `paper_status_ms` | 5s | `usePaperStatus()` | `GET /v1/control/paper/status` | **Yes** | Used by ControlPlane |
| `ohlcv_ms` | 5s | `useLiveOHLCV()` | `GET /v1/ohlcv` | **Yes** | Used by MarketDetail |
| `snapshot_ms` | 5s | `useSnapshot()` | `GET /v1/snapshot/minute` | **Yes** | Used by MarketDetail |
| `signals_top_ms` | 30s | `useTopSignals()` | `GET /v1/signals/top` | **Yes** | Used by Dashboard |
| `queue_status_ms` | 15s | `useQueueStatus()` | `GET /v1/control/runs/queue` | **Yes** | Used by ControlPlane |
| `public_feed_ms` | 12s | `useLiveFeed()` | `GET /v1/public/pipeline-feed` | **Yes** | Used by Landing/LiveFeedTicker |

**Result: Zero polling gaps.** All 9 governed polling keys have live backend endpoints and active frontend consumers.

---

## 5. Missing / Mismatched Backend Endpoints

### 5.1 Path Mismatches (frontend calls wrong path)

| Frontend path | Frontend caller | Backend path | Issue |
|---------------|----------------|-------------|-------|
| `PUT /v1/strategies/{id}` | StrategyBuilder.jsx:428 | `PUT /v1/user-strategies/{strategy_id}` | Path segment mismatch: `strategies` vs `user-strategies` |
| `DELETE /v1/strategies/{id}` | StrategyBuilder.jsx:445 | `DELETE /v1/user-strategies/{strategy_id}` | Same mismatch |

**Impact:** Strategy update and delete operations will return 405 Method Not Allowed (the `GET /v1/strategies` route exists but doesn't accept PUT/DELETE). Users can create strategies but cannot edit or delete them.

**Fix:** Change frontend paths from `/v1/strategies/{id}` to `/v1/user-strategies/{id}`.

### 5.2 MSW-only endpoints (no backend route, no frontend caller)

| MSW path | Purpose | Action needed |
|----------|---------|--------------|
| `GET /v1/analytics/dashboard` | Demo fixture for strategy dashboard | **Remove** — frontend uses `usePublicJson()` for this data, not an API call |
| `GET /v1/analytics/metrics` | Demo fixture for run metrics | **Remove** — same reason |
| `GET /v1/runs/trace/:id` | Demo fixture for Glass Box cases | **Remove** — same reason |

These are vestigial from an earlier design where demo data was served by the API. The frontend now loads these fixtures from static JSON files via `usePublicJson()`.

### 5.3 Endpoints that exist nowhere

No frontend page calls an endpoint that is completely absent from both the backend AND MSW. All ~50 frontend endpoint calls have either a matching backend route or an MSW handler (most have both).

---

## 6. Unused Backend Endpoints (no frontend consumer)

### 6.1 Main Router (inline in `main.py`)

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `GET /v1/auth/ping` | N/A (auth check) | Internal/tooling use |
| `GET /v1/pipeline/stop_policy_template` | Config | Pipeline page could surface this |
| `GET /v1/universe` | Postgres universe table | Markets page could use for richer view |
| `POST /v1/signals/queue` | N/A (enqueue action) | ControlPlane could add manual signal queueing |
| `GET /v1/signals/{signal_id}` | Postgres signals table | OpportunityDetail could link to source signal |

### 6.2 Trading Router — 2 unused

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `GET /v1/trading/positions/{position_id}` | Postgres paper_positions | PaperTrading position detail view |
| `POST /v1/trading/positions/{position_id}/close` | Action | PaperTrading manual close button |

### 6.3 Emergency Router — 4 unused (entire router)

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `POST /v1/emergency/kill` | Action | **High priority** — needs UI surface |
| `POST /v1/emergency/pause` | Action | **High priority** — needs UI surface |
| `POST /v1/emergency/resume` | Action | **High priority** — needs UI surface |
| `GET /v1/emergency/status` | Redis state | **High priority** — needs UI surface |

**Note:** Emergency controls have no frontend page at all. Consider adding an Emergency panel to ControlPlane or a dedicated EmergencyControl page.

### 6.4 Analytics Router — 5 unused

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `GET /v1/analytics/correlation` | Postgres | CorrelationAnalysis could show recent analyses list |
| `POST /v1/analytics/insights` | Action | Post-analysis insight capture |
| `PUT /v1/analytics/insights/{id}` | Action | Insight editing |
| `GET /v1/analytics/insights` | Postgres | Insights dashboard |
| `GET /v1/analytics/insights/{id}` | Postgres | Insight detail |

### 6.5 Champion/Challenger Router — 20 unused (entire router)

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `POST /v1/cc/variant` | Action | Strategy variant creation |
| `POST /v1/cc/challenger/from/{id}` | Action | Challenger creation |
| `GET /v1/cc/champions` | Postgres | Champion list |
| `GET /v1/cc/variants` | Postgres | Variant list |
| `GET /v1/cc/variant/{id}` | Postgres | Variant detail |
| `POST /v1/cc/run` | Action | Start experiment |
| `POST /v1/cc/run/{id}/close` | Action | End experiment |
| `POST /v1/cc/run/{id}/promote` | Action | Promote challenger |
| `POST /v1/cc/run/{id}/cancel` | Action | Cancel experiment |
| `GET /v1/cc/runs` | Postgres | Experiment list |
| `GET /v1/cc/run/{id}` | Postgres | Experiment detail |
| `GET /v1/cc/run/{id}/metrics` | Postgres | Experiment metrics |
| `GET /v1/cc/run/{id}/assets` | Postgres | Per-asset metrics |
| `GET /v1/cc/run/{id}/trades` | Postgres | Experiment trades |
| `GET /v1/cc/diagnostics` | Postgres | Trade diagnostics |
| `GET /v1/cc/change-ledger` | Postgres | Change history |

**Note:** Full A/B testing framework with no frontend. Needs a dedicated CC page.

### 6.6 Equities Router — 8 unused (entire router)

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `GET /v1/equities/opportunities` | Postgres | Equities opportunity list |
| `GET /v1/equities/opportunities/{id}` | Postgres | Equities opp detail |
| `GET /v1/equities/opportunities/{id}/explain` | Postgres | Explainability payload |
| `GET /v1/equities/dashboard/performance` | Computed | Performance dashboard |
| `GET /v1/equities/dashboard/realized` | Computed | Realized performance |
| `GET /v1/equities/dashboard/policy` | Config | Policy dashboard |
| `GET /v1/equities/pipeline/health` | Redis/Postgres | Pipeline health |
| `POST /v1/equities/simulation` | Action | Run equities simulation |
| `POST /v1/runs/equities-simulation` | Action | Fixed-horizon simulation |

**Note:** Complete equities forward predictor with no frontend. Needs a dedicated Equities page.

### 6.7 Unicorns Router — 3 unused (entire router)

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `GET /v1/unicorns/embryo-active` | Postgres/Redis | Ranked embryo candidates |
| `GET /v1/unicorns/pipeline-status` | Redis | Pipeline metadata |
| `GET /v1/unicorns/{symbol}` | Postgres | Full explainability payload |

**Note:** Ignition pipeline embryo tracker with no frontend. Needs a dedicated Unicorns page.

### 6.8 Stats Router — 4 unused (entire router)

| Endpoint | Data available | Recommendation |
|----------|---------------|----------------|
| `GET /v1/stats/opportunities` | Computed | Rolling opportunity stats |
| `GET /v1/stats/opportunities/detail` | Computed | Per-opp drill-down |
| `GET /v1/stats/go-live` | Computed | Unified go-live stats |
| `GET /v1/stats/go-live/detail` | Computed | Go-live drill-down |

**Note:** Go-live readiness metrics with no frontend. Could be added to Dashboard or a dedicated Stats page.

### 6.9 Meta Router — 41 unused (only `topology` is used)

| Category | Endpoints | Count |
|----------|----------|-------|
| Node versions | `GET /versions`, `GET /version/{id}`, `POST /node/version`, `PUT staleness/health/output-hash`, `PATCH pareto_status` | 7 |
| Events & lineage | `GET /version/{id}/events`, `POST /version/{id}/event`, `GET /event/{id}/causal_chain`, `GET /events/recent`, `GET /events/search` | 5 |
| Experiments | `GET /experiments`, `GET /experiment/{id}`, `POST /experiment`, `PATCH /experiment/{id}`, `POST /experiment/{id}/update` | 5 |
| Compositions | `GET /compositions`, `GET /composition/{id}`, `POST /composition`, `POST /deploy`, `GET /significance`, `POST /sync-waves`, `POST /constraint`, `GET /space/estimate`, `POST /draft`, `PUT /draft` | 10 |
| Drift & health | `GET /drift`, `POST /propagate-staleness` | 2 |
| Dependencies | `GET /dependency-audit`, `GET /reachability`, `GET /pareto_frontier` | 3 |
| Search & compare | `GET /search/versions`, `GET /compare/versions/{a}/{b}`, `GET /compare/compositions/{a}/{b}` | 3 |
| Sweep | `POST /sweep/register`, `POST /sweep/results` | 2 |
| Datasets & provenance | `GET /pipelines`, `POST /pipeline`, `POST /datasets`, `GET /datasets`, `GET /openlineage/events`, `GET /provenance/search` | 6 |
| Stop policy | `GET /stop_policy/audit` | 1 |

**Note:** The Pipeline page currently only uses `GET /v1/meta/topology`. The remaining 41 meta endpoints power node detail panels, event timelines, composition management, and experiment tracking — all features that could be surfaced in the Pipeline page's side panels.

### 6.10 Other Unused

| Endpoint | Router | Data available | Recommendation |
|----------|--------|---------------|----------------|
| `GET /v1/d2/status` | d2_router (main.py) | Postgres d2_cluster_events + paper_positions | D2 paper trader status page |
| `GET /v1/analytics/indicator-correlations` | market_intel_router | Computed | Could enhance CorrelationAnalysis page |
| `GET /v1/strategies/{strategy_id}/opportunities` | strategies_router | Postgres | StrategyBuilder or StrategyDashboard |

---

## 7. Summary

### Connection Status Overview

| Category | Count |
|----------|-------|
| Total frontend pages | 25 |
| Pages with backend dependencies | 19 |
| Static/marketing pages (no backend) | 6 |
| Total unique frontend → backend endpoint calls | ~52 |
| Endpoints confirmed LIVE | **50** |
| Endpoints with path MISMATCH | **2** (StrategyBuilder PUT/DELETE) |
| MSW-only endpoints (no backend, no frontend caller) | **3** (vestigial demo fixtures) |
| Total backend endpoints | ~135 |
| Backend endpoints with frontend consumers | **~44** |
| Backend endpoints with no frontend consumer | **~91** |

### Largest Unwired Backend Surfaces

| Router | Unused endpoints | Priority |
|--------|-----------------|----------|
| Meta | 41 | Medium — Pipeline page could surface node detail, events, compositions |
| CC (Champion/Challenger) | 20 | High — complete A/B framework with zero UI |
| Equities | 9 | High — complete forward predictor with zero UI |
| Analytics (insights) | 5 | Medium — insights capture/review |
| Emergency | 4 | **Critical** — kill switch has no UI |
| Stats | 4 | Medium — go-live readiness dashboard |
| Unicorns | 3 | Medium — embryo tracker |
| Trading (detail/close) | 2 | Low — minor PaperTrading enhancements |

### Bugs Found

1. **StrategyBuilder update/delete path mismatch** — Frontend calls `PUT/DELETE /v1/strategies/{id}`, backend expects `/v1/user-strategies/{strategy_id}`. Strategy edit and delete are broken.

### Key Architectural Notes

- **Auth model:** Session-based. API key sent once to `POST /v1/session`, then session cookie used for all subsequent requests via `credentials: 'include'`.
- **Response envelope:** All backend responses wrapped in `{ data: ..., meta: { timestamp, version, compute_ms, as_of_ts } }`. Frontend `apiClient.js` unwraps automatically and attaches `__meta`.
- **Polling governance:** All intervals delivered by backend via `GET /v1/session/bootstrap → frontend_config`. Frontend `governed.js` is the single source of truth. Backend can tune polling without a frontend deploy.
- **MSW bypass:** In dev, MSW is always active but unhandled requests pass through to real backend. This means the app works against either MSW fixtures or a real backend transparently.
- **Feature flags:** 30+ pipeline feature flags delivered via bootstrap, consumed via `getFeatureFlag()`. All Wave 2-5 features enabled by default.
