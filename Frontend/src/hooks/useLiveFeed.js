import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { getPollingInterval, getRequestConfig, getStalenessThresholdS } from '../config/governed'
import { getLogger } from '../utils/logger'

const log = getLogger('live-feed')

function resolveApiBase() {
  if (import.meta.env.VITE_PUBLIC_API_BASE) return import.meta.env.VITE_PUBLIC_API_BASE
  // Derive from hostname: mcasui.com → api.mcasui.com
  const host = typeof window !== 'undefined' ? window.location.hostname : ''
  if (host && host !== 'localhost' && !host.startsWith('127.') && !host.startsWith('192.168.')) {
    return `${window.location.protocol}//api.${host}`
  }
  return ''
}

const API_BASE = resolveApiBase()

/** Derive per-event heartbeat rows from the raw pipeline trace. */
function deriveHeartbeatRows(events) {
  const rows = []
  for (const event of events) {
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
    const traceHash = p.trace_hash || null
    const isSealed = Boolean(traceHash)

    // Each real event maps to the EMIT phase; we synthesise the 5-phase cascade
    const PHASES = [
      { code: 'ING', cls: 'phase-ing' },
      { code: 'FTR', cls: 'phase-ftr' },
      { code: 'SIG', cls: 'phase-sig' },
      { code: 'RSK', cls: 'phase-rsk' },
      { code: 'EMT', cls: 'phase-emt' },
    ]

    for (const phase of PHASES) {
      rows.push({
        id: `${event.opportunity.id}-${phase.code}`,
        ts: evalTs,
        phase,
        passCount,
        failCount,
        indCount,
        barCount,
        raw,
        calibrated,
        latMs,
        traceHash,
        isSealed,
        isEmit: phase.code === 'EMT',
      })
    }
  }
  return rows
}

export default function useLiveFeed() {
  const [events, setEvents] = useState([])
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
      setEvents(feedEvents)
      setError(null)

      if (feedEvents.length > 0) {
        const newest = feedEvents[0]
        const createdTs = newest?.opportunity?.created_ts
        if (createdTs) {
          const ageS = (Date.now() - new Date(createdTs).getTime()) / 1000
          const tf = newest?.opportunity?.timeframe || '1h'
          setIsLive(ageS < getStalenessThresholdS(tf))
        } else {
          setIsLive(false)
        }
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

  // Derive heartbeat rows and stats from raw events
  const rows = useMemo(() => deriveHeartbeatRows(events), [events])

  const stats = useMemo(() => {
    if (events.length === 0) return null
    const latencies = []
    let totalPass = 0
    let totalGates = 0
    let sealedCount = 0

    for (const e of events) {
      const p = e.pipeline
      if (!p) continue
      const gates = p.gates || []
      totalPass += gates.filter((g) => g.passed).length
      totalGates += gates.length
      if (p.trace_hash) sealedCount++
      if (p.promotion?.sealed_at && p.eval_ts) {
        latencies.push(Math.round((p.promotion.sealed_at - p.eval_ts) * 1000))
      }
    }

    const avgLat = latencies.length > 0
      ? Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length)
      : 0
    const gatePassRate = totalGates > 0
      ? ((totalPass / totalGates) * 100).toFixed(1)
      : '0.0'

    // decisions/h: extrapolate from the time span of events we have
    let decisionsPerH = 0
    if (events.length >= 2) {
      const newest = new Date(events[0]?.opportunity?.created_ts).getTime()
      const oldest = new Date(events[events.length - 1]?.opportunity?.created_ts).getTime()
      const spanH = (newest - oldest) / 3_600_000
      if (spanH > 0) decisionsPerH = Math.round(events.length / spanH)
    }

    return { decisionsPerH, avgLatMs: avgLat, gatePassRate, sealedCount }
  }, [events])

  return { events, rows, stats, error, isLive }
}
