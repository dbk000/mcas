/**
 * Frontend Governance Kernel
 * ==========================
 *
 * Single source of truth for all operational parameters.
 * Values are delivered by the backend via GET /v1/session/bootstrap -> frontend_config.
 *
 * Rules:
 * 1. NEVER hardcode polling intervals, thresholds, limits, or timeframe maps
 *    outside this file.
 * 2. If frontend_config is missing (old backend), defaults are used but a
 *    warning is logged -- the drift is visible, not silent.
 * 3. If you need a new governed value, add it to the backend bootstrap payload
 *    first, then expose it here. The backend is the registry.
 */

let _config = null
let _warned = false

/**
 * Called by SessionProvider after bootstrap resolves.
 * This is the frontend's bootstrap_genesis().
 */
export function setFrontendConfig(config) {
  _config = config || null
  if (!_config && !_warned) {
    _warned = true
    console.warn(
      '[governed] frontend_config missing from bootstrap response. ' +
      'Using hardcoded defaults. Backend may need updating.'
    )
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
    public_feed_ms: 16_000,
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
    // Wave 1: Graph Readability (all disabled)
    pipeline_lod: false,
    pipeline_keyboard_shortcuts: false,
    pipeline_tv_scaling: false,
    pipeline_web_worker_layout: false,
    pipeline_collapsible_groups: false,
    pipeline_panel_aware_fit_view: false,
    pipeline_complexity_check: false,
    pipeline_edge_culling: false,
    pipeline_url_state: false,
    // Wave 2: Timeline & Biography (all enabled)
    pipeline_event_tree: true,
    pipeline_progressive_disclosure: true,
    pipeline_critical_path: true,
    pipeline_event_adjusters: true,
    pipeline_time_gap_viz: true,
    pipeline_table_tree_toggle: true,
    pipeline_temporal_slider: true,
    pipeline_before_after_state: true,
    pipeline_configurable_facets: true,
    pipeline_slicing: true,
    pipeline_recent_activity: true,
    // Wave 3: Dependency Hardening (all enabled)
    pipeline_staleness: true,
    pipeline_output_hash: true,
    pipeline_drift_detection: true,
    pipeline_diff: true,
    pipeline_tiered_confirmation: true,
    // Wave 4: Advanced Interaction (all enabled)
    pipeline_lineage_depth: true,
    pipeline_search: true,
    pipeline_focus_mode: true,
    pipeline_split_panel: true,
    pipeline_composition_draft: true,
    // Wave 5: Interoperability (all enabled)
    pipeline_openlineage: true,
    pipeline_provenance_search: true,
  },
}

/**
 * Feature flag key mapping: frontend camelCase -> backend snake_case.
 * This is the single translation table. pipelineFeatures.js re-exports
 * using camelCase getters that call getFeatureFlag() with the backend key.
 */
const FEATURE_KEY_MAP = {
  // Wave 1
  lodTiers: 'pipeline_lod',
  keyboardShortcuts: 'pipeline_keyboard_shortcuts',
  tvTextScaling: 'pipeline_tv_scaling',
  webWorkerLayout: 'pipeline_web_worker_layout',
  collapsibleGroups: 'pipeline_collapsible_groups',
  panelAwareFitView: 'pipeline_panel_aware_fit_view',
  complexityCheck: 'pipeline_complexity_check',
  edgeCulling: 'pipeline_edge_culling',
  urlState: 'pipeline_url_state',
  // Wave 2
  eventTreeView: 'pipeline_event_tree',
  progressiveDisclosure: 'pipeline_progressive_disclosure',
  criticalPath: 'pipeline_critical_path',
  eventAdjusters: 'pipeline_event_adjusters',
  timeGapViz: 'pipeline_time_gap_viz',
  tableTreeToggle: 'pipeline_table_tree_toggle',
  temporalSlider: 'pipeline_temporal_slider',
  beforeAfterState: 'pipeline_before_after_state',
  configurableFacets: 'pipeline_configurable_facets',
  pipelineSlicing: 'pipeline_slicing',
  recentActivity: 'pipeline_recent_activity',
  // Wave 3
  threeStateStaleness: 'pipeline_staleness',
  outputHash: 'pipeline_output_hash',
  driftDetection: 'pipeline_drift_detection',
  diffVisualization: 'pipeline_diff',
  tieredConfirmation: 'pipeline_tiered_confirmation',
  // Wave 4
  lineageDepthSlider: 'pipeline_lineage_depth',
  searchFilter: 'pipeline_search',
  focusMode: 'pipeline_focus_mode',
  splitPanelSidebar: 'pipeline_split_panel',
  compositionDraftUI: 'pipeline_composition_draft',
  // Wave 5
  openLineageAdapter: 'pipeline_openlineage',
  provenanceSearch: 'pipeline_provenance_search',
}

function _get(path) {
  const keys = path.split('.')
  let val = _config
  for (const k of keys) {
    if (val == null) break
    val = val[k]
  }
  if (val !== undefined && val !== null) return val

  // Fallback to defaults
  let def = DEFAULTS
  for (const k of keys) {
    if (def == null) return undefined
    def = def[k]
  }
  return def
}

// --- Public accessors ---

export function getPollingInterval(key) {
  return _get(`polling.${key}`)
}

export function getStalenessThresholdS(timeframe) {
  const tfSec = _get('tf_seconds')?.[timeframe] ?? 60
  const mult = _get('staleness_multiplier') ?? 2.5
  return tfSec * mult
}

export function getSignalMaxAgeS() {
  return _get('signal_max_age_s')
}

export function getTimeframes() {
  return _get('timeframes')
}

export function getTfSeconds() {
  return _get('tf_seconds')
}

export function getPaginationLimit(key) {
  return _get(`pagination.${key}`)
}

export function getRequestConfig() {
  return {
    timeoutMs: _get('request.timeout_ms'),
    retryCount: _get('request.retry_count'),
    retryBackoffMs: _get('request.retry_backoff_ms'),
  }
}

/**
 * Get a feature flag by name.
 * Accepts either frontend camelCase (e.g. 'criticalPath') or
 * backend snake_case (e.g. 'pipeline_critical_path').
 * The FEATURE_KEY_MAP translates camelCase to the backend key.
 */
export function getFeatureFlag(name) {
  const backendKey = FEATURE_KEY_MAP[name] || name
  return _get(`features.${backendKey}`) ?? false
}

export function getAllFeatureFlags() {
  return _config?.features ?? DEFAULTS.features
}
