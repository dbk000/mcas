import { Link } from 'react-router-dom'
import { ArrowRight, ShieldCheck, ScanLine, TimerReset, ChevronRight, Layers3, Radio } from 'lucide-react'
import { Badge, Card, CardContent, CardHeader, CardTitle } from '../../components/ui'
import { ANALYTICS_EVENTS, trackCtaClick } from '../../utils/analytics'
import useLiveFeed from '../../hooks/useLiveFeed'

// --- Pills ---
export function HeroPills() {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="rounded-full border border-amber-600/40 bg-amber-600/15 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-amber-600">
        Live Strategies
      </span>
      <span className="rounded-full border border-emerald-700/40 bg-emerald-700/15 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-emerald-700">
        Glass Box Verified
      </span>
    </div>
  )
}

// --- Headline ---
export function HeroHeadline({ className = '' }) {
  return (
    <h1 className={`text-xl font-semibold leading-tight text-terminal-100 sm:text-4xl lg:text-6xl ${className}`}>
      Comprehensive realtime intelligence that shows the flattering number, the stricter number, and the timing integrity behind both.
    </h1>
  )
}

// --- Body copy ---
export function HeroCopy() {
  return (
    <p className="max-w-3xl text-[13px] leading-5 text-terminal-300 sm:text-lg">
      MCAS splits the public archive from the paid operator app. Free pages show ranked historical winners and verified proof strips. Pro unlocks live strategy ladders, Glass Box causality, and action-window context inside the app shell.
    </p>
  )
}

// --- Proof badges ---
export function ProofBadges({ proofPack }) {
  return (
    <div className="rounded-3xl border border-terminal-800 bg-terminal-900/85 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex items-center gap-1.5 rounded-full bg-terminal-800 px-2.5 py-1 text-[9px] text-terminal-300">
          <ShieldCheck className="size-3 text-emerald-400" />
          {proofPack?.glass_box_walkthrough?.headline || 'Causality verified'}
        </div>
        <div className="inline-flex items-center gap-1.5 rounded-full bg-terminal-800 px-2.5 py-1 text-[9px] text-terminal-300">
          <TimerReset className="size-3 text-amber-300" />
          {proofPack?.honest_pf_demo?.days_excluded_infinite_profitable ?? 0} perfect days excluded from average
        </div>
      </div>
    </div>
  )
}

// --- CTA buttons ---
export function HeroCTAs({ primaryHref, primaryLabel, isPaid, tier }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row">
      <Link
        to={primaryHref}
        data-testid="landing-primary-cta"
        onClick={() => trackCtaClick(ANALYTICS_EVENTS.LANDING_CTA_CLICK, {
          surface: 'landing', placement: 'hero_primary', sourceStatus: 'placeholder',
          ctaId: 'landing-open-live', ctaLabel: primaryLabel, href: primaryHref, tier,
        })}
        className="btn btn-primary h-10 rounded-full px-4 text-sm sm:h-12 sm:px-5 sm:text-base"
      >
        {primaryLabel}
        <ArrowRight className="size-4" />
      </Link>
      <Link
        to="/winners"
        onClick={() => trackCtaClick(ANALYTICS_EVENTS.LANDING_CTA_CLICK, {
          surface: 'landing', placement: 'hero_secondary', sourceStatus: 'placeholder',
          ctaId: 'landing-winners', ctaLabel: 'Inspect top winners', href: '/winners', tier,
        })}
        className="btn btn-secondary h-10 rounded-full px-4 text-sm sm:h-12 sm:px-5 sm:text-base"
      >
        Inspect top winners
      </Link>
      <Link
        to="/pricing"
        onClick={() => trackCtaClick(ANALYTICS_EVENTS.LANDING_CTA_CLICK, {
          surface: 'landing', placement: 'hero_tertiary', sourceStatus: 'placeholder',
          ctaId: 'landing-pricing', ctaLabel: 'See pricing', href: '/pricing', tier,
        })}
        className="inline-flex h-10 items-center rounded-full px-3 text-xs text-terminal-300 transition-colors hover:bg-terminal-900 hover:text-terminal-100 sm:px-4 sm:text-sm"
      >
        See pricing
        <ChevronRight className="ml-1 size-4" />
      </Link>
    </div>
  )
}

