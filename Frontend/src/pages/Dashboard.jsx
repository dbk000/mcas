import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  Activity,
  Target,
  Wallet,
  Play,
  AlertCircle,
  CheckCircle,
  Clock,
  ArrowRight,
  Zap,
  Gauge
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useTradingAccount, useTopSignals, useLatestRun } from '../hooks/useData'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  StatusBadge,
  DirectionBadge,
  Skeleton,
  EmptyState,
  RefreshControl
} from '../components/ui'
import {
  formatPrice,
  formatPercent,
  formatPnL,
  formatR,
  formatRelativeTime,
  formatDollar,
  getPnLColor
} from '../utils/formatters'

export default function Dashboard() {
  const { isConnected } = useApi()

  return (
    <div className="space-y-6" data-operator-surface>
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Dashboard</h1>
          <p className="mt-1 text-sm text-terminal-400">Market overview and system status</p>
        </div>
      </div>

      {/* Status banner if not connected */}
      {!isConnected && (
        <div className="flex items-center gap-3 rounded-lg border border-amber-700/50 bg-amber-900/20 p-4">
          <AlertCircle className="size-5 shrink-0 text-amber-400" />
          <div className="flex-1">
            <p className="text-sm text-amber-200">
              Not connected to backend. Please configure your connection in{' '}
              <Link to="/settings" className="underline hover:text-amber-100">Settings</Link>.
            </p>
          </div>
        </div>
      )}

      {/* Grid */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* System Status */}
        <div className="op-row h-auto"><SystemStatusPanel /></div>

        {/* Latest Scan */}
        <div className="op-row h-auto"><LatestScanPanel /></div>

        {/* Top Signals */}
        <div className="op-row h-auto"><TopSignalsPanel /></div>

        {/* Paper Trading Snapshot */}
        <div className="op-row h-auto"><PaperTradingPanel /></div>

        {/* Fear & Greed Index */}
        <div className="op-row h-auto"><FearGreedWidget /></div>
      </div>
    </div>
  )
}

