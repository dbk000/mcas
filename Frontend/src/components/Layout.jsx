import { useState, useEffect } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  LayoutDashboard,
  Target,
  BarChart3,
  Play,
  Terminal,
  Wallet,
  Settings,
  Wifi,
  WifiOff,
  RefreshCw,
  Zap,
  GitBranch,
  Network,
  ChevronLeft,
  ChevronRight
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { useStrategies } from '../hooks/useData'
import { formatRelativeTime } from '../utils/formatters'
import { getLogger } from '../utils/logger'

const log = getLogger('layout')

const navigation = [
  { name: 'Dashboard', href: '/', icon: LayoutDashboard },
  { name: 'Opportunities', href: '/opportunities', icon: Target },
  { name: 'Markets', href: '/markets', icon: BarChart3 },
  { name: 'Runs', href: '/runs', icon: Play },
  { name: 'Control Plane', href: '/control', icon: Terminal },
  { name: 'Paper Trading', href: '/paper', icon: Wallet },
  { name: 'Correlations', href: '/analytics/correlations', icon: GitBranch },
  { name: 'Pipeline', href: '/pipeline', icon: Network },
]

export default function Layout({ children }) {
  const { config, connectionStatus, isConnected } = useApi()
  const { addToast } = useToast()
  const location = useLocation()
  const navigate = useNavigate()
  const isPipeline = location.pathname === '/pipeline'
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem('sidebar_collapsed') === 'true'
  )
  const toggleSidebar = () => {
    setSidebarCollapsed(prev => {
      localStorage.setItem('sidebar_collapsed', String(!prev))
      return !prev
    })
  }

  // Fix #2: Redirect to settings when auth becomes unauthorized
  useEffect(() => {
    if (connectionStatus.auth === 'unauthorized' && location.pathname !== '/settings') {
      log.warn('Unauthorized redirect', { event: 'unauthorized_redirect', from: location.pathname })
      addToast('Unauthorized: please check your API key', 'error')
      navigate('/settings', { replace: true })
    }
  }, [connectionStatus.auth, location.pathname, navigate, addToast])

  return (
    <div className="min-h-screen bg-terminal-900 flex">
      {/* Sidebar */}
      <aside className={`${sidebarCollapsed ? 'w-16' : 'w-64'} bg-terminal-800 border-r border-terminal-600 flex flex-col transition-all duration-300 overflow-hidden`}>
        {/* Logo / Toggle */}
        <div className={`h-16 flex items-center border-b border-terminal-600 ${sidebarCollapsed ? 'justify-center' : 'px-6'}`}>
          {sidebarCollapsed ? (
            <button
              onClick={toggleSidebar}
              className="w-10 h-10 rounded bg-gradient-to-br from-amber-500 to-amber-700 flex items-center justify-center hover:from-amber-400 hover:to-amber-600 transition-colors"
              title="Expand sidebar"
            >
              <ChevronRight className="w-5 h-5 text-white" />
            </button>
          ) : (
            <div className="flex items-center justify-between w-full">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded bg-gradient-to-br from-amber-500 to-amber-700 flex items-center justify-center">
                  <Zap className="w-5 h-5 text-white" />
                </div>
                <div>
                  <h1 className="font-bold text-terminal-100 tracking-tight">MCAS</h1>
                  <p className="text-[10px] text-terminal-400 uppercase tracking-widest">Market Cognition</p>
                </div>
              </div>
              <button
                onClick={toggleSidebar}
                className="p-1.5 rounded hover:bg-terminal-700 text-terminal-400 hover:text-terminal-200 transition-colors"
                title="Collapse sidebar"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
            </div>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 py-4 px-3 space-y-1">
          {navigation.map((item) => {
            const isActive = location.pathname === item.href ||
              (item.href !== '/' && location.pathname.startsWith(item.href))

            return (
              <NavLink
                key={item.name}
                to={item.href}
                title={sidebarCollapsed ? item.name : undefined}
                className={`
                  flex items-center ${sidebarCollapsed ? 'justify-center' : 'gap-3'} px-3 py-2.5 rounded-lg text-sm font-medium
                  transition-all duration-200
                  ${isActive
                    ? `bg-amber-600/20 text-amber-400${sidebarCollapsed ? '' : ' border-l-2 border-amber-500 ml-0.5'}`
                    : 'text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700'
                  }
                `}
              >
                <item.icon className="w-5 h-5 shrink-0" />
                {!sidebarCollapsed && <span className="whitespace-nowrap">{item.name}</span>}
              </NavLink>
            )
          })}
        </nav>

        {/* Settings link */}
        <div className="px-3 pb-4">
          <NavLink
            to="/settings"
            title={sidebarCollapsed ? 'Settings' : undefined}
            className={`
              flex items-center ${sidebarCollapsed ? 'justify-center' : 'gap-3'} px-3 py-2.5 rounded-lg text-sm font-medium
              transition-all duration-200
              ${location.pathname === '/settings'
                ? 'bg-amber-600/20 text-amber-400'
                : 'text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700'
              }
            `}
          >
            <Settings className="w-5 h-5 shrink-0" />
            {!sidebarCollapsed && <span className="whitespace-nowrap">Settings</span>}
          </NavLink>
        </div>

        {/* Connection status */}
        <div className={`border-t border-terminal-600 ${sidebarCollapsed ? 'p-2' : 'p-4'}`}>
          <ConnectionIndicator collapsed={sidebarCollapsed} />
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 flex flex-col min-h-screen">
        {/* Top header */}
        <Header />
        
        {/* Content — no padding on Pipeline for full-bleed canvas */}
        <div className={`flex-1 overflow-auto ${isPipeline ? '' : 'p-6'}`}>
          {children}
        </div>
      </main>
    </div>
  )
}

function ConnectionIndicator({ collapsed }) {
  const { config, connectionStatus, isConnected, testConnection } = useApi()
  const [retesting, setRetesting] = useState(false)

  const statusColor = isConnected
    ? 'bg-green-500'
    : connectionStatus.health === 'unhealthy'
      ? 'bg-red-500'
      : connectionStatus.health === 'error'
        ? 'bg-red-500'
        : 'bg-amber-500'

  const statusText = isConnected
    ? 'Connected'
    : connectionStatus.health === 'unhealthy'
      ? 'Unhealthy'
      : connectionStatus.health === 'error'
        ? 'Error'
        : connectionStatus.auth === 'unauthorized'
          ? 'Unauthorized'
          : connectionStatus.checking
            ? 'Connecting...'
            : 'Disconnected'

  const handleRetest = async () => {
    log.info('Connection retest', { event: 'connection_retest' })
    setRetesting(true)
    await testConnection()
    setRetesting(false)
  }

  if (collapsed) {
    return (
      <div className="flex justify-center" title={statusText}>
        <div className={`w-3 h-3 rounded-full ${statusColor} ${isConnected ? 'animate-pulse-slow' : ''}`} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${statusColor} ${isConnected ? 'animate-pulse-slow' : ''}`} />
          <span className="text-sm text-terminal-200">{statusText}</span>
        </div>
        {isConnected ? (
          <Wifi className="w-4 h-4 text-green-400" />
        ) : (
          <WifiOff className="w-4 h-4 text-terminal-400" />
        )}
      </div>

      {config.baseUrl && (
        <p className="text-xs text-terminal-400 truncate" title={config.baseUrl}>
          {config.baseUrl.replace('https://', '').replace('http://', '')}
        </p>
      )}

      {connectionStatus.lastChecked && (
        <p className="text-xs text-terminal-500">
          Last check: {formatRelativeTime(connectionStatus.lastChecked)}
        </p>
      )}

      {/* Re-test connection button */}
      {config.baseUrl && config.apiKey && !connectionStatus.checking && (
        <button
          onClick={handleRetest}
          disabled={retesting}
          className="w-full flex items-center justify-center gap-2 px-2 py-1.5 text-xs bg-terminal-700 hover:bg-terminal-600 text-terminal-300 rounded transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${retesting ? 'animate-spin' : ''}`} />
          Re-test
        </button>
      )}
    </div>
  )
}

