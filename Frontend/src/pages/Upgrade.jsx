import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowRight, ExternalLink, Loader2, ShieldCheck } from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useSubscription } from '../context/SubscriptionContext'
import { usePageMeta } from '../utils/pageMeta'
import { usePublicJson } from '../utils/publicJson'
import { sanitizeReturnTo, storePendingReturnTo } from '../utils/returnTo'

export default function Upgrade() {
  usePageMeta('upgrade')

  const [searchParams] = useSearchParams()
  const { post, config } = useApi()
  const { isPaid } = useSubscription()
  const { data: onboarding } = usePublicJson('/curation/onboarding-flow.v1.json')
  const { data: pricingData } = usePublicJson('/curation/pricing-matrix.v1.json')
  const [checkoutLoading, setCheckoutLoading] = useState(false)
  const [checkoutError, setCheckoutError] = useState(null)

  const rawReturnTo = searchParams.get('returnTo')
  const returnTo = useMemo(() => sanitizeReturnTo(rawReturnTo), [rawReturnTo])
  const proPlan = useMemo(
    () => (pricingData?.plans || []).find((plan) => plan.id === 'pro'),
    [pricingData],
  )

  const handleCheckout = async () => {
    storePendingReturnTo(returnTo)
    setCheckoutError(null)
    setCheckoutLoading(true)

    try {
      const origin = window.location.origin
      const data = await post('/v1/stripe/create-checkout-session', {
        price_id: searchParams.get('price') || undefined,
        success_url: `${origin}/checkout/success?session_id={CHECKOUT_SESSION_ID}`,
        cancel_url: `${origin}/checkout/cancel`,
      })

      if (data?.checkout_url) {
        window.location.assign(data.checkout_url)
      } else {
        setCheckoutError('No checkout URL returned. Please try again.')
        setCheckoutLoading(false)
      }
    } catch (err) {
      setCheckoutError(err?.message || 'Failed to start checkout. Please try again.')
      setCheckoutLoading(false)
    }
  }

  return (
    <div className="space-y-8 lg:space-y-10" data-operator-surface>
      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/70 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="inline-flex items-center gap-2 rounded-full border border-terminal-700 bg-terminal-900/80 px-3 py-1 text-xs uppercase tracking-[0.22em] text-terminal-400">
          <ShieldCheck className="size-3.5 text-emerald-300" />
          Secure Checkout
        </div>
        <h1 className="mt-5 text-4xl font-semibold tracking-tight text-terminal-100 sm:text-5xl">
          Unlock MCAS Pro
        </h1>
        <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
          Subscribe to MCAS Pro for live signals, verified audit trails, unlimited simulations,
          and paper trading. Billed securely via Stripe.
        </p>
        {returnTo && (
          <div className="mt-4 rounded-2xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
            After checkout you'll be returned to <span className="font-mono">{returnTo}</span>.
          </div>
        )}
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <button
            type="button"
            disabled={isPaid || checkoutLoading || !config.baseUrl}
            onClick={handleCheckout}
            className="btn btn-primary btn-sm disabled:cursor-not-allowed disabled:opacity-60"
          >
            {checkoutLoading ? (
              <>
                <Loader2 className="size-4 animate-spin" />
                Redirecting to Stripe...
              </>
            ) : isPaid ? (
              'Already Subscribed'
            ) : (
              <>
                Subscribe to Pro
                <ExternalLink className="size-4" />
              </>
            )}
          </button>
          <Link to="/pricing" className="btn btn-secondary btn-sm">
            Back To Pricing
          </Link>
        </div>
        {checkoutError && (
          <p className="mt-4 text-sm text-red-400">{checkoutError}</p>
        )}
      </section>

      {proPlan && (
        <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6 sm:p-8">
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">What You Get</div>
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
          <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">How It Works</div>
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
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">After Checkout</div>
            <h2 className="mt-2 text-2xl font-semibold text-terminal-100">
              {isPaid ? 'You\'re subscribed. Head to the app.' : 'After payment, connect your MCAS account in Settings to activate.'}
            </h2>
          </div>
          <Link to={isPaid ? '/app' : '/settings'} className="btn btn-secondary btn-sm">
            {isPaid ? 'Open App' : 'Open Settings'}
            <ArrowRight className="size-4" />
          </Link>
        </div>
      </section>
    </div>
  )
}
