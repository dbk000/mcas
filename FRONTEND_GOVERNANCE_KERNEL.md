# Frontend Governance Kernel: Design + Implementation

## Architecture

```
BACKEND (bootstrap_genesis → get_config → registry)
    │
    ├── GET /v1/session/bootstrap   ← ALREADY called on mount
    │   └── frontend_config: { ... }  ← NEW: governed values delivered here
    │
FRONTEND (SessionProvider → context → hooks/pages)
    │
    ├── SessionContext.frontend_config  ← NEW: raw config from backend
    │
    ├── src/config/governed.js         ← NEW: typed accessor with defaults
    │   ├── getPollingInterval(key)
    │   ├── getStalenessThreshold(tf)
    │   ├── getFeatureFlag(name)
    │   └── getPaginationLimit(key)
    │
    └── hooks/pages import from governed.js, NEVER hardcode
```

Eager resolution: if bootstrap fails, the app already redirects to /settings (broken connection).
If frontend_config is missing (old backend), governed.js returns hardcoded defaults — but logs
a warning so the drift is visible, not silent.

---

## Part 1: Backend — Extend bootstrap payload

### Backend housekeeping (while you're in there)

**File: api/app/main.py, line 103:**
```python
SIGNAL_MAX_AGE_S = 3600  # ← GOVERNANCE VIOLATION: duplicates cache_contract.SIGNAL_MAX_AGE_S
```
Replace with:
```python
from governance.cache_contract import SIGNAL_MAX_AGE_S  # Already imported in cache_contract
```
This value is used on lines 394 and 935 for opportunity max_age. It must track the
governance kernel, not be a feral copy.

### Deploy on VPS Server
### cd /home/darren/Documents/MCAS/Backend

### File: api/app/routers/session.py

Add import at top (after existing governance.bootstrap import):

```python
from governance.cache_contract import TF_SECONDS, STALENESS_MULTIPLIER, SIGNAL_MAX_AGE_S
```

Then replace `_build_bootstrap_payload` with:

```python
def _build_bootstrap_payload(request: Request, *, authenticated: bool) -> dict:
    cfg = get_config().Worker
    timeframes = cfg.DEFAULT_TIMEFRAMES.split(",")

    return {
        # --- Existing auth state (unchanged) ---
        "has_session": authenticated,
        "has_app_access": authenticated,
        "auth": "ok" if authenticated else "missing",
        "session_source": "http_only_cookie" if authenticated else "none",
        "entitlement_mode": "preview_only",
        "api_origin": _get_api_origin(request),
        "checked_at": datetime.now(timezone.utc).isoformat(),

        # --- Frontend governance config (NEW) ---
        # Every value traces to the governance kernel.
        # Frontend must read from here, never hardcode.
        "frontend_config": {
            # Polling intervals (ms) — backend-governed cadence
            "polling": {
                "opportunities_ms": 15_000,
                "positions_ms": 5_000,
                "trading_account_ms": 5_000,
                "paper_status_ms": 5_000,
                "ohlcv_ms": 5_000,
                "snapshot_ms": 5_000,
                "signals_top_ms": 30_000,
                "queue_status_ms": 15_000,
            },

            # Timeframes — from DEFAULT_TIMEFRAMES registry
            "timeframes": timeframes,
            "tf_seconds": {tf: TF_SECONDS.get(tf, 60) for tf in timeframes},

            # Staleness — from governance.cache_contract
            "staleness_multiplier": STALENESS_MULTIPLIER,
            "signal_max_age_s": SIGNAL_MAX_AGE_S,

            # Pagination — backend max limits mirrored for UI
            "pagination": {
                "opportunities_default": 50,
                "opportunities_max": 500,
                "runs_default": 50,
                "runs_max": 200,
                "trades_default": 50,
                "trades_max": 500,
                "simulation_results_default": 100,
                "simulation_results_max": 500,
            },

            # Request resilience
            "request": {
                "timeout_ms": 30_000,
                "retry_count": 2,
                "retry_backoff_ms": 1_000,
            },

            # Pipeline feature flags — delivered from backend so they
            # can differ per subscriber tier without a frontend deploy.
            # Wave 1 = usability foundations, Wave 2-5 = advanced features.
            "features": {
                "pipeline_lod": False,
                "pipeline_keyboard_shortcuts": False,
                "pipeline_tv_scaling": False,
                "pipeline_web_worker_layout": False,
                "pipeline_collapsible_groups": False,
                "pipeline_event_tree": True,
                "pipeline_progressive_disclosure": True,
                "pipeline_critical_path": True,
                "pipeline_slicing": True,
                "pipeline_recent_activity": True,
                "pipeline_staleness": True,
                "pipeline_drift_detection": True,
                "pipeline_diff": True,
                "pipeline_tiered_confirmation": True,
                "pipeline_lineage_depth": True,
                "pipeline_search": True,
                "pipeline_focus_mode": True,
                "pipeline_split_panel": True,
                "pipeline_composition_draft": True,
                "pipeline_openlineage": True,
                "pipeline_provenance_search": True,
            },
        },
    }
```

