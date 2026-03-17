import { useState } from 'react'
import { Link, NavLink } from 'react-router-dom'
import { ArrowRight, Menu, X, Zap } from 'lucide-react'
import { useSubscription } from '../context/SubscriptionContext'

const navigation = [
  { label: 'Overview', to: '/' },
  { label: 'Winners', to: '/winners' },
  { label: 'Pricing', to: '/pricing' },
  { label: 'Settings', to: '/settings' },
]

function NavItem({ to, children, onClick }) {
  return (
    <NavLink
      to={to}
      end={to === '/'}
      onClick={onClick}
      className={({ isActive }) => [
        'text-sm font-medium transition-colors',
        isActive ? 'text-amber-300' : 'text-terminal-300 hover:text-terminal-100',
      ].join(' ')}
    >
      {children}
    </NavLink>
  )
}

export default function MarketingLayout({ children }) {
  const { isPaid } = useSubscription()
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  const primaryHref = isPaid ? '/app' : '/upgrade'
  const primaryLabel = isPaid ? 'Open App' : 'Upgrade'

  return (
    <div
      className="min-h-screen overflow-x-hidden bg-terminal-900 text-terminal-100"
      style={{
        backgroundImage: `linear-gradient(rgba(217,119,6,var(--bg-grid-opacity)) 1.2px, transparent 1.2px), linear-gradient(90deg, rgba(217,119,6,var(--bg-grid-opacity)) 1.2px, transparent 1.2px)`,
        backgroundSize: `calc(var(--bg-grid-size) * 0.75) calc(var(--bg-grid-size) * 0.75)`,
      }}
    >
      <div className="pointer-events-none fixed inset-0 overflow-hidden">
        <div className="bg-amber-500/12 absolute -left-32 -top-32 size-72 rounded-full blur-3xl" />
        <div className="absolute -right-24 top-24 size-64 rounded-full bg-blue-500/10 blur-3xl" />
        <div className="bg-emerald-500/8 absolute -bottom-40 left-1/2 size-80 -translate-x-1/2 rounded-full blur-3xl" />
      </div>

      <header className="sticky top-0 z-40 border-b border-terminal-700/80 bg-terminal-900/90 backdrop-blur">
        <div className="flex w-full items-center justify-between px-4 py-1.5 sm:px-8 lg:px-6">
          <Link to="/" className="flex items-center gap-4">
            <div className="flex size-14 items-center justify-center rounded-2xl bg-gradient-to-br from-amber-400 to-amber-700 shadow-lg shadow-amber-900/30">
              <Zap className="size-7 text-terminal-900" />
            </div>
            <div className="text-xl font-bold tracking-[0.18em] text-terminal-100 sm:text-2xl">MCAS</div>
          </Link>

          <div className="hidden flex-1 justify-center md:flex">
            <span className="text-base font-semibold uppercase tracking-[0.25em] text-terminal-300 lg:text-lg">Market Cognition At Scale</span>
          </div>

          <nav className="hidden items-center gap-6 md:flex">
            {navigation.map((item) => (
              <NavItem key={item.to} to={item.to}>{item.label}</NavItem>
            ))}
          </nav>

          <div className="hidden items-center gap-3 md:flex">
            <Link to="/winners" className="btn btn-ghost btn-sm">
              Take the tour
            </Link>
            <Link to={primaryHref} className="btn btn-primary btn-sm">
              {primaryLabel}
              <ArrowRight className="size-4" />
            </Link>
          </div>

          <button
            type="button"
            className="inline-flex size-11 items-center justify-center rounded-xl border border-terminal-700 bg-terminal-800 text-terminal-200 md:hidden"
            onClick={() => setMobileMenuOpen((open) => !open)}
            aria-label="Toggle navigation"
          >
            {mobileMenuOpen ? <X className="size-5" /> : <Menu className="size-5" />}
          </button>
        </div>

        {mobileMenuOpen && (
          <div className="border-t border-terminal-700 bg-terminal-900/95 md:hidden">
            <div className="mx-auto flex max-w-7xl flex-col gap-4 p-4 sm:px-6">
              {navigation.map((item) => (
                <NavItem key={item.to} to={item.to} onClick={() => setMobileMenuOpen(false)}>
                  {item.label}
                </NavItem>
              ))}
              <div className="grid grid-cols-2 gap-3 pt-2">
                <Link to="/winners" className="btn btn-secondary btn-sm" onClick={() => setMobileMenuOpen(false)}>
                  Take the tour
                </Link>
                <Link to={primaryHref} className="btn btn-primary btn-sm" onClick={() => setMobileMenuOpen(false)}>
                  {primaryLabel}
                </Link>
              </div>
            </div>
          </div>
        )}
      </header>

      <main className="relative">
        <div className="mx-auto w-full px-4 sm:px-8 lg:px-6">
          {children}
        </div>
      </main>
    </div>
  )
}
