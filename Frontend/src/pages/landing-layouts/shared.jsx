import { useMemo, useState } from 'react'
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

// ── Heartbeat: 10 radically different rendering strategies ───────────────
const MAX_ROWS = 16
const pad = (s, n) => String(s).padEnd(n)
const padL = (s, n) => String(s).padStart(n)
const SPARK = '▁▂▃▄▅▆▇█'

function fmtTime(d) {
  if (!d) return '--:--:--'
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}

function C({ c, children }) {
  return <span style={{ color: c }}>{children}</span>
}

function spark(v, max) {
  const i = Math.min(Math.floor((v / (max || 1)) * 8), 7)
  return SPARK[i]
}

// Get sealed pipeline data for a row
function getSealed(row, sealedMap) {
  return sealedMap?.[row.sealedEventId] || null
}

// ── 01: Classic printf columns ──────────────────────────────────────────
function V01({ row }) {
  const ph = row.phase?.code || '·'
  const p = Math.min(row.passCount, 6), f = Math.min(row.failCount, 6)
  const pips = '█'.repeat(p) + '░'.repeat(f) + ' '.repeat(Math.max(0, 6 - p - f))
  const sc = row.raw > 0 ? `${padL(row.raw.toFixed(2), 5)} → ${padL(row.calibrated.toFixed(2), 5)}` : `${padL('—', 5)}   ${padL('—', 5)}`
  const lat = row.latMs != null ? padL(`${row.latMs}ms`, 6) : padL('—', 6)
  const hash = row.traceHash ? row.traceHash.slice(0, 12) : '            '
  const seal = row.isEmit && row.isSealed ? ' SEALED' : ''
  return `${pad(fmtTime(row.ts), 9)}${pad(ph, 4)}${pips}  ${padL(row.indCount, 3)}  ${padL(row.barCount, 4)}  ${sc}  ${lat}  ${hash}${seal}`
}
const H01 = `${pad('TIME', 9)}${pad('PH', 4)}${'GATES   '}  ${'IND'}  ${'BARS'}  ${pad('SCORE', 13)}  ${padL('LAT', 6)}  TRACE HASH`

// ── 02: Structured log (key=value pairs) ────────────────────────────────
function V02({ row, s }) {
  const parts = [`ts=${fmtTime(row.ts)}`, `stage=${row.phase?.code || '?'}`]
  if (row.tf) parts.push(`tf=${row.tf}`)
  parts.push(`ind=${row.indCount}`, `bars=${row.barCount}`)
  if (row.passCount + row.failCount > 0) parts.push(`gates=${row.passCount}/${row.passCount + row.failCount}`)
  if (row.raw > 0) parts.push(`score=${row.raw.toFixed(2)}→${row.calibrated.toFixed(2)}`)
  if (row.latMs != null) parts.push(`lat=${row.latMs}ms`)
  if (row.signals > 0) parts.push(`signals=${row.signals}`)
  if (s?.microstructure?.drift_bps != null) parts.push(`drift=${s.microstructure.drift_bps.toFixed(0)}bp`)
  if (s?.cross_sectional_context?.breadth != null) parts.push(`breadth=${s.cross_sectional_context.breadth.toFixed(3)}`)
  if (row.traceHash) parts.push(`hash=${row.traceHash.slice(0, 8)}`)
  if (row.isEmit && row.isSealed) parts.push('SEALED')
  return parts.join(' ')
}

// ── 03: JSON stream (tail -f on a log) ──────────────────────────────────
function V03({ row }) {
  // Dump the raw data exactly as it came from the pipeline — no reshaping
  return JSON.stringify(row._raw || { ts: row.ts?.getTime() / 1000, phase: row.phase?.code })
}

// ── 04: Sparkline intensity bars ────────────────────────────────────────
function V04({ row, s }) {
  const ind = s?.indicator_snapshot || {}
  const vals = Object.values(ind).filter((v) => typeof v === 'number')
  const max = Math.max(...vals, 1)
  const sparks = vals.map((v) => spark(Math.abs(v), max)).join('')
  const ph = pad(row.phase?.code || '·', 4)
  return `${pad(fmtTime(row.ts), 9)}${ph}${padL(row.indCount, 3)} ${sparks || '·'.repeat(8)}${padL(row.barCount, 5)} bars  ${row.traceHash ? row.traceHash.slice(0, 8) : '        '}${row.isSealed ? ' SEALED' : ''}`
}
const H04 = `${pad('TIME', 9)}${'PH  '}IND INTENSITY                   BARS  HASH`

