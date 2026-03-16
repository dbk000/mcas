import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import {
  CheckCircle,
  Database,
  Key,
  Link2,
  Server,
  Settings as SettingsIcon,
  ShieldCheck,
  Wifi,
  XCircle,
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useSubscription } from '../context/SubscriptionContext'
import { Badge, Card, CardContent, CardHeader, CardTitle, Spinner } from '../components/ui'
import { trackSettingsConfigured } from '../utils/analytics'
import { getLogger } from '../utils/logger'
import { usePageMeta } from '../utils/pageMeta'
import { usePublicJson } from '../utils/publicJson'
import { sanitizeReturnTo } from '../utils/returnTo'

const log = getLogger('settings')

function getStatusCopy(connectionStatus) {
  if (connectionStatus.health === 'unhealthy') {
    return 'Backend is unhealthy'
  }

  if (connectionStatus.auth === 'unauthorized') {
    return 'Stored session is unauthorized'
  }

  if (connectionStatus.auth === 'missing') {
    return 'No backend session'
  }

  if (connectionStatus.health === 'error') {
    return 'Connection check failed'
  }

  return 'Not connected'
}

function StatusIndicator({ status, labels }) {
  const label = labels?.[status ?? 'null'] || labels?.null || 'Unknown'
  const tone = status === 'healthy' || status === 'ok'
    ? 'text-green-400'
    : status === 'unhealthy' || status === 'unauthorized'
      ? 'text-red-400'
      : 'text-terminal-300'

  return <span className={`text-sm font-medium ${tone}`}>{label}</span>
}