### Governance self-audit:
- ✅ `timeframes` → `get_config().Worker.DEFAULT_TIMEFRAMES` (registry)
- ✅ `tf_seconds` → `TF_SECONDS` from `governance.cache_contract` (registry)
- ✅ `staleness_multiplier` → `STALENESS_MULTIPLIER` from `governance.cache_contract` (registry)
- ✅ `signal_max_age_s` → `SIGNAL_MAX_AGE_S` from `governance.cache_contract` (registry)
- ⚠️ `polling`, `pagination`, `request` values — centralised here (single source)
  but not yet env-var governed in get_config(). Strictly better than 12+ scattered
  files. To fully comply, these should be promoted to env vars in a follow-up:
    FRONTEND_POLL_OPPORTUNITIES_MS=15000
    FRONTEND_POLL_POSITIONS_MS=5000
    ...etc
  For now, one place to change them > zero places to find them.
- ✅ `features` → 32 pipeline flags, currently static. Delivered from backend so
  they can later differ per subscriber tier without a frontend deploy.

---

## Part 2: Frontend — Config kernel

### File: src/config/governed.js (NEW)

```javascript
/**
 * Frontend Governance Kernel
 * ==========================
 *
 * Single source of truth for all operational parameters.
 * Values are delivered by the backend via GET /v1/session/bootstrap → frontend_config.
 *
 * Rules:
 * 1. NEVER hardcode polling intervals, thresholds, limits, or timeframe maps
 *    outside this file.
 * 2. If frontend_config is missing (old backend), defaults are used but a
 *    warning is logged — the drift is visible, not silent.
 * 3. If you need a new governed value, add it to the backend bootstrap payload
 *    first, then expose it here. The backend is the registry.
 */

let _config = null;
let _warned = false;

/**
 * Called by SessionProvider after bootstrap resolves.
 * This is the frontend's bootstrap_genesis().
 */
export function setFrontendConfig(config) {
  _config = config || null;
  if (!_config && !_warned) {
    _warned = true;
    console.warn(
      '[governed] frontend_config missing from bootstrap response. ' +
      'Using hardcoded defaults. Backend may need updating.'
    );
  }
}

// --- Defaults (used ONLY when backend doesn't deliver frontend_config) ---
const DEFAULTS = {
  polling: {
    opportunities_ms: 15_000,
    positions_ms: 5_000,
    trading_account_ms: 5_000,
    paper_status_ms: 5_000,
    ohlcv_ms: 5_000,
    snapshot_ms: 5_000,
    signals_top_ms: 30_000,
    queue_status_ms: 15_000,
  },
  timeframes: ['1m', '5m', '15m', '1h', '4h', '1d'],
  tf_seconds: { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 },
  staleness_multiplier: 2.5,
  signal_max_age_s: 3600,
  pagination: {
    opportunities_default: 50,
    opportunities_max: 500,
    runs_default: 50,
    runs_max: 200,
    trades_default: 50,
    trades_max: 500,
    simulation_results_default: 100,
    simulation_results_max: 500,
  },
  request: {
    timeout_ms: 30_000,
    retry_count: 2,
    retry_backoff_ms: 1_000,
  },
  features: {
    pipeline_lod: false,
    pipeline_keyboard_shortcuts: false,
    pipeline_tv_scaling: false,
    pipeline_web_worker_layout: false,
    pipeline_collapsible_groups: false,
    pipeline_event_tree: true,
    pipeline_progressive_disclosure: true,
    pipeline_critical_path: true,
    pipeline_slicing: true,
    pipeline_recent_activity: true,
    pipeline_staleness: true,
    pipeline_drift_detection: true,
    pipeline_diff: true,
    pipeline_tiered_confirmation: true,
    pipeline_lineage_depth: true,
    pipeline_search: true,
    pipeline_focus_mode: true,
    pipeline_split_panel: true,
    pipeline_composition_draft: true,
    pipeline_openlineage: true,
    pipeline_provenance_search: true,
  },
};

function _get(path) {
  const keys = path.split('.');
  let val = _config;
  for (const k of keys) {
    if (val == null) break;
    val = val[k];
  }
  if (val !== undefined && val !== null) return val;

  // Fallback to defaults
  let def = DEFAULTS;
  for (const k of keys) {
    if (def == null) return undefined;
    def = def[k];
  }
  return def;
}

// --- Public accessors ---

export function getPollingInterval(key) {
  return _get(`polling.${key}`);
}

export function getStalenessThresholdS(timeframe) {
  const tfSec = _get('tf_seconds')?.[timeframe] ?? 60;
  const mult = _get('staleness_multiplier') ?? 2.5;
  return tfSec * mult;
}

export function getSignalMaxAgeS() {
  return _get('signal_max_age_s');
}

export function getTimeframes() {
  return _get('timeframes');
}

export function getTfSeconds() {
  return _get('tf_seconds');
}

export function getPaginationLimit(key) {
  return _get(`pagination.${key}`);
}

export function getRequestConfig() {
  return {
    timeoutMs: _get('request.timeout_ms'),
    retryCount: _get('request.retry_count'),
    retryBackoffMs: _get('request.retry_backoff_ms'),
  };
}

export function getFeatureFlag(name) {
  return _get(`features.${name}`) ?? false;
}

export function getAllFeatureFlags() {
  return _config?.features ?? DEFAULTS.features;
}
```

