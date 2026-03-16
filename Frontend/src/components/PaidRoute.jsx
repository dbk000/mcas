import { Link, useLocation } from 'react-router-dom'
import { ArrowRight, LockKeyhole, Radar, ShieldCheck, TimerReset } from 'lucide-react'
import { useSubscription } from '../context/SubscriptionContext'
import { FRONTEND_ANALYTICS_EVENTS, emitCtaEvent } from '../utils/analytics'
import { buildUpgradeHref } from '../utils/returnTo'

function PaywallButton({ href, label, placement, variant = 'primary', tier, children }) {
  const classes = variant === 'primary'
    ? 'btn btn-primary'
    : 'btn btn-secondary'

  return (
    <Link
      to={href}
      className={`${classes} btn-sm`}
      onClick={() => emitCtaEvent(FRONTEND_ANALYTICS_EVENTS.paywallCtaClick, {
        surface: 'paywall',
        placement,
        source_status: 'placeholder',
        ctaId: placement,
        ctaLabel: label,
        href,
        tier,
        returnToPresent: href.includes('returnTo='),
      })}
    >
      {children}
    </Link>
  )
}

export default function PaidRoute({ children }) {
  const location = useLocation()
  const {
    isPaid: hasLocalActivation,
    tier,
    accessMode,
    entitlementSource,
  } = useSubscription()

  if (hasLocalActivation) {
    return children
  }

  const returnTo = `${location.pathname}${location.search}${location.hash}`
  const upgradeHref = buildUpgradeHref(returnTo)

  return (
    <div className="min-h-screen overflow-x-hidden bg-terminal-900 text-terminal-100">
      <div className="mx-auto flex min-h-screen max-w-7xl flex-col px-4 py-2 sm:px-6 sm:py-8 lg:px-8">
        <div className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr] lg:items-stretch">
          <section className="relative overflow-hidden rounded-3xl border border-terminal-700 bg-terminal-800/70 p-4 shadow-2xl shadow-black/25 sm:p-8 lg:p-10">
            <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-amber-300 via-amber-500 to-red-400" />
            <div className="inline-flex items-center gap-2 rounded-full border border-amber-500/30 bg-amber-500/10 px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.22em] text-amber-200">
              <LockKeyhole className="size-3" />
              Paid Operator Surface
            </div>

            <h1 className="mt-4 max-w-2xl text-xl font-semibold tracking-tight text-terminal-100 sm:text-4xl">
              This route stays behind a browser-local Pro preview gate.
            </h1>
            <p className="mt-2 max-w-2xl text-xs leading-5 text-terminal-300 sm:text-base">
              The public side shows curated historical proof. The operator side unlocks live strategy ranking,
              Glass Box state, timing windows, and honest PF surfaces inside the app shell after local activation.
              Until a backend entitlement exists, this gate separates UX states only.
            </p>

            <div className="mt-4 grid gap-2 sm:grid-cols-3">
              <div className="rounded-xl border border-terminal-700 bg-terminal-900/70 p-3">
                <ShieldCheck className="size-4 text-emerald-300" />
                <div className="mt-2 text-xs font-semibold text-terminal-100">Glass Box visibility</div>
                <p className="mt-1 text-[10px] leading-4 text-terminal-400">See whether timing stayed verified, violated, unknown, or not attached.</p>
              </div>
              <div className="rounded-xl border border-terminal-700 bg-terminal-900/70 p-3">
                <Radar className="size-4 text-amber-300" />
                <div className="mt-2 text-xs font-semibold text-terminal-100">Live strategy ladder</div>
                <p className="mt-1 text-[10px] leading-4 text-terminal-400">Rank active setups by urgency, freshness, and verified opportunity count.</p>
              </div>
              <div className="rounded-xl border border-terminal-700 bg-terminal-900/70 p-3">
                <TimerReset className="size-4 text-sky-300" />
                <div className="mt-2 text-xs font-semibold text-terminal-100">Honest metrics</div>
                <p className="mt-1 text-[10px] leading-4 text-terminal-400">Pair aggregate PF with Avg Daily PF so flattering totals do not hide inconsistency.</p>
              </div>
            </div>

            <div className="mt-6 flex flex-col gap-2 sm:flex-row">
              <PaywallButton href={upgradeHref} label="Upgrade to Pro" placement="hero_primary" tier={tier}>
                Upgrade To Pro
                <ArrowRight className="size-4" />
              </PaywallButton>
              <PaywallButton href="/winners" label="Review Free Winners" placement="hero_secondary" variant="secondary" tier={tier}>
                Review Free Winners
              </PaywallButton>
            </div>

            <div className="mt-6 rounded-xl border border-terminal-700 bg-terminal-900/80 p-3 text-[11px] text-terminal-400">
              <div className="font-semibold text-terminal-200">Trust note</div>
              <p className="mt-1">
                This is a {accessMode.replace('_', ' ')} gate only. Current source: {entitlementSource.replace('_', ' ')}.
                Secure entitlement enforcement is intentionally out of scope in the current front-end-only implementation.
              </p>
            </div>
          </section>

          <aside className="flex flex-col gap-4 rounded-3xl border border-terminal-700 bg-terminal-800/60 p-4 sm:p-8">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-terminal-500">Locked From Here</div>
              <div className="mt-2 text-lg font-semibold text-terminal-100">{location.pathname}</div>
              <p className="mt-1 text-xs leading-5 text-terminal-400">
                Activate Pro locally, then connect your backend in Settings. After that, `/app/*` routes will resolve
                through the app shell instead of this gate.
              </p>
            </div>

            <div className="space-y-2">
              {[
                'Live strategy dashboard with ranked cards',
                'Opportunity detail with Glass Box badge',
                'Run detail with Avg Daily PF',
              ].map((item) => (
                <div key={item} className="rounded-xl border border-terminal-700 bg-terminal-900/70 px-3 py-2 text-[11px] text-terminal-300">
                  {item}
                </div>
              ))}
            </div>

            <div className="mt-auto space-y-2 pt-4">
              <PaywallButton href={upgradeHref} label="Unlock operator access" placement="sidebar_primary" tier={tier}>
                Unlock Operator Access
                <ArrowRight className="size-4" />
              </PaywallButton>
              <PaywallButton href="/pricing" label="See pricing" placement="sidebar_pricing" variant="secondary" tier={tier}>
                Compare Plans
              </PaywallButton>
            </div>
          </aside>
        </div>
      </div>
    </div>
  )
}