const PIPELINE_SUBSYSTEMS = [
  { name: 'ingestion', color: '#3b82f6' },
  { name: 'feature', color: '#8b5cf6' },
  { name: 'signal', color: '#f59e0b' },
  { name: 'filtering', color: '#ef4444' },
  { name: 'strategy', color: '#10b981' },
  { name: 'parameter', color: '#6366f1' },
  { name: 'backtest', color: '#ec4899' },
  { name: 'metrics', color: '#14b8a6' },
  { name: 'risk', color: '#f97316' },
  { name: 'execution', color: '#22c55e' },
]

function Header() {
  const { connectionStatus, isConnected } = useApi()
  const location = useLocation()
  const isPipeline = location.pathname === '/pipeline'

  return (
    <header className="h-14 bg-terminal-800/50 border-b border-terminal-600 flex items-center justify-between px-6">
      <div className="flex items-center gap-4">
        {/* Latency indicators */}
        {isConnected && (
          <div className="flex items-center gap-4 text-xs font-mono">
            {connectionStatus.redisMs !== null && (
              <span className="text-terminal-400">
                Redis: <span className="text-terminal-200">{connectionStatus.redisMs}ms</span>
              </span>
            )}
            {connectionStatus.pgMs !== null && (
              <span className="text-terminal-400">
                PG: <span className="text-terminal-200">{connectionStatus.pgMs}ms</span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Pipeline subsystem legend — shown when on Pipeline page */}
      {isPipeline && (
        <div className="flex flex-wrap gap-x-2.5 gap-y-0.5 text-[10px]">
          {PIPELINE_SUBSYSTEMS.map(s => (
            <div key={s.name} className="flex items-center gap-1">
              <div className="w-1.5 h-1.5 rounded-sm flex-shrink-0" style={{ backgroundColor: s.color }} />
              <span className="text-terminal-500 capitalize">{s.name}</span>
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center gap-3">
        {/* Quick run scan button */}
        <QuickScanButton />
      </div>
    </header>
  )
}

function QuickScanButton() {
  const { post, isConnected } = useApi()
  const { addToast } = useToast()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(false)
  const { data: strategyData } = useStrategies()

  const handleScan = async () => {
    if (!isConnected || loading) return

    setLoading(true)
    try {
      // Use active strategies from registry (exclude legacy)
      const activeIds = (strategyData?.strategies || [])
        .filter(s => s.status === 'active')
        .map(s => s.id)
      const result = await post('/v1/control/runs/scan', {
        setup_ids: activeIds.length > 0 ? activeIds : ['S9', 'U1', 'S10'],
        top_n: 10,
        priority: 5,
      })
      log.info('Quick scan queued', { event: 'quick_scan_queued', run_id: result.run_id })
      addToast(`Scan queued: ${result.fingerprint?.slice(0, 8) || 'started'}`, 'success')
      if (result.run_id) {
        navigate(`/runs/${result.run_id}`)
      }
    } catch (err) {
      log.error('Quick scan failed', { event: 'quick_scan_error', error_message: err.message })
      addToast(`Failed to queue scan: ${err.message}`, 'error')
    } finally {
      setLoading(false)
    }
  }

  return (
    <button
      onClick={handleScan}
      disabled={!isConnected || loading}
      className="btn btn-primary btn-sm"
    >
      {loading ? (
        <RefreshCw className="w-4 h-4 animate-spin" />
      ) : (
        <Zap className="w-4 h-4" />
      )}
      Run Scan
    </button>
  )
}
