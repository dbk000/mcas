import React, { Suspense, lazy, useEffect } from 'react'
import { Routes, Route, Navigate, Outlet, useLocation } from 'react-router-dom'
import { useApi } from './context/ApiContext'
import { getLogger, rotateCorrelationId } from './utils/logger'
import Layout from './components/Layout'
import MarketingLayout from './components/MarketingLayout'
import PaidRoute from './components/PaidRoute'
import DevHUD from './components/DevHUD'
import ThemeSwitcher from './components/ThemeSwitcher'

const log = getLogger('router')

function lazyPage(routeId, loadPage) {
  return lazy(async () => {
    const start = performance.now()

    try {
      const pageModule = await loadPage()
      log.info('Route module loaded', {
        event: 'route_module_loaded',
        route_id: routeId,
        duration_ms: Math.round(performance.now() - start),
      })
      return pageModule
    } catch (error) {
      log.error('Route module load failed', {
        event: 'route_module_load_failed',
        route_id: routeId,
        duration_ms: Math.round(performance.now() - start),
        error_message: error?.message || String(error),
      })
      throw error
    }
  })
}

const Landing = lazyPage('marketing:landing', () => import('./pages/Landing'))
const Winners = lazyPage('marketing:winners', () => import('./pages/Winners'))
const Pricing = lazyPage('marketing:pricing', () => import('./pages/Pricing'))
const Upgrade = lazyPage('marketing:upgrade', () => import('./pages/Upgrade'))
const CheckoutSuccess = lazyPage('marketing:checkout-success', () => import('./pages/CheckoutSuccess'))
const CheckoutCancel = lazyPage('marketing:checkout-cancel', () => import('./pages/CheckoutCancel'))
const Settings = lazyPage('marketing:settings', () => import('./pages/Settings'))

const Dashboard = lazyPage('app:dashboard', () => import('./pages/Dashboard'))
const StrategyDashboard = lazyPage('app:strategies', () => import('./pages/StrategyDashboard'))
const Opportunities = lazyPage('app:opportunities', () => import('./pages/Opportunities'))
const OpportunitySurvey = lazyPage('app:opportunity-survey', () => import('./pages/OpportunitySurvey'))
const OpportunityDetail = lazyPage('app:opportunity-detail', () => import('./pages/OpportunityDetail'))
const Markets = lazyPage('app:markets', () => import('./pages/Markets'))
const MarketDetail = lazyPage('app:market-detail', () => import('./pages/MarketDetail'))
const Runs = lazyPage('app:runs', () => import('./pages/Runs'))
const RunCompare = lazyPage('app:run-compare', () => import('./pages/RunCompare'))
const RunDetail = lazyPage('app:run-detail', () => import('./pages/RunDetail'))
const SimulationResults = lazyPage('app:simulation-results', () => import('./pages/SimulationResults'))
const ControlPlane = lazyPage('app:control', () => import('./pages/ControlPlane'))
const PaperTrading = lazyPage('app:paper', () => import('./pages/PaperTrading'))
const CorrelationAnalysis = lazyPage('app:analytics-correlations', () => import('./pages/CorrelationAnalysis'))
const Pipeline = lazyPage('app:pipeline', () => import('./pages/Pipeline'))

function RouteLogger() {
  const { pathname } = useLocation()

  useEffect(() => {
    rotateCorrelationId()
    log.info('Route change', { event: 'route_change', path: pathname })
  }, [pathname])

  return null
}

function ProtectedRoute({ children }) {
  const { config, session, sessionReady } = useApi()

  const settingsParams = new URLSearchParams({
    from: 'protected-route',
    returnTo: `${window.location.pathname}${window.location.search}`,
  })

  if (!config.baseUrl) {
    log.info('Auth redirect', { event: 'auth_redirect', from: window.location.pathname })
    return <Navigate to={`/settings?${settingsParams.toString()}`} replace />
  }

  if (!sessionReady) {
    return (
      <div className="m-6 flex min-h-[42vh] flex-col items-center justify-center gap-3 rounded-lg border border-terminal-700/80 bg-terminal-900/60 px-6 py-10 text-center">
        <div className="size-8 animate-spin rounded-full border-2 border-terminal-600 border-t-amber-400" />
        <div className="space-y-1">
          <p className="font-mono text-sm font-semibold uppercase tracking-[0.18em] text-terminal-100">
            Restoring session
          </p>
          <p className="text-sm text-terminal-400">
            Checking the backend bootstrap before the app shell loads.
          </p>
        </div>
      </div>
    )
  }

  if (!session.hasSession || session.auth !== 'ok') {
    log.info('Session redirect', {
      event: 'session_redirect',
      from: window.location.pathname,
      auth: session.auth,
    })
    return <Navigate to={`/settings?${settingsParams.toString()}`} replace />
  }

  return children
}

function PublicLayoutRoute() {
  return (
    <MarketingLayout>
      <Outlet />
    </MarketingLayout>
  )
}