// --- Disclaimer ---
export function Disclaimer() {
  return (
    <p className="text-[10px] leading-4 text-terminal-500 sm:text-xs">
      Not financial advice. Public pages show selected historical examples. Live app functionality remains access-limited and depends on local Pro activation plus your API configuration.
    </p>
  )
}

// --- Single metric card ---
export function HeroMetric({ metric }) {
  const content = (
    <>
      <p className="text-[10px] uppercase tracking-[0.18em] text-terminal-500">{metric.label}</p>
      <p className="mt-2 font-mono text-xl text-amber-300">{metric.value}</p>
      <p className="mt-1 text-[11px] text-terminal-400">{metric.subtext}</p>
    </>
  )
  if (metric.link) {
    return (
      <Link to={metric.link} className="block rounded-2xl border border-terminal-800 bg-terminal-900/85 p-3 transition-colors hover:border-amber-500/30">
        {content}
      </Link>
    )
  }
  return <div className="rounded-2xl border border-terminal-800 bg-terminal-900/85 p-3">{content}</div>
}

// --- Metrics grid (2x2) ---
export function MetricsGrid({ proofPack }) {
  return (
    <div className="grid gap-2 sm:grid-cols-2">
      {(proofPack?.hero_metrics || []).map((metric) => (
        <HeroMetric key={metric.id} metric={metric} />
      ))}
    </div>
  )
}

// --- Metrics inline (horizontal ticker) ---
export function MetricsTicker({ proofPack }) {
  return (
    <div className="flex flex-wrap gap-3">
      {(proofPack?.hero_metrics || []).map((metric) => (
        <div key={metric.id} className="flex items-baseline gap-2 rounded-full border border-terminal-800 bg-terminal-900/85 px-3 py-1.5">
          <span className="text-[9px] uppercase tracking-[0.18em] text-terminal-500">{metric.label}</span>
          <span className="font-mono text-sm text-amber-300">{metric.value}</span>
        </div>
      ))}
    </div>
  )
}

// --- Performance snapshot strip ---
export function PerformanceStrip({ proofPack }) {
  return (
    <Card className="rounded-3xl border-terminal-800 bg-terminal-900/90">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="flex items-center gap-2 text-sm text-terminal-100">
          <ScanLine className="size-3.5 text-amber-300" />
          Performance snapshot
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-4 pt-0">
        <div className="grid gap-2 sm:grid-cols-3">
          <div className="rounded-xl bg-terminal-950/80 p-3">
            <p className="text-[9px] uppercase tracking-[0.18em] text-terminal-500">Aggregate PF</p>
            <p className="mt-1 font-mono text-lg text-terminal-100">{proofPack?.honest_pf_demo?.aggregate_pf?.toFixed(2) ?? '—'}</p>
          </div>
          <div className="rounded-xl bg-terminal-950/80 p-3">
            <p className="text-[9px] uppercase tracking-[0.18em] text-terminal-500">Avg Daily PF</p>
            <p className="mt-1 font-mono text-lg text-amber-300">{proofPack?.honest_pf_demo?.avg_daily_pf?.toFixed(2) ?? '—'}</p>
          </div>
          <div className="rounded-xl bg-terminal-950/80 p-3">
            <p className="text-[9px] uppercase tracking-[0.18em] text-terminal-500">Days</p>
            <p className="mt-1 font-mono text-lg text-terminal-100">{proofPack?.honest_pf_demo?.days_counted ?? 0}</p>
          </div>
        </div>
        <p className="text-[11px] leading-4 text-terminal-400">{proofPack?.honest_pf_demo?.method_note}</p>
      </CardContent>
    </Card>
  )
}

