import { http, HttpResponse } from 'msw'
import strategyDashboardFixture from './fixtures/strategy-dashboard-demo.v1.json'
import metricsDemoFixture from './fixtures/metrics-demo.v1.json'
import glassBoxCasesFixture from './fixtures/glass-box-cases.v1.json'

// Helper to wrap data in standard envelope (Canon §1.5)
const envelope = (data, compute_ms = 5) => ({
  data,
  meta: {
    timestamp: new Date().toISOString(),
    as_of_ts: new Date().toISOString(),
    version: 'v1',
    compute_ms
  }
})

// Server-level config returned by bootstrap/session endpoints
const MOCK_FRONTEND_CONFIG = {
  polling: {
    opportunities_ms: 15000,
    positions_ms: 5000,
    trading_account_ms: 5000,
    paper_status_ms: 5000,
    ohlcv_ms: 5000,
    snapshot_ms: 5000,
    signals_top_ms: 30000,
    queue_status_ms: 15000,
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
    timeout_ms: 30000,
    retry_count: 2,
    retry_backoff_ms: 1000,
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
}

// Authored overrides for curated/premium/degraded states
let hasSession = false

const authoredOverrides = [
  http.get('*/v1/session/bootstrap', ({ request }) => {
    return HttpResponse.json(envelope({
      has_session: hasSession,
      has_app_access: hasSession,
      auth: hasSession ? 'ok' : 'missing',
      session_source: hasSession ? 'http_only_cookie' : 'none',
      entitlement_mode: 'preview_only',
      api_origin: new URL(request.url).origin,
      checked_at: new Date().toISOString(),
      frontend_config: MOCK_FRONTEND_CONFIG,
    }))
  }),

  http.post('*/v1/session', async ({ request }) => {
    const body = await request.json().catch(() => ({}))
    hasSession = Boolean(body?.api_key)

    return HttpResponse.json(envelope({
      has_session: hasSession,
      has_app_access: hasSession,
      auth: hasSession ? 'ok' : 'missing',
      session_source: hasSession ? 'http_only_cookie' : 'none',
      entitlement_mode: 'preview_only',
      api_origin: new URL(request.url).origin,
      checked_at: new Date().toISOString(),
      frontend_config: MOCK_FRONTEND_CONFIG,
    }), { status: hasSession ? 200 : 401 })
  }),

  http.delete('*/v1/session', ({ request }) => {
    hasSession = false

    return HttpResponse.json(envelope({
      has_session: false,
      has_app_access: false,
      auth: 'missing',
      session_source: 'none',
      entitlement_mode: 'preview_only',
      api_origin: new URL(request.url).origin,
      checked_at: new Date().toISOString(),
      frontend_config: MOCK_FRONTEND_CONFIG,
    }))
  }),

  http.get('*/v1/analytics/dashboard', ({ request }) => {
    const state = request.headers.get('X-Fixture-State') || 'curated'
    if (state === 'degraded') {
      return HttpResponse.json(envelope({
        leaderboard: [],
        alerts_tape: [],
        market_state: { regime: 'System Degraded', breadth_score: 0 }
      }))
    }
    if (state === 'premium') {
      return HttpResponse.json(envelope({
        ...strategyDashboardFixture,
        leaderboard: strategyDashboardFixture.leaderboard.map(l => ({ ...l, premium: true }))
      }))
    }
    return HttpResponse.json(envelope(strategyDashboardFixture))
  }),

  http.get('*/v1/analytics/metrics', ({ request }) => {
    const state = request.headers.get('X-Fixture-State') || 'curated'
    if (state === 'degraded') return HttpResponse.json(envelope({ trades: [], summary: {} }))
    return HttpResponse.json(envelope(metricsDemoFixture))
  }),

  http.get('*/v1/runs/trace/:id', ({ params, request }) => {
    const { id } = params
    const state = request.headers.get('X-Fixture-State') || 'curated'
    if (state === 'degraded') return new HttpResponse(null, { status: 404 })
    const trace = glassBoxCasesFixture.find(c => c.id === id) || glassBoxCasesFixture[0]
    return HttpResponse.json(envelope(trace))
  }),

  http.get('*/v1/control/data/availability', () => {
    return HttpResponse.json(envelope({
      universe: { known: 150, monitored: 100, with_ohlcv: 95 },
      timeframes: [
        {
          timeframe: '1h',
          symbol_count: 95,
          total_rows: 1200000,
          latest_bar: '2026-03-16T12:00:00+00:00',
          earliest_bar: '2025-01-01T00:00:00+00:00',
          freshest_ingestion: '2026-03-16T12:05:00+00:00',
          lag_seconds: 300
        },
        {
          timeframe: '4h',
          symbol_count: 95,
          total_rows: 300000,
          latest_bar: '2026-03-16T12:00:00+00:00',
          earliest_bar: '2025-01-01T00:00:00+00:00',
          freshest_ingestion: '2026-03-16T12:10:00+00:00',
          lag_seconds: 600
        },
        {
          timeframe: '1d',
          symbol_count: 95,
          total_rows: 50000,
          latest_bar: '2026-03-16T00:00:00+00:00',
          earliest_bar: '2025-01-01T00:00:00+00:00',
          freshest_ingestion: '2026-03-16T00:05:00+00:00',
          lag_seconds: 300
        }
      ],
      stale_symbols: [],
      stale_count: 0,
      healthy: true,
      governance: {
        staleness_multiplier: 2.5,
        tf_seconds: { '1m': 60, '5m': 300, '15m': 900, '1h': 3600, '4h': 14400, '1d': 86400 },
      },
    }))
  }),
]

/**
 * Initialize Hybrid Handlers (Workstream 2)
 * Dynamically imports generated handlers to avoid production bundle bloat.
 */
export async function initializeHandlers() {
  const { handlers: generatedHandlers } = await import('./generated/handlers')
  return [
    ...authoredOverrides,
    ...generatedHandlers
  ]
}