// ── 05: Gate waterfall cascade ──────────────────────────────────────────
function V05({ row, s }) {
  const gates = s?.gates || []
  const cascade = gates.length > 0
    ? gates.map((g) => `${g.gate || g.name}${g.passed ? '✓' : '✗'}`).join('→')
    : row.passCount > 0 ? `${row.passCount}✓${row.failCount > 0 ? `→${row.failCount}✗` : ''}` : '·'
  const result = row.isSealed ? '→SEALED' : row.signals > 0 ? '→EMIT' : ''
  return `${pad(fmtTime(row.ts), 9)}${pad(row.phase?.code || '·', 4)}${cascade}${result}`
}

// ── 06: Dense indicator matrix ──────────────────────────────────────────
function V06({ row, s }) {
  const snap = s?.indicator_snapshot || {}
  const entries = Object.entries(snap).filter(([, v]) => typeof v === 'number')
  const cells = entries.slice(0, 8).map(([k, v]) => `${k.slice(0, 4)}=${typeof v === 'number' ? (Math.abs(v) < 1 ? v.toFixed(4) : v.toFixed(1)) : v}`).join(' ')
  return `${pad(fmtTime(row.ts), 9)}${pad(row.phase?.code || '·', 4)}${cells || '·'}`
}

// ── 07: Config snapshot diff ────────────────────────────────────────────
function V07({ row, s }) {
  const cfg = s?.config_snapshot || {}
  const entries = Object.entries(cfg)
  if (entries.length === 0) return `${pad(fmtTime(row.ts), 9)}${pad(row.phase?.code || '·', 4)}ind=${padL(row.indCount, 3)} bars=${padL(row.barCount, 4)} ${row.tf ? `tf=${row.tf}` : ''}`
  const cfgStr = entries.map(([k, v]) => `${k}=${v}`).join(' ')
  return `${pad(fmtTime(row.ts), 9)}${pad(row.phase?.code || '·', 4)}${cfgStr}  hash=${row.traceHash ? row.traceHash.slice(0, 8) : '·'}`
}

// ── 08: Pipeline topology (worker routing) ──────────────────────────────
function V08({ row }) {
  const w = row.isActivity ? `w${row.phase?.code === 'ING' ? '0' : '?'}` : 'w?'
  const arrow = row.isSealed ? '══►' : '──►'
  const stage = row.phase?.code || '?'
  const payload = `${row.indCount}ind ${row.barCount}bars`
  const sig = row.signals > 0 ? ` ${row.signals}sig` : ''
  const seal = row.isSealed ? ` ══► SEALED[${(row.traceHash || '').slice(0, 8)}]` : ''
  return `${pad(fmtTime(row.ts), 9)}${w}:${stage}${arrow} ${payload}${sig}${seal}`
}

// ── 09: Narrative (plain English) ───────────────────────────────────────
function V09({ row, s }) {
  const stage = row.phase?.code || '?'
  const stages = { ING: 'Ingested', PER: 'Persisted', IND: 'Computed', SIG: 'Evaluated' }
  const verb = stages[stage] || 'Processed'
  let line = `${fmtTime(row.ts)} ${verb} ${row.indCount} indicators across ${row.barCount} bars`
  if (row.tf) line += ` [${row.tf}]`
  if (row.passCount + row.failCount > 0) line += `. ${row.passCount} gates passed`
  if (row.raw > 0) line += `. Score ${row.raw.toFixed(2)}→${row.calibrated.toFixed(2)}`
  if (s?.microstructure?.drift_bps != null) line += `. Drift ${s.microstructure.drift_bps.toFixed(0)}bp`
  if (row.isSealed) line += `. Sealed ${(row.traceHash || '').slice(0, 8)}`
  return line
}

