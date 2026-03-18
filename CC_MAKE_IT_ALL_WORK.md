# CC Task: Make Frontend ↔ Backend Contract Work

## Context

Opus audited every frontend hook against the backend API surface. Two confirmed endpoint mismatches (silent 404s), one newly built backend endpoint to wire up, and a sweep to confirm nothing else is broken. No scoping — just fix and verify.

## Rules

- Do NOT refactor anything not listed here.
- After each task, run `npm run build` — if it fails, stop and report.
- Bundle budget is 98.5% used. Do NOT add imports or dependencies.

---

## Task 1: Fix useQueueStatus endpoint path

**Problem:** Frontend calls `/v1/control/queue`. Backend endpoint is `/v1/control/runs/queue`.

**File:** Find `useQueueStatus` definition (likely in `src/hooks/useData.js`).

**Fix:** Change the endpoint string from `/v1/control/queue` to `/v1/control/runs/queue`.

**Also fix MSW:**
```bash
grep -rn "control/queue" src/mocks/ --include="*.js"
```
Update any mock handler paths to match.

**Verify:**
```bash
grep -rn "control/queue" src/ --include="*.js" --include="*.jsx"
# Every hit should show /v1/control/runs/queue
```

---

## Task 2: Wire useDataAvailability to new backend endpoint

**Problem:** Frontend calls `/v1/control/data/availability`. This endpoint now exists in the backend (just deployed).

**File:** Find `useDataAvailability` definition (likely in `src/hooks/useData.js`).

**Check:** Confirm the hook path is `/v1/control/data/availability`. If it's already that exact path, no change needed — the backend now serves it. If the path is different, fix it to `/v1/control/data/availability`.

**Response shape the hook should expect:**
```json
{
  "data": {
    "universe": { "known": 150, "monitored": 100, "with_ohlcv": 95 },
    "timeframes": [
      {
        "timeframe": "1h",
        "symbol_count": 95,
        "total_rows": 1200000,
        "latest_bar": "2026-03-16T12:00:00+00:00",
        "earliest_bar": "2025-01-01T00:00:00+00:00",
        "freshest_ingestion": "2026-03-16T12:05:00+00:00",
        "lag_seconds": 300
      }
    ],
    "stale_symbols": [],
    "stale_count": 0,
    "healthy": true
  },
  "meta": { "timestamp": "...", "version": "v1", "compute_ms": 42.1 }
}
```

**Also add/update MSW handler** for dev mode. Create a fixture that returns the shape above with realistic test data. Add it to the authored handlers (not generated).

**Verify:**
```bash
grep -rn "data/availability\|useDataAvailability" src/ --include="*.js" --include="*.jsx"
# Should show consistent path and usage
```

---

## Task 3: Full endpoint audit sweep

Run this and report the results. Do NOT fix anything in this task — just report.

```bash
echo "=== All API endpoint paths used in hooks ==="
grep -oP "['\"](/v1/[^'\"]+)['\"]" src/hooks/useData.js | sort -u

echo ""
echo "=== All API calls outside hooks (pages, components, contexts) ==="
grep -rn "\.get(\|\.post(\|\.request(\|fetch(" src/pages/ src/components/ src/context/ --include="*.js" --include="*.jsx" | grep -oP "['\"](/v1/[^'\"]+)['\"]" | sort -u

echo ""
echo "=== Session/auth endpoints ==="
grep -rn "/v1/session\|/health\|/v1/auth" src/context/ --include="*.js" --include="*.jsx" | grep -oP "['\"](/[^'\"]+)['\"]" | sort -u
```

Cross-reference every path found against this verified backend route table:

