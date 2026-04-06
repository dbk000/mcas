import { Link } from 'react-router-dom'
import { ArrowRight, Clock3, ShieldCheck } from 'lucide-react'
import { useSubscription } from '../context/SubscriptionContext'
import { FRONTEND_ANALYTICS_EVENTS, emitCtaEvent } from '../utils/analytics'
import { usePageMeta } from '../utils/pageMeta'
import { usePublicJson } from '../utils/publicJson'

function sourceLabel(sourceStatus) {
  if (sourceStatus === 'live') return 'Live feed'
  if (sourceStatus === 'degraded') return 'Showing curated archive'
  return 'Curated archive'
}

function isValidWinner(item) {
  return Boolean(
    item?.id &&
    item?.symbol &&
    item?.timeframe &&
    item?.direction &&
    item?.date &&
    item?.why_it_mattered
  )
}

function emitWinnerClick({ href, label, placement, tier, sourceStatus }) {
  emitCtaEvent(FRONTEND_ANALYTICS_EVENTS.winnersCtaClick, {
    surface: 'winners',
    placement,
    source_status: sourceStatus,
    ctaId: placement,
    ctaLabel: label,
    href,
    tier,
  })
}

export default function Winners() {
  usePageMeta('winners')

  const { tier } = useSubscription()
  const { data, loading, error } = usePublicJson('/curation/winners.v1.json')
  const sourceStatus = error ? 'degraded' : (data?.source === 'live' ? 'live' : 'placeholder')
  const strategies = Array.isArray(data?.strategies) ? data.strategies : []

  if (!loading && strategies.length === 0) {
    return (
      <div className="rounded-[2rem] border border-terminal-700 bg-terminal-800/70 p-8 text-center">
        <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Winners Archive</div>
        <h1 className="mt-3 text-3xl font-semibold text-terminal-100">The winners archive is temporarily unavailable.</h1>
        <p className="mt-3 text-sm text-terminal-400">
          Check back soon, or explore pricing to unlock live opportunities.
        </p>
        <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
          <Link
            to="/upgrade"
            className="btn btn-primary btn-sm"
            onClick={() => emitWinnerClick({ href: '/upgrade', label: 'Upgrade', placement: 'empty_primary', tier, sourceStatus })}
          >
            Upgrade
            <ArrowRight className="size-4" />
          </Link>
          <Link
            to="/pricing"
            className="btn btn-secondary btn-sm"
            onClick={() => emitWinnerClick({ href: '/pricing', label: 'See Pricing', placement: 'empty_secondary', tier, sourceStatus })}
          >
            See Pricing
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-8 lg:space-y-10">
      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/70 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="inline-flex items-center gap-2 rounded-full border border-terminal-700 bg-terminal-900/80 px-3 py-1 text-xs uppercase tracking-[0.22em] text-terminal-400">
              <ShieldCheck className="size-3.5 text-emerald-300" />
              {sourceLabel(sourceStatus)}
            </div>
            <h1 className="mt-5 text-4xl font-semibold tracking-tight text-terminal-100 sm:text-5xl">Verified winners — ranked by strategy and market conditions.</h1>
            <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
              Historical examples of trades MCAS identified before they moved. Subscribe to Pro for live signals.
            </p>
            <p className="mt-3 text-sm text-terminal-500">
              Not financial advice. Historical examples are selected for quality. Pro subscribers get live opportunities with full audit trails.
            </p>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <Link
              to="/upgrade"
              className="btn btn-primary btn-sm"
              onClick={() => emitWinnerClick({ href: '/upgrade', label: 'Unlock Live Routes', placement: 'hero_primary', tier, sourceStatus })}
            >
              Get Live Signals
              <ArrowRight className="size-4" />
            </Link>
            <Link
              to="/pricing"
              className="btn btn-secondary btn-sm"
              onClick={() => emitWinnerClick({ href: '/pricing', label: 'Compare Plans', placement: 'hero_secondary', tier, sourceStatus })}
            >
              See Pricing
            </Link>
          </div>
        </div>
      </section>

      {strategies.map((strategy, index) => {
        const validItems = (strategy.items || []).filter(isValidWinner)
        if (validItems.length === 0) {
          return null
        }

        return (
          <section key={strategy.setup_id} className="space-y-4 rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6 sm:p-8">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Rank #{index + 1} • {strategy.setup_id}</div>
                <h2 className="mt-2 text-2xl font-semibold text-terminal-100">{strategy.label}</h2>
                <p className="mt-2 max-w-3xl text-sm leading-6 text-terminal-400">{strategy.description}</p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {(strategy.market_fit || []).map((fit) => (
                    <span key={fit} className="rounded-full border border-terminal-700 bg-terminal-900/80 px-3 py-1 text-xs uppercase tracking-[0.16em] text-terminal-400">
                      {fit}
                    </span>
                  ))}
                </div>
              </div>
              <Link
                to={`/upgrade?setup=${encodeURIComponent(strategy.setup_id)}`}
                className="btn btn-secondary btn-sm"
                onClick={() => emitWinnerClick({
                  href: `/upgrade?setup=${encodeURIComponent(strategy.setup_id)}`,
                  label: `Unlock ${strategy.setup_id}`,
                  placement: `section_${strategy.setup_id}`,
                  tier,
                  sourceStatus,
                })}
              >
                Unlock this strategy
              </Link>
            </div>

            <div className="grid gap-4 lg:grid-cols-3">
              {validItems.map((item) => (
                <article key={item.id} className="flex h-full flex-col rounded-3xl border border-terminal-700 bg-terminal-900/85 p-5">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="text-2xl font-semibold text-terminal-100">{item.symbol}</div>
                      <div className="mt-1 text-xs uppercase tracking-[0.2em] text-terminal-500">
                        {item.timeframe} • {String(item.direction).toUpperCase()}
                      </div>
                    </div>
                    <div className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-3 py-1 text-sm font-semibold text-emerald-200">
                      {item.outcome_pct > 0 ? '+' : ''}{item.outcome_pct}%
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
                    <div className="rounded-xl border border-terminal-700 bg-terminal-800/80 p-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-terminal-500">Quality</div>
                      <div className="mt-1 text-lg font-semibold text-terminal-100">{item.quality_score}</div>
                    </div>
                    <div className="rounded-xl border border-terminal-700 bg-terminal-800/80 p-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-terminal-500">Holding Bars</div>
                      <div className="mt-1 text-lg font-semibold text-terminal-100">{item.holding_bars}</div>
                    </div>
                  </div>

                  <div className="mt-4 flex items-center gap-2 text-sm text-terminal-500">
                    <Clock3 className="size-4" />
                    {item.date}
                    <span className="rounded-full bg-terminal-800 px-2 py-1 text-xs text-terminal-400">{item.regime || 'Regime unavailable'}</span>
                  </div>

                  <p className="mt-4 text-sm leading-6 text-terminal-300">{item.why_it_mattered}</p>

                  <ul className="mt-4 space-y-2 text-sm text-terminal-400">
                    {(item.notes || []).map((note) => (
                      <li key={note} className="rounded-xl border border-terminal-700 bg-terminal-800/70 px-3 py-2">
                        {note}
                      </li>
                    ))}
                  </ul>

                  <div className="mt-auto pt-5">
                    <div className="rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                      {item.cta_tease}
                    </div>
                    <Link
                      to={`/upgrade?winner=${encodeURIComponent(item.id)}`}
                      className="btn btn-primary btn-sm mt-4 w-full"
                      onClick={() => emitWinnerClick({
                        href: `/upgrade?winner=${encodeURIComponent(item.id)}`,
                        label: 'Unlock Live Timing',
                        placement: `card_${item.id}`,
                        tier,
                        sourceStatus,
                      })}
                    >
                      Get Live Signals
                      <ArrowRight className="size-4" />
                    </Link>
                  </div>
                </article>
              ))}
            </div>
          </section>
        )
      })}
    </div>
  )
}
