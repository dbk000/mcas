import React from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import { useApi } from './context/ApiContext'
import { getLogger, rotateCorrelationId } from './utils/logger'
import Layout from './components/Layout'

// Pages
import Dashboard from './pages/Dashboard'
import Opportunities from './pages/Opportunities'
import OpportunitySurvey from './pages/OpportunitySurvey'
import OpportunityDetail from './pages/OpportunityDetail'
import Markets from './pages/Markets'
import MarketDetail from './pages/MarketDetail'
import Runs from './pages/Runs'
import RunDetail from './pages/RunDetail'
import RunCompare from './pages/RunCompare'
import ControlPlane from './pages/ControlPlane'
import PaperTrading from './pages/PaperTrading'
import SimulationResults from './pages/SimulationResults'
import CorrelationAnalysis from './pages/CorrelationAnalysis'
import Settings from './pages/Settings'
import Pipeline from './pages/Pipeline'

const log = getLogger('router')

function RouteLogger() {
  const { pathname } = useLocation()

  useEffect(() => {
    rotateCorrelationId()
    log.info('Route change', { event: 'route_change', path: pathname })
  }, [pathname])

  return null
}

function ProtectedRoute({ children }) {
  const { isConnected, config } = useApi()

  // If no config at all (both baseUrl and apiKey required), redirect to settings
  if (!config.baseUrl || !config.apiKey) {
    log.info('Auth redirect', { event: 'auth_redirect', from: window.location.pathname })
    return <Navigate to="/settings" replace />
  }

  return children
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
        <div style={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: '#0d1117',
          color: '#e6edf3',
          fontFamily: 'monospace',
          padding: '2rem',
        }}>
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
      <RouteLogger />
      <Layout>
        <Routes>
          <Route path="/settings" element={<Settings />} />

          <Route path="/" element={
            <ProtectedRoute>
              <Dashboard />
            </ProtectedRoute>
          } />

          <Route path="/opportunities" element={
            <ProtectedRoute>
              <Opportunities />
            </ProtectedRoute>
          } />

          <Route path="/opportunities/survey" element={
            <ProtectedRoute>
              <OpportunitySurvey />
            </ProtectedRoute>
          } />

          <Route path="/opportunities/:id" element={
            <ProtectedRoute>
              <OpportunityDetail />
            </ProtectedRoute>
          } />

          <Route path="/markets" element={
            <ProtectedRoute>
              <Markets />
            </ProtectedRoute>
          } />

          <Route path="/markets/:symbol" element={
            <ProtectedRoute>
              <MarketDetail />
            </ProtectedRoute>
          } />

          <Route path="/runs" element={
            <ProtectedRoute>
              <Runs />
            </ProtectedRoute>
          } />

          <Route path="/runs/compare" element={
            <ProtectedRoute>
              <RunCompare />
            </ProtectedRoute>
          } />

          <Route path="/runs/:id" element={
            <ProtectedRoute>
              <RunDetail />
            </ProtectedRoute>
          } />

          <Route path="/runs/:id/simulation" element={
            <ProtectedRoute>
              <SimulationResults />
            </ProtectedRoute>
          } />

          <Route path="/control" element={
            <ProtectedRoute>
              <ControlPlane />
            </ProtectedRoute>
          } />

          <Route path="/paper" element={
            <ProtectedRoute>
              <PaperTrading />
            </ProtectedRoute>
          } />

          <Route path="/analytics/correlations" element={
            <ProtectedRoute>
              <CorrelationAnalysis />
            </ProtectedRoute>
          } />

          <Route path="/pipeline" element={
            <ProtectedRoute>
              <Pipeline />
            </ProtectedRoute>
          } />

          {/* Catch all - redirect to dashboard */}
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </Layout>
    </ErrorBoundary>
  )
}