---

## Part 3: CC Taskboard — Wire it through

This is the CC prompt to execute the frontend wiring.

---

# CC Task: Wire Frontend Governance Kernel

## Context

The backend bootstrap endpoint now delivers a `frontend_config` block with all governed
operational parameters. A new `src/config/governed.js` module has been created as the
frontend's single source of truth. Your job: wire it through so nothing is hardcoded anymore.

## Rules

- Every change MUST import from `src/config/governed.js`. No inline constants.
- Do NOT change any values — only change WHERE they're read from.
- Run `npm run build` after each task group. Bundle must stay under 1110 KB.
- Do NOT add new dependencies.

---

### Task 1: Hook up SessionProvider → governed.js

**File:** `src/context/SessionContext.jsx`

After the bootstrap response is received and parsed, call:
```javascript
import { setFrontendConfig } from '../config/governed';

// Inside the bootstrap success handler, after setting session state:
setFrontendConfig(bootstrapData.frontend_config);
```

This is the frontend's `bootstrap_genesis()` call. It must happen before any hook renders.

**Verify:** Add a temporary `console.log('[governed] config loaded:', bootstrapData.frontend_config)` and check it fires on page load. Remove the log after verifying.

---

### Task 2: Replace hardcoded polling intervals

**File:** `src/hooks/useData.js`

Find every hook that passes a hardcoded interval (in ms) to usePolling or setInterval. Replace with:

```javascript
import { getPollingInterval } from '../config/governed';

// BEFORE:
// usePolling('/v1/opportunities', { interval: 15000 })
// AFTER:
usePolling('/v1/opportunities', { interval: getPollingInterval('opportunities_ms') })
```

Full mapping:
```
15000 in useOpportunities     → getPollingInterval('opportunities_ms')
5000  in usePositions          → getPollingInterval('positions_ms')
5000  in useTradingAccount     → getPollingInterval('trading_account_ms')
5000  in usePaperStatus        → getPollingInterval('paper_status_ms')
5000  in useLiveOHLCV          → getPollingInterval('ohlcv_ms')
5000  in useSnapshot           → getPollingInterval('snapshot_ms')
30000 in useTopSignals         → getPollingInterval('signals_top_ms')
15000 in useQueueStatus        → getPollingInterval('queue_status_ms')
```

