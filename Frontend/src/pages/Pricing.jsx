import { Link } from 'react-router-dom'
import { ArrowRight, CheckCircle2 } from 'lucide-react'
import { useSubscription } from '../context/SubscriptionContext'
import { FRONTEND_ANALYTICS_EVENTS, emitCtaEvent } from '../utils/analytics'
import { usePageMeta } from '../utils/pageMeta'
import { usePublicJson } from '../utils/publicJson'

function sourceLabel(sourceStatus) {
  if (sourceStatus === 'live') return 'Live feed'
  if (sourceStatus === 'degraded') return 'Showing cached pricing'
  return 'Pricing'
}

function emitPricingClick({ href, label, placement, tier, sourceStatus }) {
  emitCtaEvent(FRONTEND_ANALYTICS_EVENTS.pricingCtaClick, {
    surface: 'pricing',
    placement,
    source_status: sourceStatus,
    ctaId: placement,
    ctaLabel: label,
    href,
    tier,
  })
}

export default function Pricing() {
  usePageMeta('pricing')

  const { tier } = useSubscription()
  const { data: pricingData, error } = usePublicJson('/curation/pricing-matrix.v1.json')
  const { data: proofPack } = usePublicJson('/curation/proof-pack.v1.json')
  const sourceStatus = error ? 'degraded' : (pricingData?.source === 'live' ? 'live' : 'placeholder')
  const plans = pricingData?.plans || []
  const valueStack = pricingData?.value_stack || []
  const comparisonRows = pricingData?.comparison_rows || []
  const faq = pricingData?.faq || []
  const honestPf = proofPack?.honest_pf_demo

  return (
    <div className="space-y-8 lg:space-y-10" data-operator-surface>
      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/70 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">{sourceLabel(sourceStatus)}</div>
        <h1 className="mt-4 max-w-4xl text-4xl font-semibold tracking-tight text-terminal-100 sm:text-5xl">
          One plan. Full access. Proof of every decision.
        </h1>
        <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
          Explore verified historical winners for free. Subscribe to Pro for live signals, full audit trails, and complete platform access.
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Link
            to="/upgrade"
            className="btn btn-primary btn-sm"
            onClick={() => emitPricingClick({ href: '/upgrade', label: 'Upgrade', placement: 'hero_primary', tier, sourceStatus })}
          >
            Upgrade
            <ArrowRight className="size-4" />
          </Link>
          <Link
            to="/winners"
            className="btn btn-secondary btn-sm"
            onClick={() => emitPricingClick({ href: '/winners', label: 'See Winners', placement: 'hero_secondary', tier, sourceStatus })}
          >
            See Free Winners
          </Link>
        </div>
      </section>

      {valueStack.length > 0 && (
        <section className="grid gap-4 lg:grid-cols-3">
          {valueStack
            .slice()
            .sort((a, b) => (a.priority_rank || 999) - (b.priority_rank || 999))
            .map((item) => (
              <div key={item.id} className="rounded-[1.75rem] border border-terminal-700 bg-terminal-800/60 p-6">
                <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">What you get</div>
                <h2 className="mt-3 text-xl font-semibold text-terminal-100">{item.headline}</h2>
                <p className="mt-3 text-sm leading-6 text-terminal-400">{item.body}</p>
              </div>
            ))}
        </section>
      )}

      <section className="grid gap-4 lg:grid-cols-2">
        {plans.map((plan) => {
          const isPrimary = plan.id === 'pro'
          return (
            <article
              key={plan.id}
              className={`rounded-[2rem] border p-6 sm:p-8 ${
                isPrimary
                  ? 'from-amber-500/12 border-amber-500/40 bg-gradient-to-b to-terminal-800/80'
                  : 'border-terminal-700 bg-terminal-800/60'
              }`}
            >
              <div className="flex items-center justify-between gap-4">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">{plan.id}</div>
                  <h2 className="mt-2 text-3xl font-semibold text-terminal-100">{plan.label}</h2>
                </div>
                <div className="text-3xl font-semibold text-terminal-100">{plan.price_label}</div>
              </div>
              <p className="mt-4 text-sm leading-6 text-terminal-400">{plan.tagline}</p>
              <Link
                to={plan.cta_href}
                className={`btn btn-sm mt-6 ${isPrimary ? 'btn-primary' : 'btn-secondary'}`}
                onClick={() => emitPricingClick({
                  href: plan.cta_href,
                  label: plan.cta_label,
                  placement: `plan_${plan.id}`,
                  tier,
                  sourceStatus,
                })}
              >
                {plan.cta_label}
                {isPrimary && <ArrowRight className="size-4" />}
              </Link>
            </article>
          )
        })}
      </section>

      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6 sm:p-8">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Feature comparison</div>
            <h2 className="mt-2 text-2xl font-semibold text-terminal-100">Free vs Pro — what's included</h2>
          </div>
          <Link
            to="/upgrade"
            className="btn btn-primary btn-sm"
            onClick={() => emitPricingClick({ href: '/upgrade', label: 'Upgrade', placement: 'matrix_primary', tier, sourceStatus })}
          >
            Upgrade
            <ArrowRight className="size-4" />
          </Link>
        </div>

        <div className="mt-6 overflow-x-auto">
          <table className="w-full min-w-[680px] text-sm">
            <thead>
              <tr className="border-b border-terminal-700 text-left text-terminal-500">
                <th className="pb-3 pr-6 font-medium">Feature</th>
                <th className="px-6 pb-3 font-medium">Free</th>
                <th className="pb-3 pl-6 font-medium text-amber-200">Pro</th>
              </tr>
            </thead>
            <tbody>
              {comparisonRows.map((row) => (
                <tr key={row.feature} className="border-b border-terminal-800/80">
                  <td className="py-3 pr-6 text-terminal-200">{row.feature}</td>
                  <td className="px-6 py-3 text-terminal-400">{row.free}</td>
                  <td className="py-3 pl-6 text-terminal-200">{row.pro}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6 sm:p-8">
        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">How we measure performance</div>
        <h2 className="mt-2 text-2xl font-semibold text-terminal-100">Two numbers, not one. We show both the headline figure and the stricter daily average.</h2>
        <div className="mt-5 grid gap-4 sm:grid-cols-2">
          <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4">
            <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Aggregate PF</div>
            <div className="mt-2 text-3xl font-semibold text-terminal-100">{honestPf?.aggregate_pf ?? '—'}</div>
          </div>
          <div className="rounded-2xl border border-terminal-700 bg-terminal-900/80 p-4">
            <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Avg Daily PF</div>
            <div className="mt-2 text-3xl font-semibold text-terminal-100">{honestPf?.avg_daily_pf ?? '—'}</div>
          </div>
        </div>
        <p className="mt-4 text-sm leading-6 text-terminal-400">
          {honestPf?.method_note || 'Daily profit factor is capped at 10.0 for days with no losing trades. All days are included in the average.'}
        </p>
      </section>

      {faq.length > 0 && (
        <section className="grid gap-4 lg:grid-cols-3">
          {faq.map((item) => (
            <div key={item.q} className="rounded-[1.75rem] border border-terminal-700 bg-terminal-800/60 p-6">
              <div className="flex items-start gap-3">
                <CheckCircle2 className="mt-0.5 size-5 text-emerald-300" />
                <div>
                  <h3 className="text-lg font-semibold text-terminal-100">{item.q}</h3>
                  <p className="mt-3 text-sm leading-6 text-terminal-400">{item.a}</p>
                </div>
              </div>
            </div>
          ))}
        </section>
      )}
    </div>
  )
}