```
INLINE (main.py):
  GET  /health                                    (unprotected)
  GET  /v1/auth/ping
  GET  /v1/strategies
  GET  /v1/pipeline/stop_policy_template
  GET  /v1/markets
  GET  /v1/universe
  GET  /v1/timeframes
  GET  /v1/ohlcv
  GET  /v1/squeeze/state
  GET  /v1/btc/squeeze-status
  POST /v1/signals/queue
  GET  /v1/signals/top
  GET  /v1/signals/{signal_id}
  GET  /v1/opportunities
  GET  /v1/opportunities/csv
  GET  /v1/opportunities/live/{setup_id}/{symbol}/{tf}
  GET  /v1/opportunities/{opp_id}
  GET  /v1/opportunities/{opp_id}/price-window
  GET  /v1/snapshot/minute
  GET  /v1/d2/status

SESSION (routers/session.py):
  GET  /v1/session/bootstrap                     (unprotected)
  POST /v1/session                                (unprotected)
  DEL  /v1/session                                (unprotected)

RUNS (routers/runs.py, prefix=/runs):
  GET  /v1/runs
  GET  /v1/runs/latest
  GET  /v1/runs/{run_id}
  GET  /v1/runs/{run_id}/trades
  GET  /v1/runs/{run_id}/events
  GET  /v1/runs/{run_id}/simulation-results
  GET  /v1/runs/{run_id}/simulation-results/csv
  GET  /v1/runs/fingerprint/{fingerprint}

CONTROL (routers/control.py, prefix=/control):
  POST /v1/control/runs/scan
  POST /v1/control/runs/backtest
  POST /v1/control/runs/simulation
  POST /v1/control/runs/paper/start
  POST /v1/control/runs/paper/stop
  POST /v1/control/runs/{run_id}/cancel
  GET  /v1/control/runs/queue
  GET  /v1/control/paper/status
  GET  /v1/control/data/availability              (NEW)

TRADING (routers/trading.py, prefix=/trading):
  GET  /v1/trading/account
  GET  /v1/trading/positions
  GET  /v1/trading/positions/{position_id}
  GET  /v1/trading/trades
  GET  /v1/trading/performance
  POST /v1/trading/positions/{position_id}/close

ANALYTICS (routers/analytics.py, prefix=/analytics):
  POST /v1/analytics/correlation
  GET  /v1/analytics/correlation/{analysis_id}
  GET  /v1/analytics/correlation
  POST /v1/analytics/insights
  PUT  /v1/analytics/insights/{insight_id}
  GET  /v1/analytics/insights
  GET  /v1/analytics/insights/{insight_id}

TELEMETRY (routers/telemetry.py):
  POST /v1/telemetry/frontend-logs               (unprotected)

META / PIPELINE (routers/meta.py, prefix=/meta):
  GET  /v1/meta/topology
  GET  /v1/meta/node/{node_id}/versions
  GET  /v1/meta/node/{node_id}/version/{version_id}
  GET  /v1/meta/stop_policy/audit
  GET  /v1/meta/experiments
  GET  /v1/meta/experiment/{experiment_id}
  GET  /v1/meta/compositions
  GET  /v1/meta/composition/{composition_id}
  GET  /v1/meta/search/versions
  GET  /v1/meta/compare/versions/{a}/{b}
  GET  /v1/meta/compare/compositions/{a}/{b}
  POST /v1/meta/sweep/register
  POST /v1/meta/sweep/results
  POST /v1/meta/experiment
  POST /v1/meta/node/version
  GET  /v1/meta/version/{version_id}/events
  POST /v1/meta/version/{version_id}/event
  GET  /v1/meta/event/{event_id}/causal_chain
  GET  /v1/meta/events/recent
  PATCH /v1/meta/experiment/{experiment_id}
  POST /v1/meta/experiment/{experiment_id}/update
  POST /v1/meta/composition
  POST /v1/meta/composition/{composition_id}/deploy
  GET  /v1/meta/composition/{composition_id}/significance
  PUT  /v1/meta/node/{node_id}/version/{version_id}/staleness
  PUT  /v1/meta/node/{node_id}/version/{version_id}/health
  PUT  /v1/meta/node/{node_id}/version/{version_id}/output-hash
  GET  /v1/meta/drift
  POST /v1/meta/node/{node_id}/version/{version_id}/propagate-staleness
  GET  /v1/meta/pipelines
  POST /v1/meta/pipeline
  GET  /v1/meta/node/{node_id}/pareto_frontier
  PATCH /v1/meta/node_version/{version_id}/pareto_status
  POST /v1/meta/composition/constraint
  GET  /v1/meta/composition/space/estimate
  POST /v1/meta/composition/{composition_id}/sync-waves
  GET  /v1/meta/events/search
  POST /v1/meta/composition/draft
  PUT  /v1/meta/composition/{composition_id}/draft
  GET  /v1/meta/node/{node_id}/dependency-audit
  GET  /v1/meta/version/{version_id}/reachability
  POST /v1/meta/datasets
  GET  /v1/meta/datasets
  GET  /v1/meta/openlineage/events
  GET  /v1/meta/provenance/search

UNICORNS (routers/unicorns.py, prefix=/unicorns):
  GET  /v1/unicorns/embryo-active
  GET  /v1/unicorns/pipeline-status
  GET  /v1/unicorns/{symbol}

CC (routers/cc.py, prefix=/cc):
  POST /v1/cc/variant
  POST /v1/cc/challenger/from/{champion_variant_id}
  POST /v1/cc/run
  POST /v1/cc/run/{run_id}/close
  POST /v1/cc/run/{run_id}/promote
  POST /v1/cc/run/{run_id}/cancel
  GET  /v1/cc/champions
  GET  /v1/cc/variants
  GET  /v1/cc/variant/{variant_id}
  GET  /v1/cc/runs
  GET  /v1/cc/run/{run_id}
  GET  /v1/cc/run/{run_id}/metrics
  GET  /v1/cc/run/{run_id}/assets
  GET  /v1/cc/run/{run_id}/trades

EMERGENCY (routers/emergency.py, prefix=/emergency):
  POST /v1/emergency/kill
  POST /v1/emergency/pause
  POST /v1/emergency/resume
  GET  /v1/emergency/status

EQUITIES (routers/equities.py, prefix=/equities):  [optional]
  GET  /v1/equities/opportunities
  GET  /v1/equities/opportunities/{opp_id}
  GET  /v1/equities/opportunities/{opp_id}/explain
  GET  /v1/equities/dashboard/performance
  GET  /v1/equities/dashboard/realized
  GET  /v1/equities/dashboard/policy
  GET  /v1/equities/pipeline/health
  POST /v1/equities/simulation
```