// --- Static Glass Box replay (fallback) ---
function StaticGlassBoxReplay({ proofPack }) {
  return (
    <Card className="rounded-3xl border-terminal-800 bg-terminal-900/85">
      <CardHeader className="p-4">
        <CardTitle className="flex items-center gap-2 text-sm text-terminal-100">
          <Layers3 className="size-3.5 text-emerald-300" />
          Sample Glass Box replay
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-4 pt-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="info" className="text-[9px]">{proofPack?.glass_box_walkthrough?.setup_id}</Badge>
          <Badge className="text-[9px]">{proofPack?.glass_box_walkthrough?.symbol}</Badge>
          <Badge variant="success" className="text-[9px]">{proofPack?.glass_box_walkthrough?.causality_status}</Badge>
        </div>
        <h2 className="text-base font-semibold text-terminal-100">{proofPack?.glass_box_walkthrough?.title}</h2>
        <p className="text-xs leading-5 text-terminal-400">{proofPack?.glass_box_walkthrough?.headline}</p>
        <div className="space-y-2">
          {(proofPack?.glass_box_walkthrough?.timeline || []).map((item) => (
            <div key={`${item.phase}-${item.ts}`} className="rounded-xl border border-terminal-800 bg-terminal-950/80 p-2.5">
              <div className="flex items-center justify-between gap-2">
                <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-terminal-300">{item.phase}</p>
                <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[9px] uppercase tracking-[0.18em] text-emerald-300">
                  {item.state}
                </span>
              </div>
              <p className="mt-1 text-[10px] text-terminal-500">{new Date(item.ts).toLocaleString()}</p>
              <p className="mt-1 text-xs leading-4 text-terminal-400">{item.detail}</p>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}

// --- Feed event row ---
function FeedEvent({ event }) {
  const opp = event.opportunity
  const pipeline = event.pipeline
  const sim = event.simulation
  const age = opp.created_ts
    ? Math.round((Date.now() - new Date(opp.created_ts).getTime()) / 1000)
    : null

  const formatAge = (s) => {
    if (s == null) return ''
    if (s < 60) return `${s}s ago`
    if (s < 3600) return `${Math.floor(s / 60)}m ago`
    return `${Math.floor(s / 3600)}h ago`
  }

  const hasTrace = Boolean(pipeline?.trace_hash)

  return (
    <div className="feed-enter rounded-xl border border-terminal-800 bg-terminal-950/80 p-2.5">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant="info" className="text-[9px]">{opp.setup_id}</Badge>
          <Badge className="text-[9px]">{opp.timeframe}</Badge>
          <span className="rounded-full border border-terminal-700 bg-terminal-900 px-2 py-0.5 text-[9px] text-terminal-400">
            crypto
          </span>
          {hasTrace && (
            <span className="rounded-full bg-emerald-500/10 px-2 py-0.5 text-[9px] uppercase tracking-[0.18em] text-emerald-300">
              verified
            </span>
          )}
        </div>
        <span className="text-[10px] tabular-nums text-terminal-500">{formatAge(age)}</span>
      </div>
      {pipeline && (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {(pipeline.gates || []).slice(0, 4).map((g, i) => (
            <span
              key={i}
              className={`rounded px-1.5 py-0.5 text-[9px] ${
                g.passed ? 'bg-emerald-500/10 text-emerald-300' : 'bg-red-500/10 text-red-400'
              }`}
            >
              {g.name}
            </span>
          ))}
        </div>
      )}
      {sim?.exit_reason && (
        <p className="mt-1 text-[10px] text-terminal-500">
          Exit: {sim.exit_reason}
        </p>
      )}
    </div>
  )
}

// --- Live Pipeline Feed ---
function LivePipelineFeed({ events, isLive }) {
  return (
    <Card className="rounded-3xl border-terminal-800 bg-terminal-900/85">
      <CardHeader className="p-4">
        <CardTitle className="flex items-center justify-between text-sm text-terminal-100">
          <span className="flex items-center gap-2">
            <Radio className="size-3.5 text-amber-300" />
            Pipeline feed
          </span>
          <span
            className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[9px] font-semibold uppercase tracking-[0.18em] ${
              isLive
                ? 'bg-emerald-500/15 text-emerald-300'
                : 'bg-amber-500/15 text-amber-300'
            }`}
          >
            <span className={`size-1.5 rounded-full ${isLive ? 'animate-pulse bg-emerald-400' : 'bg-amber-400'}`} />
            {isLive ? 'Live' : 'Delayed'}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent className="max-h-[400px] space-y-2 overflow-y-auto p-4 pt-0">
        {events.map((event) => (
          <FeedEvent key={event.opportunity.id} event={event} />
        ))}
        {events.length === 0 && (
          <p className="py-6 text-center text-xs text-terminal-500">No recent pipeline events</p>
        )}
      </CardContent>
    </Card>
  )
}

// --- Glass Box replay (live with static fallback) ---
export function GlassBoxReplay({ proofPack }) {
  const { events, isLive } = useLiveFeed()

  if (events.length > 0) {
    return <LivePipelineFeed events={events} isLive={isLive} />
  }

  return <StaticGlassBoxReplay proofPack={proofPack} />
}

// --- Evidence cards ---
export function EvidenceCards({ proofPack }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
      {(proofPack?.evidence_blocks || []).map((block) => (
        <Card key={block.id} className="h-full rounded-3xl border-terminal-800 bg-terminal-900/85">
          <CardContent className="space-y-2 p-4">
            <p className="text-[10px] uppercase tracking-[0.22em] text-terminal-500">{block.eyebrow}</p>
            <h3 className="text-base font-semibold text-terminal-100">{block.headline}</h3>
            <p className="text-xs leading-5 text-terminal-400">{block.body}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

// --- Winners preview ---
export function WinnersPreview({ previewStrategies, tier }) {
  return (
    <section className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[10px] uppercase tracking-[0.22em] text-terminal-500">Preview continuity</p>
          <h2 className="mt-1 text-xl font-semibold text-terminal-100 sm:text-2xl">Ranked winners stay strategy-native and dense.</h2>
        </div>
        <Link
          to="/winners"
          onClick={() => trackCtaClick(ANALYTICS_EVENTS.LANDING_CTA_CLICK, {
            surface: 'landing', placement: 'winners_teaser', sourceStatus: 'placeholder',
            ctaId: 'landing-preview-winners', ctaLabel: 'View all winners', href: '/winners', tier,
          })}
          className="inline-flex items-center gap-1.5 text-xs text-amber-300 transition-colors hover:text-amber-200"
        >
          View all winners
          <ArrowRight className="size-3.5" />
        </Link>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {previewStrategies.map((strategy, index) => (
          <Card key={strategy.setup_id} className="rounded-3xl border-terminal-800 bg-terminal-900/85">
            <CardContent className="space-y-4 p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-[10px] uppercase tracking-[0.22em] text-terminal-500">Ranked strategy {index + 1}</p>
                  <h3 className="mt-1 text-lg font-semibold text-terminal-100">{strategy.label}</h3>
                  <p className="mt-1 text-xs leading-5 text-terminal-400">{strategy.description}</p>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {(strategy.market_fit || []).map((label) => (
                    <Badge key={label} className="text-[9px]">{label}</Badge>
                  ))}
                </div>
              </div>
              <div className="space-y-2">
                {(strategy.items || []).slice(0, 2).map((item) => (
                  <div key={item.id} className="rounded-xl border border-terminal-800 bg-terminal-950/80 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <p className="font-mono text-base text-terminal-100">{item.symbol}</p>
                        <p className="text-[10px] uppercase tracking-[0.18em] text-terminal-500">{item.timeframe} · {item.regime}</p>
                      </div>
                      <div className="text-right">
                        <p className="font-mono text-base text-emerald-300">+{item.outcome_pct?.toFixed(1)}%</p>
                        <p className="text-[10px] text-terminal-500">{item.holding_bars} bars</p>
                      </div>
                    </div>
                    <p className="mt-2 text-xs leading-4 text-terminal-400">{item.why_it_mattered}</p>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    </section>
  )
}
