import { useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, ExternalLink, ShieldCheck } from 'lucide-react'
import { useSubscription } from '../context/SubscriptionContext'
import { usePageMeta } from '../utils/pageMeta'
import { usePublicJson } from '../utils/publicJson'
import { sanitizeReturnTo, storePendingReturnTo } from '../utils/returnTo'
import { FeedbackOnset } from '../components/FeedbackOnset'

export default function Upgrade() {
  usePageMeta('upgrade')

  const [searchParams] = useSearchParams()
  const { isPaid } = useSubscription()
  const { data: onboarding } = usePublicJson('/curation/onboarding-flow.v1.json')
  const { data: pricingData } = usePublicJson('/curation/pricing-matrix.v1.json')

  const rawReturnTo = searchParams.get('returnTo')
  const returnTo = useMemo(() => sanitizeReturnTo(rawReturnTo), [rawReturnTo])
  const paymentUrl = import.meta.env.VITE_STRIPE_PAYMENT_URL
  const proPlan = useMemo(
    () => (pricingData?.plans || []).find((plan) => plan.id === 'pro'),
    [pricingData],
  )

  const handleCheckout = () => {
    storePendingReturnTo(returnTo)

    if (paymentUrl) {
      window.location.assign(paymentUrl)
    }
  }

  return (
    <div className="space-y-8 lg:space-y-10" data-operator-surface>
      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/70 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="inline-flex items-center gap-2 rounded-full border border-terminal-700 bg-terminal-900/80 px-3 py-1 text-xs uppercase tracking-[0.22em] text-terminal-400">
          <ShieldCheck className="size-3.5 text-emerald-300" />
          Authored preview set
        </div>
        <h1 className="mt-5 text-4xl font-semibold tracking-tight text-terminal-100 sm:text-5xl">Activate Pro, then connect your backend.</h1>
        <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
          The upgrade flow is front-end-only in this release. Checkout success only marks this browser as Pro locally.
          Backend connection is a second step after checkout, and secure entitlement enforcement is still out of scope.
        </p>
        {returnTo && (
          <div className="mt-4 rounded-2xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
            After activation, MCAS will try to send you back to <span className="font-mono">{returnTo}</span>.
          </div>
        )}
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <FeedbackOnset 
            onClick={handleCheckout} 
            latencyLabel="Upgrade"
          >
            <button
              type="button"
              disabled={!paymentUrl || isPaid}
              className="btn btn-primary btn-sm disabled:cursor-not-allowed disabled:opacity-60"
            >
              {isPaid ? 'Already Activated Locally' : 'Open Hosted Checkout'}
              <ExternalLink className="size-4" />
            </button>
          </FeedbackOnset>
          <Link to="/pricing" className="btn btn-secondary btn-sm">
            Back To Pricing
          </Link>
        </div>
        {!paymentUrl && (
          <p className="mt-4 text-sm text-terminal-400">
            No hosted checkout URL is configured in <code className="text-amber-300">VITE_STRIPE_PAYMENT_URL</code>.
            Leave the CTA disabled and direct the user to support until the payment link exists.
          </p>
        )}
      </section>

      {proPlan && (
        <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6 sm:p-8">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Value Continuity</div>
          <h2 className="mt-2 text-2xl font-semibold text-terminal-100">{proPlan.label} • {proPlan.price_label}</h2>
          <p className="mt-3 text-sm leading-6 text-terminal-400">{proPlan.tagline}</p>
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

      {onboarding?.trust_copy && (
        <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6 sm:p-8">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Trust Copy</div>
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

      <section className="rounded-[2rem] border border-terminal-700 bg-gradient-to-r from-terminal-800 via-terminal-800 to-amber-900/20 p-6 sm:p-8">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Next Step</div>
            <h2 className="mt-2 text-2xl font-semibold text-terminal-100">Activate locally, then go to Settings and point the UI at your backend.</h2>
          </div>
          <Link to="/settings" className="btn btn-secondary btn-sm">
            Open Settings
            <ArrowRight className="size-4" />
          </Link>
        </div>
      </section>
    </div>
  )
}
