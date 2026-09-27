import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from '../../context/ApiContext'
import {
  getPollingInterval,
  getRequestConfig,
  getStalenessThresholdS,
  getTfSeconds,
} from '../../config/governed'
import { STORAGE_KEYS } from '../../config/storageKeys'
import { buildEndpointWithParams } from '../../api/requestClient'
import { readStored, validSelection, writeStored } from './model'
import { expandDemoFixture } from './demoFixture'

// Serial polling: at most one in-flight request per feed; requests are cancelled
// on a key change. Retained results are never shown under a different market/TF.
export function useLabFeed(
  endpoint,
  params,
  {
    enabled = true,
    paused,
    cadence = 'opportunities_ms',
    demoData,
    demo = false,
  } = {}
) {
  const { get, config, session } = useApi()
  const query = useMemo(
    () => buildEndpointWithParams(endpoint, params),
    [endpoint, params]
  )
  const key = `${config.baseUrl}|${query}`
  const [result, setResult] = useState({})
  const [refreshId, setRefreshId] = useState(0)
  const consumedRefresh = useRef(0)
  const interval = getPollingInterval(cadence)
  const timeout = getRequestConfig().timeoutMs
  const authenticated = session.hasSession && session.auth === 'ok'

  useEffect(() => {
    const manualRefresh = refreshId !== consumedRefresh.current
    consumedRefresh.current = refreshId
    if (demo || !enabled || !authenticated) return undefined
    let active = true
    let timer
    let controller
    let timeoutId
    async function poll() {
      controller = new AbortController()
      timeoutId = window.setTimeout(() => controller.abort(), timeout)
      try {
        const data = await get(query, {}, { signal: controller.signal })
        if (active)
          setResult({ key, data, receivedAt: Date.now(), error: null })
      } catch (error) {
        if (active)
          setResult((previous) => ({
            ...(previous.key === key ? previous : {}),
            key,
            error: error.isAbort ? 'Request timed out' : error.message,
            failedAt: Date.now(),
          }))
      } finally {
        window.clearTimeout(timeoutId)
        if (active && !paused) timer = window.setTimeout(poll, interval)
      }
    }
    // Pausing retains the displayed data; manual refresh still performs one read.
    if (!paused || manualRefresh) void poll()
    return () => {
      active = false
      window.clearTimeout(timer)
      window.clearTimeout(timeoutId)
      controller?.abort()
    }
  }, [
    authenticated,
    demo,
    enabled,
    get,
    interval,
    key,
    paused,
    query,
    refreshId,
    timeout,
  ])

  const refresh = useCallback(() => setRefreshId((n) => n + 1), [])
  if (demo)
    return {
      data: demoData,
      error: null,
      loading: !demoData,
      receivedAt: null,
      refresh,
      demo: true,
    }
  const current = result.key === key ? result : {}
  return {
    ...current,
    data: enabled && authenticated ? current.data : null,
    loading:
      enabled && authenticated && !current.data && !current.error && !paused,
    refresh,
    demo: false,
    paused,
  }
}

export function useLinkedSelection({
  scope,
  follow,
  initialSymbol,
  initialTf,
  timeframes,
}) {
  const key = `${STORAGE_KEYS.TRADING_LAB_SELECTION}:${scope}`
  const [selection, setSelection] = useState(() => {
    const saved = readStored(key, null)
    const symbol = /^[A-Z0-9._:/-]{1,40}$/.test(initialSymbol || '')
      ? initialSymbol
      : ''
    return !symbol &&
      follow &&
      validSelection(saved, scope) &&
      timeframes.includes(saved.tf)
      ? saved
      : { symbol, tf: initialTf, scope, sentAt: 0 }
  })
  const channel = useRef(null)

  useEffect(() => {
    const accept = (value) => {
      if (
        follow &&
        validSelection(value, scope) &&
        timeframes.includes(value.tf)
      ) {
        setSelection((previous) =>
          value.sentAt >= previous.sentAt ? value : previous
        )
      }
    }
    // Rejoining a link group catches up to its last selection.
    accept(readStored(key, null))
    try {
      channel.current = new BroadcastChannel(key)
    } catch {
      channel.current = null
    }
    if (channel.current)
      channel.current.onmessage = (event) => accept(event.data)
    const onStorage = (event) => {
      if (event.key !== key || !event.newValue) return
      try {
        accept(JSON.parse(event.newValue))
      } catch {
        /* malformed data is ignored */
      }
    }
    window.addEventListener('storage', onStorage)
    return () => {
      channel.current?.close()
      channel.current = null
      window.removeEventListener('storage', onStorage)
    }
  }, [follow, key, scope, timeframes])

  const select = useCallback(
    (symbol, tf = selection.tf) => {
      const next = { symbol, tf, scope, sentAt: Date.now() }
      if (!validSelection(next, scope) || !timeframes.includes(tf)) return
      setSelection(next)
      if (follow) {
        writeStored(key, next)
        channel.current?.postMessage(next)
      }
    },
    [follow, key, scope, selection.tf, timeframes]
  )
  return [selection, select]
}

export function useLabClock() {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

export function useLabDemo(enabled) {
  const [state, setState] = useState({ data: null, error: null })
  useEffect(() => {
    if (!enabled) return undefined
    const controller = new AbortController()
    fetch('/curation/trading-lab-demo.v1.json', { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error('Preview data could not be loaded')
        return response.json()
      })
      .then((data) =>
        setState({
          data: expandDemoFixture(data, getTfSeconds()),
          error: null,
        })
      )
      .catch((error) => {
        if (!controller.signal.aborted)
          setState({ data: null, error: error.message })
      })
    return () => controller.abort()
  }, [enabled])
  return state
}

export function feedThreshold(tf) {
  return getStalenessThresholdS(tf)
}
