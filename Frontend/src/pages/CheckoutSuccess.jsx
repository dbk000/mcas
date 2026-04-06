import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, CheckCircle2, Settings as SettingsIcon } from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useSubscription } from '../context/SubscriptionContext'
import { FRONTEND_ANALYTICS_EVENTS, emitAnalyticsEvent } from '../utils/analytics'
import { usePageMeta } from '../utils/pageMeta'
import { usePublicJson } from '../utils/publicJson'
import {
  DEFAULT_RETURN_TO,
  clearPendingReturnTo,
  readPendingReturnTo,
  sanitizeReturnTo,
} from '../utils/returnTo'

export default function CheckoutSuccess() {
  usePageMeta('checkoutSuccess')

  const [searchParams] = useSearchParams()
  const { config, session } = useApi()
  const { refreshSubscription, isPaid } = useSubscription()
  const { data: onboarding } = usePublicJson('/curation/onboarding-flow.v1.json')
  const [storedReturnTo] = useState(() => readPendingReturnTo())

  const queryReturnTo = sanitizeReturnTo(searchParams.get('returnTo'))
  const resolvedReturnTo = queryReturnTo || storedReturnTo || ''

  const isAuthenticated = config.baseUrl && session.hasSession && session.auth === 'ok'

  const nextHref = useMemo(() => {
    if (!isAuthenticated) {
      const nextParams = new URLSearchParams({ from: 'checkout-success' })
      if (resolvedReturnTo) {
        nextParams.set('returnTo', resolvedReturnTo)
      }
      return `/settings?${nextParams.toString()}`
    }
    return resolvedReturnTo || DEFAULT_RETURN_TO
  }, [isAuthenticated, resolvedReturnTo])

  useEffect(() => {
    // If already authenticated, refresh subscription to claim it
    if (isAuthenticated) {
      refreshSubscription()
    }
    emitAnalyticsEvent(FRONTEND_ANALYTICS_EVENTS.checkoutSuccessLanded, {
      surface: 'checkout_success',
      placement: 'page_load',
      source_status: 'live',
    })
    clearPendingReturnTo()
  }, [isAuthenticated, refreshSubscription])

  return (
    <div className="space-y-8">
      <section className="rounded-[2rem] border border-emerald-500/30 bg-emerald-500/10 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-terminal-900/70 px-3 py-1 text-xs uppercase tracking-[0.22em] text-emerald-200">
          <CheckCircle2 className="size-3.5" />
          Payment Confirmed
        </div>
        <h1 className="mt-5 text-4xl font-semibold tracking-tight text-terminal-100 sm:text-5xl">
          {isAuthenticated && isPaid
            ? 'You\'re all set. Welcome to Pro.'
            : 'Payment received. One step left.'}
        </h1>
        <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
          {isAuthenticated && isPaid ? (
            'Your subscription is active. All Pro features are now unlocked.'
          ) : isAuthenticated ? (
            'Your payment was successful. Your subscription will be linked momentarily.'
          ) : (
            'Your payment was successful. Go to Settings to connect your account and activate Pro.'
          )}
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          {isAuthenticated && isPaid ? (
            <Link to={resolvedReturnTo || '/app'} className="btn btn-primary btn-sm">
              Open App
              <ArrowRight className="size-4" />
            </Link>
          ) : (
            <Link to={nextHref} className="btn btn-primary btn-sm">
              <SettingsIcon className="size-4" />
              {isAuthenticated ? 'Continue' : 'Connect Your Account'}
            </Link>
          )}
          <Link to="/app" className="btn btn-secondary btn-sm">
            Open Dashboard
          </Link>
        </div>
      </section>

      {!isAuthenticated && (
        <section className="rounded-[2rem] border border-amber-500/20 bg-amber-500/5 p-6 sm:p-8">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">How To Activate</div>
          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4">
              <div className="text-2xl font-semibold text-amber-300">1</div>
              <h3 className="mt-2 text-sm font-semibold text-terminal-100">Go to Settings</h3>
              <p className="mt-1 text-xs leading-5 text-terminal-400">
                Click the button above to get started.
              </p>
            </div>
            <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4">
              <div className="text-2xl font-semibold text-amber-300">2</div>
              <h3 className="mt-2 text-sm font-semibold text-terminal-100">Connect your account</h3>
              <p className="mt-1 text-xs leading-5 text-terminal-400">
                Enter your connection details to activate Pro features.
              </p>
            </div>
            <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4">
              <div className="text-2xl font-semibold text-amber-300">3</div>
              <h3 className="mt-2 text-sm font-semibold text-terminal-100">Start using Pro</h3>
              <p className="mt-1 text-xs leading-5 text-terminal-400">
                Live signals, unlimited simulations, verified audit trails, and full platform access — all unlocked.
              </p>
            </div>
          </div>
        </section>
      )}

      <section className="grid gap-4 lg:grid-cols-4">
        {(onboarding?.steps || []).map((step) => (
          <div key={step.id} className="rounded-[1.75rem] border border-terminal-700 bg-terminal-800/60 p-6">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">{step.label}</div>
            <h2 className="mt-3 text-xl font-semibold text-terminal-100">{step.headline}</h2>
            <p className="mt-3 text-sm leading-6 text-terminal-400">{step.body}</p>
          </div>
        ))}
      </section>
    </div>
  )
}
