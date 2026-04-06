import { useEffect, useState } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import {
  BarChart3,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  GitBranch,
  Grid3X3,
  LayoutDashboard,
  Layers3,
  Menu,
  Network,
  Play,
  Settings,
  Target,
  Terminal,
  TrendingUp,
  Wallet,
  Wifi,
  WifiOff,
  X,
  Zap,
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { getLogger } from '../utils/logger'

const log = getLogger('layout')

const navSections = [
  {
    id: 'core',
    label: null,
    items: [
      { name: 'Dashboard', href: '/app', icon: LayoutDashboard },
      { name: 'Opportunities', href: '/app/opportunities', icon: Target },
      { name: 'Markets', href: '/app/markets', icon: BarChart3 },
      { name: 'Strategies', href: '/app/strategies', icon: Layers3 },
    ],
  },
  {
    id: 'intel',
    label: 'MARKET INTEL',
    defaultExpanded: true,
    items: [
      { name: 'Heatmap', href: '/app/heatmap', icon: Grid3X3 },
      { name: 'Derivatives', href: '/app/derivatives', icon: TrendingUp },
    ],
  },
  {
    id: 'research',
    label: 'RESEARCH',
    defaultExpanded: false,
    items: [
      { name: 'Correlations', href: '/app/analytics/correlations', icon: GitBranch },
      { name: 'Strategy Builder', href: '/app/strategies/builder', icon: Zap },
    ],
  },
  {
    id: 'operator',
    label: 'OPERATOR',
    defaultExpanded: false,
    items: [
      { name: 'Runs', href: '/app/runs', icon: Play },
      { name: 'Paper Trading', href: '/app/paper', icon: Wallet },
      { name: 'Control Plane', href: '/app/control', icon: Terminal },
      { name: 'Pipeline', href: '/app/pipeline', icon: Network },
    ],
  },
]

function loadSectionState() {
  try {
    const stored = localStorage.getItem('mcas_nav_sections')
    return stored ? JSON.parse(stored) : {}
  } catch {
    return {}
  }
}

function NavSection({ section, collapsed, isDesktop, location, onNavClick }) {
  const stored = loadSectionState()
  const defaultState = section.label === null ? true : (stored[section.id] !== undefined ? stored[section.id] : section.defaultExpanded)
  const [expanded, setExpanded] = useState(defaultState)

  const toggleExpanded = () => {
    if (section.label === null) return
    setExpanded((prev) => {
      const next = !prev
      try {
        const current = loadSectionState()
        current[section.id] = next
        localStorage.setItem('mcas_nav_sections', JSON.stringify(current))
      } catch { /* ignore */ }
      return next
    })
  }

  const showItems = section.label === null || expanded || collapsed

  return (
    <div>
      {section.label && !collapsed && (
        <button
          type="button"
          onClick={toggleExpanded}
          className="flex w-full items-center justify-between px-3 pt-4 pb-1 text-[10px] font-semibold uppercase tracking-[0.2em] text-terminal-500 hover:text-terminal-400 transition-colors"
        >
          <span>{section.label}</span>
          <ChevronDown
            className={[
              'size-3 transition-transform duration-200',
              expanded ? '' : '-rotate-90',
            ].join(' ')}
          />
        </button>
      )}
      {showItems && section.items.map((item) => {
        const isActive = isNavItemActive(location.pathname, item.href)

        return (
          <NavLink
            key={item.href}
            to={item.href}
            onClick={onNavClick}
            className={[
              'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
              collapsed ? 'md:justify-center md:px-2.5 md:gap-0' : '',
              isActive
                ? 'bg-amber-600/20 text-amber-400'
                : 'text-terminal-300 hover:bg-terminal-700 hover:text-terminal-100',
            ].join(' ')}
            title={collapsed ? item.name : undefined}
          >
            <item.icon className="size-5 shrink-0" />
            {!collapsed && <span className="whitespace-nowrap">{item.name}</span>}
          </NavLink>
        )
      })}
    </div>
  )
}

function isNavItemActive(pathname, href) {
  if (href === '/app') {
    return pathname === '/app'
  }

  return pathname === href || pathname.startsWith(`${href}/`)
}

function ConnectionIndicator({ collapsed }) {
  const { config, connectionStatus, isConnected } = useApi()

  const statusColor = isConnected
    ? 'bg-green-500'
    : connectionStatus.auth === 'unauthorized' || connectionStatus.health === 'unhealthy' || connectionStatus.health === 'error'
      ? 'bg-red-500'
      : 'bg-amber-500'

  const statusText = isConnected
    ? 'Connected'
    : connectionStatus.auth === 'unauthorized'
      ? 'Unauthorized'
      : connectionStatus.auth === 'missing'
        ? 'Session required'
      : connectionStatus.health === 'unhealthy'
        ? 'Unhealthy'
        : connectionStatus.health === 'error'
          ? 'Error'
          : connectionStatus.checking
            ? 'Checking...'
            : 'Disconnected'

  if (collapsed) {
    return (
      <div className="flex justify-center" title={statusText}>
        <div className={`size-3 rounded-full ${statusColor}`} />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <div className={`size-2.5 rounded-full ${statusColor}`} />
          <span className="text-sm text-terminal-200">{statusText}</span>
        </div>
        {isConnected ? (
          <Wifi className="size-4 text-green-400" />
        ) : (
          <WifiOff className="size-4 text-terminal-400" />
        )}
      </div>
      {config.baseUrl && (
        <div className="truncate text-xs text-terminal-400" title={config.baseUrl}>
          {config.baseUrl}
        </div>
      )}
    </div>
  )
}

function Header({ onOpenNavigation, isMobile }) {
  const location = useLocation()

  return (
    <header className="sticky top-0 z-20 border-b border-terminal-600 bg-terminal-900/90 backdrop-blur">
      <div className="flex items-center justify-between gap-4 px-4 py-3 md:px-6">
        <div className="flex items-center gap-3">
          {isMobile && (
            <button
              type="button"
              onClick={onOpenNavigation}
              className="inline-flex size-10 items-center justify-center rounded-lg border border-terminal-600 bg-terminal-800 text-terminal-100 md:hidden"
              aria-label="Open navigation"
            >
              <Menu className="size-5" />
            </button>
          )}
          <div>
            <div className="text-sm font-medium text-terminal-100">{location.pathname.replace('/app', '') || 'Dashboard'}</div>
          </div>
        </div>

        <div className="hidden text-xs text-terminal-500 sm:block" />
      </div>
    </header>
  )
}

export default function Layout({ children }) {
  const { connectionStatus, sessionReady } = useApi()
  const { addToast } = useToast()
  const location = useLocation()
  const navigate = useNavigate()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () => localStorage.getItem('sidebar_collapsed') === 'true'
  )
  const [mobileOpen, setMobileOpen] = useState(false)
  const [isDesktop, setIsDesktop] = useState(() => (
    typeof window !== 'undefined'
      ? window.matchMedia('(min-width: 768px)').matches
      : false
  ))

  const isPipeline = location.pathname === '/app/pipeline'
  const collapsed = isDesktop && sidebarCollapsed

  useEffect(() => {
    if (!sessionReady || location.pathname === '/settings') {
      return
    }

    if (connectionStatus.auth === 'unauthorized' || connectionStatus.auth === 'missing') {
      log.warn('Session redirect', {
        event: 'session_redirect',
        from: location.pathname,
        auth: connectionStatus.auth,
      })
      addToast(
        connectionStatus.auth === 'unauthorized'
          ? 'Backend session expired. Reconnect in Settings.'
          : 'Backend session required. Reconnect in Settings.',
        'error'
      )
      navigate('/settings', { replace: true })
    }
  }, [addToast, connectionStatus.auth, location.pathname, navigate, sessionReady])

  useEffect(() => {
    if (typeof window === 'undefined') return undefined

    const mediaQuery = window.matchMedia('(min-width: 768px)')
    const handleChange = (event) => {
      setIsDesktop(event.matches)
      if (event.matches) {
        setMobileOpen(false)
      }
    }

    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  useEffect(() => {
    if (!(mobileOpen && !isDesktop)) return undefined

    const previousOverflow = document.body.style.overflow
    const handleEscape = (event) => {
      if (event.key === 'Escape') {
        setMobileOpen(false)
      }
    }

    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', handleEscape)

    return () => {
      document.body.style.overflow = previousOverflow
      window.removeEventListener('keydown', handleEscape)
    }
  }, [mobileOpen, isDesktop])

  const toggleSidebar = () => {
    setSidebarCollapsed((previous) => {
      localStorage.setItem('sidebar_collapsed', String(!previous))
      return !previous
    })
  }

  return (
    <div className="min-h-screen bg-terminal-900 text-terminal-100 md:flex">
      {mobileOpen && !isDesktop && (
        <button
          type="button"
          className="bg-terminal-950/70 fixed inset-0 z-30 backdrop-blur-sm md:hidden"
          onClick={() => setMobileOpen(false)}
          aria-label="Close navigation backdrop"
        />
      )}

      <aside
        className={[
          'fixed inset-y-0 left-0 z-40 flex max-w-[85vw] flex-col border-r border-terminal-600 bg-terminal-800 shadow-2xl shadow-black/40 transition-transform duration-300 md:static md:max-w-none md:translate-x-0 md:shadow-none',
          mobileOpen ? 'translate-x-0' : '-translate-x-full',
          collapsed ? 'md:w-16' : 'md:w-64',
          'w-72',
        ].join(' ')}
      >
        <div className={`flex h-16 items-center border-b border-terminal-600 ${collapsed ? 'justify-center px-2' : 'justify-between px-4 md:px-6'}`}>
          <div className="flex items-center gap-3 overflow-hidden">
            <div className="flex size-8 shrink-0 items-center justify-center rounded bg-gradient-to-br from-amber-500 to-amber-700">
              <Zap className="size-5 text-white" />
            </div>
            {!collapsed && (
              <div className="min-w-0">
                <h1 className="font-bold tracking-tight text-terminal-100">MCAS</h1>
                <p className="text-[10px] uppercase tracking-widest text-terminal-400">Market Cognition</p>
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            {!isDesktop && (
              <button
                type="button"
                onClick={() => setMobileOpen(false)}
                className="rounded p-1.5 text-terminal-400 transition-colors hover:bg-terminal-700 hover:text-terminal-100 md:hidden"
                aria-label="Close navigation"
              >
                <X className="size-4" />
              </button>
            )}
            {isDesktop && (
              <button
                type="button"
                onClick={toggleSidebar}
                className={collapsed
                  ? 'flex size-10 items-center justify-center rounded bg-gradient-to-br from-amber-500 to-amber-700'
                  : 'rounded p-1.5 text-terminal-400 transition-colors hover:bg-terminal-700 hover:text-terminal-100'
                }
                aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
              >
                {collapsed ? <ChevronRight className="size-5 text-white" /> : <ChevronLeft className="size-4" />}
              </button>
            )}
          </div>
        </div>

        <nav className="flex-1 space-y-1 px-3 py-4">
          {navSections.map((section) => (
            <NavSection
              key={section.id}
              section={section}
              collapsed={collapsed}
              isDesktop={isDesktop}
              location={location}
              onNavClick={() => !isDesktop && setMobileOpen(false)}
            />
          ))}
        </nav>

        <div className="px-3 pb-4">
          <NavLink
            to="/settings"
            onClick={() => !isDesktop && setMobileOpen(false)}
            className={[
              'flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors',
              collapsed ? 'md:justify-center md:px-2.5 md:gap-0' : '',
              location.pathname === '/settings'
                ? 'bg-amber-600/20 text-amber-400'
                : 'text-terminal-300 hover:bg-terminal-700 hover:text-terminal-100',
            ].join(' ')}
            title={collapsed ? 'Settings' : undefined}
          >
            <Settings className="size-5 shrink-0" />
            {!collapsed && <span className="whitespace-nowrap">Settings</span>}
          </NavLink>
        </div>

        <div className={`border-t border-terminal-600 ${collapsed ? 'p-2' : 'p-4'}`}>
          <ConnectionIndicator collapsed={collapsed} />
        </div>
      </aside>

      <main className="flex min-h-screen flex-1 flex-col">
        <Header onOpenNavigation={() => setMobileOpen(true)} isMobile={!isDesktop} />
        <div className={`flex-1 overflow-auto ${isPipeline ? '' : 'p-4 md:p-6'}`}>
          {children}
        </div>
      </main>
    </div>
  )
}