export default function Settings() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const {
    config,
    session,
    saveConfig,
    clearSession,
    connectionStatus,
    testConfig,
    testConnection,
    isConnected,
  } = useApi()
  const { tier, isPaid, activatedAt } = useSubscription()
  const origin = searchParams.get('from') || 'settings'
  const returnTo = sanitizeReturnTo(searchParams.get('returnTo'), { fallback: '/app' })
  const { data: onboardingFlow } = usePublicJson('/curation/onboarding-flow.v1.json')

  const [baseUrl, setBaseUrl] = useState(config.baseUrl)
  const [apiKey, setApiKey] = useState('')
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)

  usePageMeta({
    title: 'MCAS Settings | Configure Backend Session',
    description: 'Configure the MCAS backend URL and establish a server-backed session after local Pro activation.',
    image: '/social/og-default.png',
    url: '/settings',
  })

  const handleSave = () => {
    log.info('Settings saved', { event: 'settings_saved' })
    saveConfig({ baseUrl })
  }

  const handleConnect = async () => {
    log.info('Connection setup initiated', { event: 'settings_session_start' })
    setConnecting(true)
    const success = await testConfig({
      baseUrl,
      apiKey,
    })
    setConnecting(false)

    if (!success) {
      return
    }

    setApiKey('')

    trackSettingsConfigured({
      sourceStatus: 'live',
      hasBaseUrl: Boolean(baseUrl.trim()),
      hasApiKey: true,
      origin,
    })

    await testConnection(true)
    log.info('Backend session established', { event: 'settings_session_success' })
    setTimeout(() => navigate(returnTo, { replace: true }), 500)
  }

  const handleClearSession = async () => {
    setDisconnecting(true)
    await clearSession()
    setDisconnecting(false)
  }

  const sessionStatusLabel = session.auth === 'ok'
    ? 'Session active'
    : session.auth === 'unauthorized'
      ? 'Unauthorized'
      : session.auth === 'missing'
        ? 'No session'
        : 'Unknown'

  return (
    <div className="mx-auto max-w-5xl px-4 py-10 sm:px-6 lg:px-8">
      <div className="space-y-8">
        <section className="rounded-[2rem] border border-white/10 bg-gradient-to-br from-terminal-800 via-terminal-900 to-slate-950 px-5 py-6 sm:p-8">
          <div className="space-y-4">
            <div className="inline-flex items-center gap-2 rounded-full border border-cyan-400/30 bg-cyan-400/10 px-3 py-1 text-xs uppercase tracking-[0.22em] text-cyan-100">
              <ShieldCheck className="size-3.5" />
              {isPaid ? 'Pro active on this browser' : 'Public settings route'}
            </div>

            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <div className="flex items-center gap-3">
                  <div className="flex size-11 items-center justify-center rounded-2xl bg-white/10">
                    <SettingsIcon className="size-5 text-amber-300" />
                  </div>
                  <div>
                    <h1 className="text-3xl font-semibold text-white">Settings</h1>
                    <p className="text-sm text-terminal-300">Establish a server-backed backend session without the app sidebar shell.</p>
                  </div>
                </div>
                <p className="mt-4 max-w-3xl text-sm leading-6 text-terminal-300">
                  {isPaid
                    ? 'Local Pro activation is already set in this browser. Connect the backend once to unlock the paid app routes.'
                    : 'Settings stays public so you can preconfigure the backend before activation or finish backend onboarding after checkout.'}
                </p>
              </div>

              <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4 text-sm text-terminal-300">
                <div className="text-xs uppercase tracking-[0.18em] text-terminal-400">Origin</div>
                <div className="mt-2 text-terminal-100">{origin}</div>
                <div className="mt-3 text-xs uppercase tracking-[0.18em] text-terminal-400">Return target</div>
                <div className="mt-2 font-mono text-terminal-100">{returnTo}</div>
              </div>
            </div>

            <div className="grid gap-4 md:grid-cols-3">
              <Card className="border-white/10 bg-white/[0.04]">
                <CardContent className="space-y-2 p-5">
                  <div className="text-xs uppercase tracking-[0.18em] text-terminal-400">Tier</div>
                  <div className="text-2xl font-semibold text-white">{tier.toUpperCase()}</div>
                  <p className="text-sm text-terminal-300">
                    {onboardingFlow?.trust_copy?.activation_scope || 'Pro activation is still stored locally in this browser.'}
                  </p>
                </CardContent>
              </Card>
              <Card className="border-white/10 bg-white/[0.04]">
                <CardContent className="space-y-2 p-5">
                  <div className="text-xs uppercase tracking-[0.18em] text-terminal-400">Backend step</div>
                  <p className="text-sm text-terminal-300">
                    {onboardingFlow?.trust_copy?.backend_step || 'Connect the backend through a server-backed session before entering /app.'}
                  </p>
                  {activatedAt && <div className="text-xs text-emerald-200">Activated {new Date(activatedAt).toLocaleString()}</div>}
                </CardContent>
              </Card>
              <Card className="border-white/10 bg-white/[0.04]">
                <CardContent className="space-y-2 p-5">
                  <div className="text-xs uppercase tracking-[0.18em] text-terminal-400">App routes</div>
                  <div className="text-sm text-terminal-300">
                    `/app/*` now depends on a server-backed backend session. Local Pro is still a UX gate, not a secure entitlement.
                  </div>
                  <Link to="/pricing" className="text-sm text-amber-300 hover:text-amber-200">Review pricing</Link>
                </CardContent>
              </Card>
            </div>
          </div>
        </section>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Server className="size-4" />
              Backend Session
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <label className="mb-2 block text-sm font-medium text-terminal-200">
                API Base URL
              </label>
              <input
                type="text"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder="https://api.example.com"
                className="w-full"
              />
              <p className="mt-1 text-xs text-terminal-500">
                The backend origin used for session bootstrap, health checks, telemetry, and API calls.
              </p>
            </div>

            <div>
              <label className="mb-2 block text-sm font-medium text-terminal-200">
                <div className="flex items-center gap-2">
                  <Key className="size-4" />
                  API Key
                </div>
              </label>
              <div className="relative">
                <input
                  type="password"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="Enter your API key once to establish a backend session"
                  autoComplete="off"
                  autoCapitalize="off"
                  autoCorrect="off"
                  spellCheck={false}
                  data-1p-ignore
                  data-lpignore="true"
                  className="w-full"
                />
              </div>
              <p className="mt-1 text-xs text-terminal-500">
                The browser submits the key once to create an HttpOnly backend session. It is not persisted in frontend config.
              </p>
            </div>

            <div className="rounded-lg border border-terminal-700 bg-terminal-900/70 px-3 py-2 text-xs text-terminal-400">
              Save persists only the base URL. Connect creates a server-backed session on the selected backend.
            </div>

            <div className="flex flex-col gap-3 pt-2 sm:flex-row">
              <button
                onClick={handleConnect}
                disabled={connecting || !baseUrl || !apiKey}
                className="btn btn-primary flex-1"
              >
                {connecting ? (
                  <>
                    <Spinner size="sm" />
                    Connecting...
                  </>
                ) : (
                  <>
                    <Wifi className="size-4" />
                    Connect Backend
                  </>
                )}
              </button>
              <button onClick={handleSave} className="btn btn-secondary">
                Save URL
              </button>
              <button
                onClick={handleClearSession}
                disabled={disconnecting || session.auth !== 'ok'}
                className="btn btn-secondary"
              >
                {disconnecting ? 'Clearing...' : 'Clear Session'}
              </button>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="size-4" />
              Connection Status
            </CardTitle>
          </CardHeader>
          <CardContent>
            {connectionStatus.checking ? (
              <div className="flex items-center justify-center py-8">
                <Spinner size="lg" className="text-amber-400" />
              </div>
            ) : connectionStatus.lastChecked ? (
              <div className="space-y-4">
                <div className="flex items-center justify-between border-b border-terminal-600 py-2">
                  <span className="text-terminal-300">Backend Health</span>
                  <StatusIndicator
                    status={connectionStatus.health}
                    labels={{ healthy: 'Healthy', unhealthy: 'Unhealthy', error: 'Error', null: 'Unknown' }}
                  />
                </div>

                <div className="flex items-center justify-between border-b border-terminal-600 py-2">
                  <span className="text-terminal-300">Session Authentication</span>
                  <StatusIndicator
                    status={connectionStatus.auth}
                    labels={{ ok: 'Session active', missing: 'No session', unauthorized: 'Unauthorized', error: 'Error', null: 'Not checked' }}
                  />
                </div>

                <div className="flex items-center justify-between border-b border-terminal-600 py-2">
                  <span className="text-terminal-300">Session Source</span>
                  <span className="font-mono text-terminal-100">{session.sessionSource || 'none'}</span>
                </div>

                <div className="flex items-center justify-between border-b border-terminal-600 py-2">
                  <span className="text-terminal-300">Entitlement Mode</span>
                  <Badge variant="warning">{session.entitlementMode || 'preview_only'}</Badge>
                </div>

                {session.apiOrigin && (
                  <div className="flex items-center justify-between border-b border-terminal-600 py-2">
                    <span className="text-terminal-300">Resolved API Origin</span>
                    <span className="font-mono text-terminal-100">{session.apiOrigin}</span>
                  </div>
                )}

                {connectionStatus.redisMs !== null && (
                  <div className="flex items-center justify-between border-b border-terminal-600 py-2">
                    <span className="text-terminal-300">Redis Latency</span>
                    <span className="font-mono text-terminal-100">{connectionStatus.redisMs}ms</span>
                  </div>
                )}

                {connectionStatus.pgMs !== null && (
                  <div className="flex items-center justify-between border-b border-terminal-600 py-2">
                    <span className="text-terminal-300">PostgreSQL Latency</span>
                    <span className="font-mono text-terminal-100">{connectionStatus.pgMs}ms</span>
                  </div>
                )}

                <div className="flex items-center justify-between py-2">
                  <span className="text-terminal-300">Last Checked</span>
                  <span className="text-sm text-terminal-400">
                    {new Date(connectionStatus.lastChecked).toLocaleString()}
                  </span>
                </div>

                <div className="mt-4 rounded-lg border border-terminal-600 bg-terminal-900 p-4">
                  {isConnected ? (
                    <div className="flex items-center gap-3 text-green-400">
                      <CheckCircle className="size-5" />
                      <span className="font-medium">Connected and ready</span>
                    </div>
                  ) : (
                    <div className="flex items-center gap-3 text-red-400">
                      <XCircle className="size-5" />
                      <span className="font-medium">{getStatusCopy(connectionStatus)}</span>
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="py-8 text-center text-terminal-400">
                Save a base URL or establish a backend session to populate diagnostics.
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Link2 className="size-4" />
              Current Session Snapshot
            </CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 md:grid-cols-3">
            <div className="rounded-lg border border-terminal-700 bg-terminal-900/60 p-4">
              <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Auth</div>
              <div className="mt-2 text-lg font-semibold text-terminal-100">{sessionStatusLabel}</div>
            </div>
            <div className="rounded-lg border border-terminal-700 bg-terminal-900/60 p-4">
              <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">App access</div>
              <div className="mt-2 text-lg font-semibold text-terminal-100">
                {session.hasAppAccess ? 'Allowed' : 'Blocked'}
              </div>
            </div>
            <div className="rounded-lg border border-terminal-700 bg-terminal-900/60 p-4">
              <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Configured base URL</div>
              <div className="mt-2 break-all font-mono text-sm text-terminal-100">
                {config.baseUrl || 'Not saved'}
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