// ── 10: Hex dump aesthetic ──────────────────────────────────────────────
function V10({ row }) {
  const ts = Math.floor((row.ts?.getTime() || 0) / 1000)
  const hex = ts.toString(16).padStart(8, '0')
  const ph = (row.phase?.code || '??').padEnd(3)
  const d1 = row.indCount.toString(16).padStart(2, '0')
  const d2 = row.barCount.toString(16).padStart(4, '0')
  const d3 = row.passCount.toString(16).padStart(2, '0')
  const d4 = row.failCount.toString(16).padStart(2, '0')
  const sc = row.raw > 0 ? Math.floor(row.raw * 255).toString(16).padStart(2, '0') + Math.floor(row.calibrated * 255).toString(16).padStart(2, '0') : '0000'
  const hashBytes = row.traceHash ? row.traceHash.slice(0, 16) : '0'.repeat(16)
  const sig = row.signals > 0 ? ` +${row.signals}` : '   '
  const seal = row.isSealed ? ' *' : '  '
  return `0x${hex}  ${ph} ${d1} ${d2} ${d3}${d4} ${sc}  ${hashBytes}${sig}${seal}`
}
const H10 = 'ADDR      STG IN BARS GATE SCOR  TRACE HASH       SIG'

// ── Variant registry ────────────────────────────────────────────────────
const VARIANTS = [
  { id: 'classic',   label: '01 Classic Pipeline',    header: H01,  render: V01 },
  { id: 'logfmt',    label: '02 Structured Log',      header: null, render: V02 },
  { id: 'json',      label: '03 JSON Stream',         header: null, render: V03 },
  { id: 'spark',     label: '04 Sparkline Intensity',  header: H04,  render: V04 },
  { id: 'waterfall', label: '05 Gate Waterfall',       header: null, render: V05 },
  { id: 'matrix',    label: '06 Indicator Matrix',     header: null, render: V06 },
  { id: 'config',    label: '07 Config Snapshot',      header: null, render: V07 },
  { id: 'topology',  label: '08 Pipeline Topology',    header: null, render: V08 },
  { id: 'narrative', label: '09 Narrative',            header: null, render: V09 },
  { id: 'hexdump',   label: '10 Hex Dump',            header: H10,  render: V10 },
]

function HeartbeatFeed({ rows, stats, isLive, events }) {
  const [variantId, setVariantId] = useState('classic')
  const variant = VARIANTS.find((v) => v.id === variantId) || VARIANTS[0]
  const Render = variant.render

  const visible = useMemo(() => rows.slice(0, MAX_ROWS), [rows])

  const sealedMap = useMemo(() => {
    const m = {}
    for (const e of events) {
      if (e.pipeline) m[e.opportunity.id] = e.pipeline
    }
    return m
  }, [events])

  return (
    <div className="overflow-hidden rounded-2xl bg-[#111113] px-4 py-3.5">
      {/* Head */}
      <div className="mb-1.5 flex items-center gap-2">
        {isLive && <div className="hb-dot" />}
        <span className="font-mono text-[10px] uppercase tracking-[1.5px] text-[#555]">Glass Box trace stream</span>
        {stats && (
          <span className="ml-auto font-mono text-[10px] text-[#444]">{stats.sealedCount.toLocaleString()} sealed</span>
        )}
      </div>

      {/* Terminal output */}
      <div className="hb-fade">
        <pre className="m-0 overflow-hidden whitespace-pre text-[11px] leading-[18px] text-[#888]">
          {variant.header && <><C c="#555">{variant.header}</C>{'\n'}</>}
          {visible.map((row) => (
            <div key={row.id} style={{ animation: 'hb-row-in 0.3s ease-out' }}>
              {Render({ row, s: getSealed(row, sealedMap) })}{'\n'}
            </div>
          ))}
          {visible.length === 0 && <C c="#444">{'  awaiting pipeline events...\n'}</C>}
        </pre>
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

      {/* Variant selector */}
      <div className="mt-2 border-t border-white/[0.04] pt-2">
        <select
          value={variantId}
          onChange={(e) => setVariantId(e.target.value)}
          className="rounded bg-[#1a1a1d] px-2 py-1 font-mono text-[10px] text-[#666] outline-none"
        >
          {VARIANTS.map((v) => (
            <option key={v.id} value={v.id}>{v.label}</option>
          ))}
        </select>
      </div>
    </div>
  )
}

// --- Glass Box replay (live heartbeat with static fallback) ---
export function GlassBoxReplay({ proofPack }) {
  const { rows, stats, isLive, events } = useLiveFeed()

  if (events.length > 0 || rows.length > 0) {
    return <HeartbeatFeed rows={rows} stats={stats} isLive={isLive} events={events} />
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
