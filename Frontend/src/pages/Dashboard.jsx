import { Link } from 'react-router-dom'
import { 
  Activity, 
  Target, 
  TrendingUp, 
  Wallet,
  Play,
  AlertCircle,
  CheckCircle,
  Clock,
  ArrowRight,
  Zap
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
  SkeletonCard,
  EmptyState,
  StatCard,
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
  const { connectionStatus, isConnected } = useApi()

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Dashboard</h1>
          <p className="text-sm text-terminal-400 mt-1">Market overview and system status</p>
        </div>
      </div>

      {/* Status banner if not connected */}
      {!isConnected && (
        <div className="bg-amber-900/20 border border-amber-700/50 rounded-lg p-4 flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-amber-400 flex-shrink-0" />
          <div className="flex-1">
            <p className="text-sm text-amber-200">
              Not connected to backend. Please configure your connection in{' '}
              <Link to="/settings" className="underline hover:text-amber-100">Settings</Link>.
            </p>
          </div>
        </div>
      )}

      {/* Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* System Status */}
        <SystemStatusPanel />

        {/* Latest Scan */}
        <LatestScanPanel />

        {/* Top Signals */}
        <TopSignalsPanel />

        {/* Paper Trading Snapshot */}
        <PaperTradingPanel />
      </div>
    </div>
  )
}

