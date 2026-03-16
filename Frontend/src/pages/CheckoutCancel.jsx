import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, RotateCcw } from 'lucide-react'
import { FRONTEND_ANALYTICS_EVENTS, emitAnalyticsEvent } from '../utils/analytics'
import { usePageMeta } from '../utils/pageMeta'

export default function CheckoutCancel() {
  usePageMeta('checkoutCancel')

  useEffect(() => {
    emitAnalyticsEvent(FRONTEND_ANALYTICS_EVENTS.checkoutCancelLanded, {
      surface: 'checkout_cancel',
      placement: 'page_load',
      source_status: 'placeholder',
    })
  }, [])

  return (
    <div className="space-y-8">
      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/70 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="inline-flex items-center gap-2 rounded-full border border-terminal-700 bg-terminal-900/80 px-3 py-1 text-xs uppercase tracking-[0.22em] text-terminal-400">
          <RotateCcw className="size-3.5 text-amber-300" />
          Checkout Canceled
        </div>
        <h1 className="mt-5 text-4xl font-semibold tracking-tight text-terminal-100 sm:text-5xl">The checkout flow stopped before local activation.</h1>
        <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
          Nothing changed in your local tier state. Review the pricing stack again, or keep browsing curated winners until you are ready to restart checkout.
        </p>
        <div className="mt-6 flex flex-col gap-3 sm:flex-row">
          <Link to="/pricing" className="btn btn-primary btn-sm">
            Return To Pricing
            <ArrowRight className="size-4" />
          </Link>
          <Link to="/winners" className="btn btn-secondary btn-sm">
            Review Winners
          </Link>
        </div>
      </section>
    </div>
  )
}