Also check pages that have their own polling (CC flagged 4 page files with intervals).
Apply the same pattern.

**Verify:**
```bash
grep -rn "[0-9]\{4,5\}" src/hooks/useData.js | grep -v "import\|from\|//"
# Should show zero raw millisecond numbers
```

---

### Task 3: Replace hardcoded pagination limits

Search for hardcoded limit values in API calls:

```bash
grep -rn "limit.*50\|limit.*100\|limit.*500\|limit.*10000" src/ --include="*.js" --include="*.jsx" | grep -v node_modules | grep -v governed.js
```

Replace with `getPaginationLimit('xxx_default')` or `getPaginationLimit('xxx_max')` as appropriate.

---

### Task 4: Centralise localStorage keys

**File:** Create `src/config/storageKeys.js` (NEW):

```javascript
/**
 * All localStorage/sessionStorage keys.
 * Single source of truth — no string literals at point of use.
 */
export const STORAGE_KEYS = {
  API_BASE_URL: 'mcas_api_base_url',
  ACCESS_TIER: 'mcas_local_access_tier',
  LOG_LEVEL: 'mcas_log_level',
  // Add all 9 keys CC found in the audit
};
```

Then find-replace every `localStorage.getItem('mcas_...')` and `localStorage.setItem('mcas_...')` call to import from storageKeys.js.

**Verify:**
```bash
grep -rn "localStorage\.\|sessionStorage\." src/ --include="*.js" --include="*.jsx" | grep -v node_modules | grep -v storageKeys.js
# Every hit should reference STORAGE_KEYS.xxx, never a string literal
```

---

### Task 5: Replace magic TF→ms maps

If any page or component has its own timeframe-to-seconds or timeframe-to-milliseconds map, replace with:

```javascript
import { getTfSeconds } from '../config/governed';
const tfSeconds = getTfSeconds();
```

---

### Task 5b: Replace pipelineFeatures.js with governed accessor

**File:** `src/config/pipelineFeatures.js`

Replace the entire static object with:

```javascript
import { getAllFeatureFlags, getFeatureFlag } from './governed';

// Re-export individual flags for backwards compatibility
// These now resolve from the backend bootstrap config.
export const FEATURES = new Proxy({}, {
  get(_, prop) {
    return getFeatureFlag(prop);
  }
});

// For code that imports the whole object
export default FEATURES;
```

OR, if Proxy is too clever and breaks existing code, the simpler version:

```javascript
import { getAllFeatureFlags } from './governed';

// Called lazily on first access — by which time bootstrap has resolved.
let _cached = null;
function features() {
  if (!_cached) _cached = getAllFeatureFlags();
  return _cached;
}

// Export individual named flags for existing imports
export const pipeline_lod = () => features().pipeline_lod;
export const pipeline_keyboard_shortcuts = () => features().pipeline_keyboard_shortcuts;
// ... (match whatever the current export shape is)
```

Check the current export shape first and match it. The goal: every feature flag
check reads from `governed.js` → backend config, not a static object.

**Verify:**
```bash
cat src/config/pipelineFeatures.js
# Should import from governed.js, not contain hardcoded booleans
```

---

### Task 6: Build + audit

```bash
npm run build 2>&1 | tail -20
npm run lint 2>&1 | tail -10

# Confirm no residual hardcoded intervals:
grep -rn "15000\|30000\|5000" src/hooks/ --include="*.js" | grep -v node_modules | grep -v governed.js

# Confirm no residual localStorage string literals:
grep -rn "'mcas_" src/ --include="*.js" --include="*.jsx" | grep -v storageKeys.js | grep -v node_modules

# Confirm pipelineFeatures.js no longer contains hardcoded booleans:
grep -c "true\|false" src/config/pipelineFeatures.js
# Should be 0 or very low (only in fallback logic)

# Confirm governed.js is imported where expected:
grep -rn "governed" src/ --include="*.js" --include="*.jsx" | grep -v node_modules
```

Report all output.

---

## Output

1. Files changed with exact diffs
2. Build + lint results
3. Residual audit results (any remaining hardcoded values)
4. Import graph showing which files now depend on governed.js
