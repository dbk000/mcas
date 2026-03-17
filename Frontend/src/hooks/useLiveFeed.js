import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getPollingInterval, getRequestConfig } from '../config/governed'
import { getLogger } from '../utils/logger'

const log = getLogger('live-feed')

function resolveApiBase() {
  if (import.meta.env.VITE_PUBLIC_API_BASE) return import.meta.env.VITE_PUBLIC_API_BASE
  const host = typeof window !== 'undefined' ? window.location.hostname : ''
  if (!host || host === 'localhost' || host.startsWith('127.') || host.startsWith('192.168.')) return ''
  if (host.endsWith('.pages.dev')) return 'https://api.mcasui.com'
  return `${window.location.protocol}//api.${host}`
}

const API_BASE = resolveApiBase()

// Pipeline phases for the heartbeat cascade
const PHASES = [
  { code: 'ING' }, { code: 'FTR' }, { code: 'SIG' },
  { code: 'RSK' }, { code: 'EMT' },
]

/**
 * Build heartbeat rows from activity events (the firehose — every eval cycle)
 * and sealed events (the highlights — opportunities with trace_json).
 */
function buildRows(activity, sealedEvents) {
  const rows = []

  // Activity events → pipeline cadence rows
  // stage field: ING (ingestor), PER (persister), IND (indicator), SIG (signal worker)
  const STAGE_PHASE = { ING: 'ING', PER: 'FTR', IND: 'SIG', undefined: 'RSK' }
  for (let i = 0; i < activity.length; i++) {
    const a = activity[i]
    const ts = a.ts ? new Date(a.ts * 1000) : null
    const stage = a.stage || null
    const phaseCode = stage ? (STAGE_PHASE[stage] || stage) : PHASES[i % PHASES.length].code
    const gates = a.gates || []
    const passCount = gates.filter((g) => g.passed).length
    const failCount = gates.length - passCount

    rows.push({
      id: `a-${i}-${a.ts}`,
      ts,
      phase: { code: stage || phaseCode },
      passCount,
      failCount,
      indCount: a.ind_count || a.evals || 0,
      barCount: a.bar_count || a.bars || a.rows || 0,
      raw: a.score?.raw ?? 0,
      calibrated: a.score?.calibrated ?? 0,
      latMs: a.compute_ms || a.batch_ms || null,
      traceHash: null,
      isSealed: false,
      isEmit: false,
      isActivity: true,
      signals: a.signals || 0,
      tf: a.tf || null,
    })
  }

  // Sealed events → EMIT rows with real trace data (interleaved by timestamp)
  for (const event of sealedEvents) {
    const p = event.pipeline
    if (!p) continue

    const evalTs = p.eval_ts ? new Date(p.eval_ts * 1000) : null
    const gates = p.gates || []
    const passCount = gates.filter((g) => g.passed).length
    const failCount = gates.length - passCount
    const indCount = p.indicator_snapshot ? Object.keys(p.indicator_snapshot).length : 0
    const barCount = (p.candle_window || []).reduce((s, w) => s + (w.bar_count || 0), 0)
    const raw = p.score?.raw ?? 0
    const calibrated = p.score?.calibrated ?? 0
    const sealedAt = p.promotion?.sealed_at
    const latMs = (sealedAt && p.eval_ts) ? Math.round((sealedAt - p.eval_ts) * 1000) : null

    rows.push({
      id: `s-${event.opportunity.id}`,
      ts: evalTs,
      phase: { code: 'EMT' },
      passCount,
      failCount,
      indCount,
      barCount,
      raw,
      calibrated,
      latMs,
      traceHash: p.trace_hash || null,
      isSealed: Boolean(p.trace_hash),
      isEmit: true,
      isActivity: false,
      signals: 1,
      sealedEventId: event.opportunity.id,
    })
  }

  // Sort by timestamp descending (newest first)
  rows.sort((a, b) => (b.ts?.getTime() || 0) - (a.ts?.getTime() || 0))
  return rows
}

export default function useLiveFeed() {
  const [events, setEvents] = useState([])
  const [activity, setActivity] = useState([])
  const [error, setError] = useState(null)
  const [isLive, setIsLive] = useState(false)
  const intervalRef = useRef(null)
  const abortRef = useRef(null)
  const visibleRef = useRef(true)

  const fetchFeed = useCallback(async () => {
    if (!visibleRef.current) return

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    const { timeoutMs } = getRequestConfig()
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs)

    try {
      const res = await fetch(`${API_BASE}/v1/public/pipeline-feed`, {
        signal: controller.signal,
      })
      clearTimeout(timeoutId)

      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const json = await res.json()
      const feedEvents = json?.data?.events || []
      const feedActivity = json?.data?.activity || []
      setEvents(feedEvents)
      setActivity(feedActivity)
      setError(null)

      // Live if we have activity within the last 30s
      if (feedActivity.length > 0) {
        const newestTs = feedActivity[0]?.ts || 0
        setIsLive((Date.now() / 1000 - newestTs) < 30)
      } else {
        setIsLive(false)
      }
    } catch (err) {
      clearTimeout(timeoutId)
      if (err.name !== 'AbortError') {
        log.warn('Feed fetch failed', { error: err.message })
        setError(err.message)
      }
    }
  }, [])

  useEffect(() => {
    const handleVisibility = () => {
      visibleRef.current = document.visibilityState === 'visible'
      if (visibleRef.current) fetchFeed()
    }
    document.addEventListener('visibilitychange', handleVisibility)

    fetchFeed()
    const ms = getPollingInterval('public_feed_ms') || 12_000
    intervalRef.current = setInterval(fetchFeed, ms)

    return () => {
      document.removeEventListener('visibilitychange', handleVisibility)
      clearInterval(intervalRef.current)
      abortRef.current?.abort()
    }
  }, [fetchFeed])

  const rows = useMemo(() => buildRows(activity, events), [activity, events])

  const stats = useMemo(() => {
    if (activity.length === 0 && events.length === 0) return null

    // From activity: total evals, gate pass rate
    let totalEvals = 0
    let totalGatesOk = 0
    let totalGatesFail = 0
    let totalSignals = 0
    for (const a of activity) {
      totalEvals += a.evals || 0
      totalGatesOk += a.gates_ok || 0
      totalGatesFail += a.gates_fail || 0
      totalSignals += a.signals || 0
    }
    const totalGates = totalGatesOk + totalGatesFail
    const gatePassRate = totalGates > 0
      ? ((totalGatesOk / totalGates) * 100).toFixed(1)
      : '0.0'

    // Evals/h from activity time span
    let evalsPerH = 0
    if (activity.length >= 2) {
      const spanS = (activity[0]?.ts || 0) - (activity[activity.length - 1]?.ts || 0)
      if (spanS > 0) evalsPerH = Math.round(totalEvals / (spanS / 3600))
    }

    // Latency from sealed events
    const latencies = []
    let sealedCount = 0
    for (const e of events) {
      const p = e.pipeline
      if (!p) continue
      if (p.trace_hash) sealedCount++
      if (p.promotion?.sealed_at && p.eval_ts) {
        latencies.push(Math.round((p.promotion.sealed_at - p.eval_ts) * 1000))
      }
    }
    const avgLatMs = latencies.length > 0
      ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length)
      : 0

    return { evalsPerH, avgLatMs, gatePassRate, sealedCount }
  }, [activity, events])

  return { events, rows, stats, error, isLive }
}