**Report format:**
```
FRONTEND PATH → BACKEND MATCH
/v1/strategies → ✓
/v1/some/broken/path → ✗ (no backend route)
```

Any path marked ✗ gets reported to Opus. Do NOT attempt to fix or create endpoints.

---

## Task 4: Confirm page-level wiring

For each of these pages, open the file, check what hooks/API calls it makes, and confirm they're all wired to real endpoints:

1. **Dashboard** (`src/pages/Dashboard.jsx` or similar) — what data does it fetch?
2. **ControlPlane** — confirm it uses useQueueStatus (now fixed) and useDataAvailability (now wired)
3. **RunCompare** — does it call a dedicated compare endpoint or compose from useRun calls?
4. **PaperTrading** — confirm it uses usePaperStatus, useTradingAccount, usePositions
5. **Pipeline** — confirm it calls /v1/meta/topology and related meta endpoints
6. **CorrelationAnalysis** — confirm it calls /v1/analytics/correlation endpoints
7. **SimulationResults** — confirm it calls /v1/runs/{id}/simulation-results

**Report format per page:**
```
Dashboard:
  - useStrategies → /v1/strategies ✓
  - useOpportunities → /v1/opportunities ✓
  - (list all hooks/calls)
```

---

## Task 5: Build verification

After all fixes:

```bash
cd /path/to/Frontend
npm run build 2>&1 | tail -20
npm run lint 2>&1 | tail -10
```

Report both outputs in full.

---

## Task 6: Frontend Governance Audit

The backend is registry-driven: every threshold, timeframe list, and operational parameter traces back to `get_config()` or a governance module constant. The frontend must mirror this — no magic numbers, no feral constants.

Audit the following and report findings (do NOT fix — report to Opus):

### 6a. Polling intervals
```bash
grep -rn "interval\|POLL\|poll\|15000\|5000\|30000\|refetchInterval" src/hooks/ --include="*.js" --include="*.jsx"
```
Are polling intervals (15s for opportunities, 5s for positions, 30s for signals) governed by a config object or hardcoded in each hook? If hardcoded, list every instance with file:line and the hardcoded value.

### 6b. Feature flags
```bash
cat src/config/pipelineFeatures.js
```
Is this a flat object of booleans, or does it resolve from a registry/config endpoint? If it's a static object, report its full contents.

### 6c. Magic numbers in pages/components
```bash
grep -rn "[0-9]\{3,\}" src/pages/ src/components/ --include="*.js" --include="*.jsx" | grep -v "node_modules\|\.map\|console\|import\|from\|test\|spec" | head -40
```
Report any constants that look like thresholds, limits, or operational parameters (not CSS values, not array indices).

### 6d. localStorage keys
```bash
grep -rn "localStorage\.\|sessionStorage\." src/ --include="*.js" --include="*.jsx" | grep -v node_modules | head -20
```
List every localStorage/sessionStorage key. Are they governed by a constants file or scattered as string literals?

### 6e. API timeout/retry config
```bash
grep -rn "timeout\|retry\|TIMEOUT\|RETRY\|backoff\|BACKOFF" src/api/ --include="*.js"
```
Are request timeouts and retry policies centralised or per-file?

**Report format:**
```
GOVERNANCE AUDIT
================
Polling intervals: HARDCODED / GOVERNED (details...)
Feature flags: STATIC_OBJECT / REGISTRY_DRIVEN (details...)
Magic numbers: (list)
localStorage keys: SCATTERED / CENTRALISED (details...)
Timeout/retry: HARDCODED / GOVERNED (details...)
```

---

## Output

After completing all tasks:
1. Files changed with exact diffs
2. Task 3 full cross-reference results
3. Task 4 page wiring report
4. Build + lint output
5. Task 6 governance audit report (raw findings, no fixes)
