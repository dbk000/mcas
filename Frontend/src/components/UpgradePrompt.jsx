import { Link, useLocation } from 'react-router-dom'
import { ArrowRight, LockKeyhole } from 'lucide-react'

/**
 * Contextual in-page upgrade prompt shown when a free user encounters a paid feature.
 * Keeps the user on the page so they see the layout and understand what they're missing.
 */
export default function UpgradePrompt({ feature = 'This feature' }) {
  const location = useLocation()
  const returnTo = `${location.pathname}${location.search}`
  const pricingHref = `/pricing?returnTo=${encodeURIComponent(returnTo)}`

  return (
    <div className="mx-auto max-w-lg rounded-2xl border border-amber-500/20 bg-terminal-800/80 p-6 text-center">
      <LockKeyhole className="mx-auto size-8 text-amber-400" />
      <h3 className="mt-3 text-lg font-semibold text-terminal-100">
        {feature} requires MCAS Pro
      </h3>
      <p className="mt-2 text-sm text-terminal-400">
        Upgrade to unlock live signals, unlimited simulations, and full platform access.
      </p>
      <Link
        to={pricingHref}
        className="btn btn-primary btn-sm mt-4 inline-flex items-center gap-2"
      >
        See MCAS Pro
        <ArrowRight className="size-4" />
      </Link>
    </div>
  )
}
