import { useCallback, useEffect, useRef, useState } from 'react'
import { getPollingInterval, getRequestConfig, getStalenessThresholdS } from '../config/governed'
import { getLogger } from '../utils/logger'

const log = getLogger('live-feed')

const API_BASE = import.meta.env.VITE_PUBLIC_API_BASE || ''

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

      if (!res.ok) {
        throw new Error(`HTTP ${res.status}`)
      }

      const json = await res.json()
      const feedEvents = json?.data?.events || []
      setEvents(feedEvents)
      setError(null)

      // Determine liveness: most recent event within staleness threshold
      if (feedEvents.length > 0) {
        const newest = feedEvents[0]
        const createdTs = newest?.opportunity?.created_ts
        if (createdTs) {
          const ageS = (Date.now() - new Date(createdTs).getTime()) / 1000
          const tf = newest?.opportunity?.timeframe || '1h'
          const threshold = getStalenessThresholdS(tf)
          setIsLive(ageS < threshold)
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

  return { events, error, isLive }
}
