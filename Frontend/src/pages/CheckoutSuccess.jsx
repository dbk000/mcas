import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, CheckCircle2 } from 'lucide-react'
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
  const { setPaid } = useSubscription()
  const { data: onboarding } = usePublicJson('/curation/onboarding-flow.v1.json')
  const [storedReturnTo] = useState(() => readPendingReturnTo())

  const queryReturnTo = sanitizeReturnTo(searchParams.get('returnTo'))
  const resolvedReturnTo = queryReturnTo || storedReturnTo || ''

  const nextHref = useMemo(() => {
    if (!config.baseUrl || !session.hasSession || session.auth !== 'ok') {
      const nextParams = new URLSearchParams({ from: 'checkout-success' })
      if (resolvedReturnTo) {
        nextParams.set('returnTo', resolvedReturnTo)
      }
      return `/settings?${nextParams.toString()}`
    }
    return resolvedReturnTo || DEFAULT_RETURN_TO
  }, [config.baseUrl, resolvedReturnTo, session.auth, session.hasSession])

  useEffect(() => {
    setPaid(new Date().toISOString())
    emitAnalyticsEvent(FRONTEND_ANALYTICS_EVENTS.checkoutSuccessLanded, {
      surface: 'checkout_success',
      placement: 'page_load',
      source_status: 'placeholder',
    })
    clearPendingReturnTo()
  }, [setPaid])

  return (
    <div className="space-y-8">
      <section className="rounded-[2rem] border border-emerald-500/30 bg-emerald-500/10 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="inline-flex items-center gap-2 rounded-full border border-emerald-500/30 bg-terminal-900/70 px-3 py-1 text-xs uppercase tracking-[0.22em] text-emerald-200">
          <CheckCircle2 className="size-3.5" />
          Local Activation Complete
        </div>
        <h1 className="mt-5 text-4xl font-semibold tracking-tight text-terminal-100 sm:text-5xl">This browser is now marked as Pro.</h1>
        <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
          Query parameters are not treated as secure payment proof in this release. This page only activates the local UX tier,
          sanitizes any requested return path, and points you to the next onboarding step.
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Link to={nextHref} className="btn btn-primary btn-sm">
            Continue
            <ArrowRight className="size-4" />
          </Link>
          <Link to="/app" className="btn btn-secondary btn-sm">
            Open /app
          </Link>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-4">
        {(onboarding?.steps || []).map((step) => (
          <div key={step.id} className="rounded-[1.75rem] border border-terminal-700 bg-terminal-800/60 p-6">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">{step.label}</div>
            <h2 className="mt-3 text-xl font-semibold text-terminal-100">{step.headline}</h2>
            <p className="mt-3 text-sm leading-6 text-terminal-400">{step.body}</p>
          </div>
        ))}
      </section>

      {onboarding?.trust_copy && (
        <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6 sm:p-8">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Why The Flow Looks Like This</div>
          <div className="mt-5 grid gap-4 lg:grid-cols-3">
            <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4 text-sm leading-6 text-terminal-300">
              {onboarding.trust_copy.activation_scope}
            </div>
            <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4 text-sm leading-6 text-terminal-300">
              {onboarding.trust_copy.payment_proof}
            </div>
            <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4 text-sm leading-6 text-terminal-300">
              {onboarding.trust_copy.backend_step}
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
