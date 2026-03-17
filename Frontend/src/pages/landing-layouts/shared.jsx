import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { ArrowRight, ShieldCheck, ScanLine, TimerReset, ChevronRight, Layers3 } from 'lucide-react'
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

// ── Heartbeat: JSON stream — direct DOM, no React render loop ────────────

function C({ c, children }) {
  return <span style={{ color: c }}>{children}</span>
}

function HeartbeatFeed({ rows, stats, isLive }) {
  const preRef = useRef(null)
  const queueRef = useRef([])
  const timerRef = useRef(null)
  const seenRef = useRef(new Set())

  // On each poll: stringify new rows, push to queue, calculate feed rate
  useEffect(() => {
    for (const row of rows) {
      if (seenRef.current.has(row.id)) continue
      seenRef.current.add(row.id)
      const text = JSON.stringify(row._raw || { ts: row.ts?.getTime() / 1000, phase: row.phase?.code })
      queueRef.current.push(text)
    }
    // Prevent seen set from growing forever
    if (seenRef.current.size > 500) {
      const arr = [...seenRef.current]
      seenRef.current = new Set(arr.slice(-200))
    }
  }, [rows])

  // Single timer: feeds one line per tick, adapts speed to queue depth
  // Goal: spread lines evenly across the ~12s poll interval so it never stops
  useEffect(() => {
    const tick = () => {
      const pre = preRef.current
      if (!pre) return

      const line = queueRef.current.shift()
      if (line) {
        pre.appendChild(document.createTextNode(line + '\n'))
        // Trim DOM — keep last 300 text nodes
        while (pre.childNodes.length > 300) pre.removeChild(pre.firstChild)
        pre.scrollTop = pre.scrollHeight
      }

      // Adaptive interval: spread remaining queue across ~11s (leave 1s buffer)
      const pending = queueRef.current.length
      const ms = pending > 0 ? Math.max(40, Math.floor(13500 / pending)) : 200
      timerRef.current = setTimeout(tick, ms)
    }

    timerRef.current = setTimeout(tick, 100)
    return () => clearTimeout(timerRef.current)
  }, [])

  return (
    <div className="overflow-hidden bg-[#111113] px-4 py-3.5">
      {/* Head */}
      <div className="mb-1.5 flex items-center gap-2">
        {isLive && <div className="hb-dot" />}
        <span className="font-mono text-[10px] uppercase tracking-[1.5px] text-[#555]">Glass Box trace stream</span>
        {stats && (
          <span className="ml-auto font-mono text-[10px] text-[#444]">{stats.sealedCount.toLocaleString()} sealed</span>
        )}
      </div>

      {/* Terminal: raw JSON fed directly to DOM, never re-rendered by React */}
      <div className="hb-fade" style={{ maxHeight: '340px' }}>
        <pre
          ref={preRef}
          className="m-0 overflow-y-auto whitespace-pre-wrap break-all text-[10px] leading-[16px] text-[#888]"
          style={{ maxHeight: '320px' }}
        />
      </div>

      {/* Stats bar */}
      {stats && (
        <div className="mt-2.5 border-t border-white/[0.06] pt-2 font-mono text-[10px] text-[#555]">
          evals/h <C c="#888">{stats.evalsPerH.toLocaleString()}</C>
          {'    '}avg latency <C c="#888">{stats.avgLatMs}ms</C>
          {'    '}gate pass <C c="#888">{stats.gatePassRate}%</C>
          {'    '}sealed <C c="#888">{stats.sealedCount.toLocaleString()}</C>
        </div>
      )}
    </div>
  )
}

// --- Glass Box replay (live JSON stream with static fallback) ---
export function GlassBoxReplay({ proofPack }) {
  const { rows, stats, isLive, events } = useLiveFeed()

  if (events.length > 0 || rows.length > 0) {
    return <HeartbeatFeed rows={rows} stats={stats} isLive={isLive} />
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