function SystemStatusPanel() {
  const { connectionStatus, isConnected } = useApi()

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Activity className="w-4 h-4 text-amber-400" />
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
          status={connectionStatus.auth === 'ok' ? 'healthy' : connectionStatus.auth === 'unauthorized' ? 'unhealthy' : null}
          statusLabel={connectionStatus.auth === 'ok' ? 'OK' : connectionStatus.auth === 'unauthorized' ? 'Unauthorized' : 'Unknown'}
        />
        
        <div className="flex justify-between items-center py-2 border-t border-terminal-600 mt-2 pt-3">
          <span className="text-sm text-terminal-400">Redis Latency</span>
          <span className="text-sm font-mono text-terminal-100">
            {connectionStatus.redisMs !== null ? `${connectionStatus.redisMs}ms` : '—'}
          </span>
        </div>
        <div className="flex justify-between items-center py-2">
          <span className="text-sm text-terminal-400">PostgreSQL Latency</span>
          <span className="text-sm font-mono text-terminal-100">
            {connectionStatus.pgMs !== null ? `${connectionStatus.pgMs}ms` : '—'}
          </span>
        </div>
        
        {connectionStatus.lastChecked && (
          <p className="text-xs text-terminal-500 pt-2 border-t border-terminal-600">
            Last checked {formatRelativeTime(connectionStatus.lastChecked)}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function StatusRow({ label, status, statusLabel }) {
  return (
    <div className="flex justify-between items-center py-2">
      <span className="text-sm text-terminal-300">{label}</span>
      <div className="flex items-center gap-2">
        {status === 'healthy' ? (
          <CheckCircle className="w-4 h-4 text-green-400" />
        ) : status === 'unhealthy' ? (
          <AlertCircle className="w-4 h-4 text-red-400" />
        ) : (
          <div className="w-4 h-4 rounded-full bg-terminal-500" />
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
          <Play className="w-4 h-4 text-amber-400" />
          Latest Scan
        </CardTitle>
        <Link to="/runs" className="text-xs text-amber-400 hover:text-amber-300 flex items-center gap-1">
          View all <ArrowRight className="w-3 h-3" />
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
                <Clock className="w-4 h-4" />
                {formatRelativeTime(data.finished_at)}
              </div>
            )}

            {data.summary && Object.keys(data.summary).length > 0 && (
              <div className="mt-3 p-3 bg-terminal-900 rounded-lg">
                <p className="text-xs text-terminal-500 uppercase tracking-wider mb-2">Summary</p>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  {Object.entries(data.summary).slice(0, 4).map(([key, value]) => (
                    <div key={key}>
                      <span className="text-terminal-400">{key.replace(/_/g, ' ')}: </span>
                      <span className="text-terminal-100 font-mono">
                        {typeof value === 'number' ? value.toFixed(2) : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <Link 
              to={`/runs/${data.id}`}
              className="btn btn-secondary btn-sm w-full mt-3"
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
          <Target className="w-4 h-4 text-amber-400" />
          Top Signals
        </CardTitle>
        <div className="flex items-center gap-3">
          <RefreshControl lastUpdated={lastUpdated} onRefresh={refetch} loading={loading} />
          <Link to="/opportunities" className="text-xs text-amber-400 hover:text-amber-300 flex items-center gap-1">
            View all <ArrowRight className="w-3 h-3" />
          </Link>
        </div>
      </CardHeader>
      <CardContent>
        {loading && !data ? (
          <div className="space-y-3">
            {[1, 2, 3].map(i => (
              <div key={i} className="p-3 border border-terminal-600 rounded-lg">
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
                to={`/opportunities/${signal.id}`}
                className="block p-3 border border-terminal-600 rounded-lg hover:border-terminal-400 hover:bg-terminal-700/30 transition-all"
              >
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-medium text-terminal-100">{signal.symbol}</span>
                    <Badge>{signal.timeframe}</Badge>
                    <Badge variant="info">{signal.setup_id}</Badge>
                  </div>
                  <DirectionBadge direction={signal.direction} />
                </div>
                <div className="flex items-center justify-between text-sm">
                  <span className="text-terminal-400">Score: <span className="text-amber-400 font-mono">{signal.score?.toFixed(1)}</span></span>
                  {signal.entry_price && (
                    <span className="text-terminal-400">Entry: <span className="text-terminal-100 font-mono">{formatPrice(signal.entry_price)}</span></span>
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
  const { data, loading, lastUpdated, refetch } = useTradingAccount()

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="flex items-center gap-2">
          <Wallet className="w-4 h-4 text-amber-400" />
          Paper Trading
        </CardTitle>
        <Link to="/paper" className="text-xs text-amber-400 hover:text-amber-300 flex items-center gap-1">
          View details <ArrowRight className="w-3 h-3" />
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
              <Link to="/control" className="btn btn-primary btn-sm mt-2">
                <Zap className="w-4 h-4" />
                Start Paper Trading
              </Link>
            }
          />
        ) : (
          <div className="space-y-4">
            {/* Capital */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-terminal-500 uppercase tracking-wider mb-1">Capital</p>
                <p className="text-xl font-bold font-mono text-terminal-100">
                  {formatDollar(data.account?.current_capital)}
                </p>
              </div>
              <div>
                <p className="text-xs text-terminal-500 uppercase tracking-wider mb-1">Total P&L</p>
                <p className={`text-xl font-bold font-mono ${getPnLColor(data.account?.total_pnl)}`}>
                  {formatPnL(data.account?.total_pnl)}
                </p>
              </div>
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-3 pt-3 border-t border-terminal-600">
              <div className="text-center">
                <p className="text-lg font-bold font-mono text-terminal-100">
                  {formatPercent((data.performance?.win_rate ?? 0) * 100, 0)}
                </p>
                <p className="text-xs text-terminal-400">Win Rate</p>
              </div>
              <div className="text-center">
                <p className="text-lg font-bold font-mono text-terminal-100">
                  {data.performance?.total_trades || 0}
                </p>
                <p className="text-xs text-terminal-400">Trades</p>
              </div>
              <div className="text-center">
                <p className={`text-lg font-bold font-mono ${getPnLColor(data.account?.max_drawdown_pct)}`}>
                  {formatPercent(data.account?.max_drawdown_pct, 1)}
                </p>
                <p className="text-xs text-terminal-400">Max DD</p>
              </div>
            </div>

            {/* Quick stats */}
            <div className="flex items-center justify-between text-sm pt-3 border-t border-terminal-600">
              <span className="text-terminal-400">
                Avg R: <span className="text-terminal-100 font-mono">{formatR(data.performance?.average_r)}</span>
              </span>
              <span className="text-terminal-400">
                PF: <span className="text-terminal-100 font-mono">{data.performance?.profit_factor?.toFixed(2) || '—'}</span>
              </span>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