function SystemStatusPanel() {
  const { connectionStatus } = useApi()
  const authStatus = connectionStatus.auth === 'ok'
    ? 'healthy'
    : connectionStatus.auth === 'unauthorized'
      ? 'unhealthy'
      : null
  const authLabel = connectionStatus.auth === 'ok'
    ? 'Session OK'
    : connectionStatus.auth === 'unauthorized'
      ? 'Unauthorized'
      : connectionStatus.auth === 'missing'
        ? 'No session'
        : 'Unknown'

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="size-4 text-amber-400" />
          System Status
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <StatusRow 
          label="Backend Health" 
          status={connectionStatus.health}
          statusLabel={connectionStatus.health === 'healthy' ? 'Healthy' : connectionStatus.health === 'unhealthy' ? 'Unhealthy' : 'Unknown'}
        />
        <StatusRow 
          label="Authentication" 
          status={authStatus}
          statusLabel={authLabel}
        />
        
        <div className="mt-2 flex items-center justify-between border-t border-terminal-600 py-2 pt-3">
          <span className="text-sm text-terminal-400">Redis Latency</span>
          <span className="font-mono text-sm text-terminal-100">
            {connectionStatus.redisMs !== null ? `${connectionStatus.redisMs}ms` : '—'}
          </span>
        </div>
        <div className="flex items-center justify-between py-2">
          <span className="text-sm text-terminal-400">PostgreSQL Latency</span>
          <span className="font-mono text-sm text-terminal-100">
            {connectionStatus.pgMs !== null ? `${connectionStatus.pgMs}ms` : '—'}
          </span>
        </div>
        
        {connectionStatus.lastChecked && (
          <p className="border-t border-terminal-600 pt-2 text-xs text-terminal-500">
            Last checked {formatRelativeTime(connectionStatus.lastChecked)}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function StatusRow({ label, status, statusLabel }) {
  return (
    <div className="flex items-center justify-between py-2">
      <span className="text-sm text-terminal-300">{label}</span>
      <div className="flex items-center gap-2">
        {status === 'healthy' ? (
          <CheckCircle className="size-4 text-green-400" />
        ) : status === 'unhealthy' ? (
          <AlertCircle className="size-4 text-red-400" />
        ) : (
          <div className="size-4 rounded-full bg-terminal-500" />
        )}
        <span className={`text-sm ${
          status === 'healthy' ? 'text-green-400' : 
          status === 'unhealthy' ? 'text-red-400' : 
          'text-terminal-400'
        }`}>
          {statusLabel}
        </span>
      </div>
    </div>
  )
}

function LatestScanPanel() {
  const { data, loading, error } = useLatestRun('SCAN')

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Play className="size-4 text-amber-400" />
          Latest Scan
        </CardTitle>
        <Link to="/app/runs" className="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300">
          View all <ArrowRight className="size-3" />
        </Link>
      </CardHeader>
      <CardContent>
        {loading ? (
          <div className="space-y-3">
            <Skeleton variant="title" />
            <Skeleton />
            <Skeleton className="w-2/3" />
          </div>
        ) : error ? (
          <EmptyState
            icon={Play}
            title="No scans yet"
            description="Run your first scan from the Control Plane"
          />
        ) : data ? (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-medium text-terminal-100">{data.name}</span>
              <StatusBadge status={data.status} />
            </div>
            
            {data.finished_at && (
              <div className="flex items-center gap-2 text-sm text-terminal-400">
                <Clock className="size-4" />
                {formatRelativeTime(data.finished_at)}
              </div>
            )}

            {data.summary && Object.keys(data.summary).length > 0 && (
              <div className="mt-3 rounded-lg bg-terminal-900 p-3">
                <p className="mb-2 text-xs uppercase tracking-wider text-terminal-500">Summary</p>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  {Object.entries(data.summary).slice(0, 4).map(([key, value]) => (
                    <div key={key}>
                      <span className="text-terminal-400">{key.replace(/_/g, ' ')}: </span>
                      <span className="font-mono text-terminal-100">
                        {typeof value === 'number' ? value.toFixed(2) : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <Link 
              to={`/app/runs/${data.id}`}
              className="btn btn-secondary btn-sm mt-3 w-full"
            >
              View Details
            </Link>
          </div>
        ) : (
          <EmptyState
            icon={Play}
            title="No scans yet"
            description="Run your first scan from the Control Plane"
          />
        )}
      </CardContent>
    </Card>
  )
}

function TopSignalsPanel() {
  const { data, loading, lastUpdated, refetch } = useTopSignals({ limit: 3 })

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Target className="size-4 text-amber-400" />
          Top Signals
        </CardTitle>
        <div className="flex items-center gap-3">
          <RefreshControl lastUpdated={lastUpdated} onRefresh={refetch} loading={loading} />
          <Link to="/app/opportunities" className="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300">
            View all <ArrowRight className="size-3" />
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="rounded-lg border border-terminal-600 p-3">
                <Skeleton variant="title" className="mb-2" />
                <Skeleton />
              </div>
            ))}
          </div>
        ) : data?.signals?.length > 0 ? (
          <div className="space-y-3">
            {data.signals.map((signal) => (
              <Link
                key={signal.id}
                to={`/app/opportunities/${signal.id}`}
                className="block rounded-lg border border-terminal-600 p-3 transition-all hover:border-terminal-400 hover:bg-terminal-700/30"
              >
                <div className="mb-2 flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-medium text-terminal-100">{signal.symbol}</span>
                    <Badge>{signal.timeframe}</Badge>
                    <Badge variant="info">{signal.setup_id}</Badge>
                  </div>
                  <DirectionBadge direction={signal.direction} />
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-terminal-400">Score: <span className="font-mono text-amber-400">{signal.score?.toFixed(1)}</span></span>
                  {signal.entry_price && (
                    <span className="text-terminal-400">Entry: <span className="font-mono text-terminal-100">{formatPrice(signal.entry_price)}</span></span>
                  )}
                </div>
              </Link>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={Target}
            title="No signals"
            description="Run a scan to generate signals"
          />
        )}
      </CardContent>
    </Card>
  )
}

function PaperTradingPanel() {
  const { data, loading } = useTradingAccount()

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Wallet className="size-4 text-amber-400" />
          Paper Trading
        </CardTitle>
        <Link to="/app/paper" className="flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300">
          View details <ArrowRight className="size-3" />
        </Link>
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <div className="space-y-3">
            <Skeleton variant="title" />
            <Skeleton />
            <Skeleton className="w-2/3" />
          </div>
        ) : !data?.active ? (
          <EmptyState
            icon={Wallet}
            title="No active session"
            description="Start a paper trading session from the Control Plane"
            action={
              <Link to="/app/control" className="btn btn-primary btn-sm mt-2">
                <Zap className="size-4" />
                Start Paper Trading
              </Link>
            }
          />
        ) : (
          <div className="space-y-4">
            {/* Capital */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="mb-1 text-xs uppercase tracking-wider text-terminal-500">Capital</p>
                <p className="font-mono text-xl font-bold text-terminal-100">
                  {formatDollar(data.account?.current_capital)}
                </p>
              </div>
              <div>
                <p className="mb-1 text-xs uppercase tracking-wider text-terminal-500">Total P&L</p>
                <p className={`font-mono text-xl font-bold ${getPnLColor(data.account?.total_pnl)}`}>
                  {formatPnL(data.account?.total_pnl)}
                </p>
              </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-3 border-t border-terminal-600 pt-3">
              <div className="text-center">
                <p className="font-mono text-lg font-bold text-terminal-100">
                  {formatPercent((data.performance?.win_rate ?? 0) * 100, 0)}
                </p>
                <p className="text-xs text-terminal-400">Win Rate</p>
              </div>
              <div className="text-center">
                <p className="font-mono text-lg font-bold text-terminal-100">
                  {data.performance?.total_trades || 0}
                </p>
                <p className="text-xs text-terminal-400">Trades</p>
              </div>
              <div className="text-center">
                <p className={`font-mono text-lg font-bold ${getPnLColor(data.account?.max_drawdown_pct)}`}>
                  {formatPercent(data.account?.max_drawdown_pct, 1)}
                </p>
                <p className="text-xs text-terminal-400">Max DD</p>
              </div>
            </div>

            {/* Quick stats */}
            <div className="flex items-center justify-between border-t border-terminal-600 pt-3 text-sm">
              <span className="text-terminal-400">
                Avg R: <span className="font-mono text-terminal-100">{formatR(data.performance?.average_r)}</span>
              </span>
              <span className="text-terminal-400">
                PF: <span className="font-mono text-terminal-100">{data.performance?.profit_factor?.toFixed(2) || '—'}</span>
              </span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function FearGreedGauge({ value }) {
  const clampedValue = Math.max(0, Math.min(100, value ?? 0))
  const angle = (clampedValue / 100) * 180 - 90 // -90 (left) to +90 (right)

  // Needle endpoint
  const needleLength = 62
  const rad = (angle * Math.PI) / 180
  const nx = 75 + needleLength * Math.cos(rad)
  const ny = 80 + needleLength * Math.sin(rad)

  // Arc segment helper: startPct and endPct are 0-100 mapped to 180deg arc
  function arcPath(startPct, endPct) {
    const r = 65
    const cx = 75
    const cy = 80
    const startAngle = ((startPct / 100) * 180 - 180) * (Math.PI / 180)
    const endAngle = ((endPct / 100) * 180 - 180) * (Math.PI / 180)
    const x1 = cx + r * Math.cos(startAngle)
    const y1 = cy + r * Math.sin(startAngle)
    const x2 = cx + r * Math.cos(endAngle)
    const y2 = cy + r * Math.sin(endAngle)
    const largeArc = endPct - startPct > 50 ? 1 : 0
    return `M ${x1} ${y1} A ${r} ${r} 0 ${largeArc} 1 ${x2} ${y2}`
  }

  const zones = [
    { start: 0, end: 25, color: '#ef4444' },    // red — extreme fear
    { start: 25, end: 45, color: '#f97316' },    // orange — fear
    { start: 45, end: 55, color: '#eab308' },    // yellow — neutral
    { start: 55, end: 75, color: '#4ade80' },    // light green — greed
    { start: 75, end: 100, color: '#22c55e' },   // green — extreme greed
  ]

  return (
    <svg viewBox="0 0 150 95" className="mx-auto w-full max-w-[200px]">
      {zones.map((z) => (
        <path
          key={z.start}
          d={arcPath(z.start, z.end)}
          fill="none"
          stroke={z.color}
          strokeWidth="10"
          strokeLinecap="butt"
          opacity="0.35"
        />
      ))}
      {/* Active arc up to current value */}
      {zones.filter(z => z.start < clampedValue).map((z) => {
        const segEnd = Math.min(z.end, clampedValue)
        if (segEnd <= z.start) return null
        return (
          <path
            key={`active-${z.start}`}
            d={arcPath(z.start, segEnd)}
            fill="none"
            stroke={z.color}
            strokeWidth="10"
            strokeLinecap="butt"
          />
        )
      })}
      {/* Needle */}
      <line
        x1="75" y1="80" x2={nx} y2={ny}
        stroke="#f5f5f5" strokeWidth="2" strokeLinecap="round"
      />
      <circle cx="75" cy="80" r="4" fill="#f5f5f5" />
      {/* Value text */}
      <text x="75" y="78" textAnchor="middle" fill="#f5f5f5" fontSize="18" fontWeight="bold" fontFamily="monospace">
        {clampedValue}
      </text>
    </svg>
  )
}

function getClassification(value) {
  if (value <= 25) return 'Extreme Fear'
  if (value <= 45) return 'Fear'
  if (value <= 55) return 'Neutral'
  if (value <= 75) return 'Greed'
  return 'Extreme Greed'
}

function getRegimeBadgeVariant(regime) {
  if (!regime) return 'default'
  const r = regime.toLowerCase()
  if (r === 'extreme_fear' || r === 'extreme fear') return 'error'
  if (r === 'fear') return 'warning'
  if (r === 'neutral') return 'default'
  if (r === 'greed') return 'success'
  if (r === 'extreme_greed' || r === 'extreme greed') return 'success'
  return 'default'
}

function FearGreedWidget() {
  const { get, isConnected } = useApi()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchData = useCallback(async () => {
    if (!isConnected) return
    try {
      setLoading(true)
      const result = await get('/v1/market/fear-greed')
      setData(result?.data ?? result)
      setError(null)
    } catch (err) {
      setError(err.message || 'Failed to fetch')
    } finally {
      setLoading(false)
    }
  }, [get, isConnected])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const value = data?.value
  const classification = data?.classification || (value != null ? getClassification(value) : null)
  const regime = data?.regime

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Gauge className="size-4 text-amber-400" />
          Fear &amp; Greed Index
        </CardTitle>
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <div className="space-y-3">
            <Skeleton className="mx-auto h-24 w-48" />
            <Skeleton className="mx-auto w-24" />
          </div>
        ) : error ? (
          <p className="py-4 text-center text-sm text-terminal-400">Unable to load index</p>
        ) : value != null ? (
          <div className="space-y-3">
            <FearGreedGauge value={value} />
            <div className="text-center">
              <p className="text-sm font-medium text-terminal-100">{classification}</p>
              {regime && (
                <Badge variant={getRegimeBadgeVariant(regime)} className="mt-1">
                  {regime.replace(/_/g, ' ').toUpperCase()}
                </Badge>
              )}
            </div>
          </div>
        ) : (
          <p className="py-4 text-center text-sm text-terminal-400">No data available</p>
        )}
      </CardContent>
    </Card>
  )
}