function RouteFamilyFallback({ title, detail, appShell = false }) {
  return (
    <div
      className={[
        'flex flex-col items-center justify-center gap-3 rounded-lg border border-terminal-700/80 bg-terminal-900/60 px-6 py-10 text-center',
        appShell ? 'm-6 min-h-[42vh]' : 'mx-auto min-h-[36vh] max-w-3xl',
      ].join(' ')}
    >
      <div className="size-8 animate-spin rounded-full border-2 border-terminal-600 border-t-amber-400" />
      <div className="space-y-1">
        <p className="font-mono text-sm font-semibold uppercase tracking-[0.18em] text-terminal-100">
          {title}
        </p>
        <p className="text-sm text-terminal-400">{detail}</p>
      </div>
    </div>
  )
}

function RouteFamilyBoundary({ title, detail, appShell = false }) {
  return (
    <Suspense fallback={<RouteFamilyFallback title={title} detail={detail} appShell={appShell} />}>
      <Outlet />
    </Suspense>
  )
}

function AppShell() {
  return (
    <PaidRoute>
      <ProtectedRoute>
        <Layout>
          <Outlet />
        </Layout>
      </ProtectedRoute>
    </PaidRoute>
  )
}

class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, info) {
    const boundaryLog = getLogger('router')
    boundaryLog.error('React error boundary', {
      event: 'react_error_boundary',
      error_message: error.message,
      component_stack: info.componentStack?.slice(0, 500),
    })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: '#0d1117',
            color: '#e6edf3',
            fontFamily: 'monospace',
            padding: '2rem',
          }}
        >
          <div style={{ maxWidth: '480px', textAlign: 'center' }}>
            <h1 style={{ color: '#f59e0b', fontSize: '1.5rem', marginBottom: '1rem' }}>
              Something went wrong
            </h1>
            <p style={{ color: '#8b949e', marginBottom: '0.5rem' }}>
              {this.state.error?.message || 'An unexpected error occurred'}
            </p>
            <p style={{ color: '#484f58', fontSize: '0.75rem', marginBottom: '1.5rem' }}>
              Check browser console or run window.__mcas.dumpLogs() for details.
            </p>
            <button
              onClick={() => window.location.reload()}
              style={{
                padding: '0.5rem 1.5rem',
                backgroundColor: '#f59e0b',
                color: '#0d1117',
                border: 'none',
                borderRadius: '0.375rem',
                fontWeight: 600,
                cursor: 'pointer',
              }}
            >
              Reload
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}

export default function App() {
  return (
    <ErrorBoundary>
      <DevHUD />
      <ThemeSwitcher />
      <RouteLogger />
      <Routes>
        <Route element={<PublicLayoutRoute />}>
          <Route
            element={(
              <RouteFamilyBoundary
                title="Loading public route"
                detail="Fetching the marketing surface for this destination."
              />
            )}
          >
            <Route path="/" element={<Landing />} />
            <Route path="/winners" element={<Winners />} />
            <Route path="/pricing" element={<Pricing />} />
            <Route path="/upgrade" element={<Upgrade />} />
            <Route path="/checkout/success" element={<CheckoutSuccess />} />
            <Route path="/checkout/cancel" element={<CheckoutCancel />} />
            <Route path="/settings" element={<Settings />} />
          </Route>
        </Route>

        <Route path="/app" element={<AppShell />}>
          <Route
            element={(
              <RouteFamilyBoundary
                appShell
                title="Loading dashboard"
                detail="Preparing the operator summary surfaces."
              />
            )}
          >
            <Route index element={<Dashboard />} />
            <Route path="strategies" element={<StrategyDashboard />} />
          </Route>
          <Route
            element={(
              <RouteFamilyBoundary
                appShell
                title="Loading opportunities"
                detail="Fetching the opportunity analysis surface."
              />
            )}
          >
            <Route path="opportunities" element={<Opportunities />} />
            <Route path="opportunities/survey" element={<OpportunitySurvey />} />
            <Route path="opportunities/:id" element={<OpportunityDetail />} />
          </Route>
          <Route
            element={(
              <RouteFamilyBoundary
                appShell
                title="Loading markets"
                detail="Preparing market detail tools and charting assets."
              />
            )}
          >
            <Route path="markets" element={<Markets />} />
            <Route path="markets/:symbol" element={<MarketDetail />} />
          </Route>
          <Route
            element={(
              <RouteFamilyBoundary
                appShell
                title="Loading runs"
                detail="Fetching run history and simulation comparisons."
              />
            )}
          >
            <Route path="runs" element={<Runs />} />
            <Route path="runs/compare" element={<RunCompare />} />
            <Route path="runs/:id" element={<RunDetail />} />
            <Route path="runs/:id/simulation" element={<SimulationResults />} />
          </Route>
          <Route
            element={(
              <RouteFamilyBoundary
                appShell
                title="Loading operator surface"
                detail="Fetching advanced analysis and control tools."
              />
            )}
          >
            <Route path="control" element={<ControlPlane />} />
            <Route path="paper" element={<PaperTrading />} />
            <Route path="analytics/correlations" element={<CorrelationAnalysis />} />
            <Route path="pipeline" element={<Pipeline />} />
          </Route>
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </ErrorBoundary>
  )
}
