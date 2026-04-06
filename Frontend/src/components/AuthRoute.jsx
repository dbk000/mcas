import { Navigate, useLocation } from 'react-router-dom'
import { useApi } from '../context/ApiContext'

/**
 * Route guard that requires an authenticated session (free_subscribed or paid).
 * Redirects to /settings if no session exists.
 */
export default function AuthRoute({ children }) {
  const { config, session, sessionReady } = useApi()
  const location = useLocation()

  const settingsParams = new URLSearchParams({
    from: 'auth-route',
    returnTo: `${location.pathname}${location.search}`,
  })

  if (!config.baseUrl) {
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
            Connecting to MCAS...
          </p>
        </div>
      </div>
    )
  }

  if (!session.hasSession || session.auth !== 'ok') {
    return <Navigate to={`/settings?${settingsParams.toString()}`} replace />
  }

  return children
}
