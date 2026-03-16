import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { normalizeRequestError } from '../api/errors'
import { buildEndpointWithParams } from '../api/requestClient'
import { getLogger } from '../utils/logger'
import { getPollingInterval } from '../config/governed'

const log = getLogger('data')

function extractMeta(result) {
  return result?.__meta ?? null
}

export function useFetch(endpoint, params = {}, options = {}) {
  const { get, isConnected } = useApi()
  const { addToast } = useToast()
  const { skip = false, showError = true } = options
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(Boolean(endpoint) && !skip)
  const [error, setError] = useState(null)
  const [errorState, setErrorState] = useState(null)
  const [meta, setMeta] = useState(null)
  const hasFetchedRef = useRef(false)
  const abortRef = useRef(null)
  const paramsRef = useRef(params)
  const requestKey = useMemo(
    () => (endpoint ? buildEndpointWithParams(endpoint, params) : ''),
    [endpoint, params]
  )

  useEffect(() => {
    paramsRef.current = params
  }, [params, requestKey])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const fetchData = useCallback(async () => {
    if (!endpoint || skip || !isConnected) {
      if (!endpoint || skip) {
        setLoading(false)
      }
      return null
    }

    cancel()

    const controller = new AbortController()
    abortRef.current = controller

    if (!hasFetchedRef.current) {
      setLoading(true)
    }

    setError(null)
    setErrorState(null)

    try {
      const result = await get(endpoint, paramsRef.current, {
        signal: controller.signal,
      })

      if (abortRef.current !== controller || controller.signal.aborted) {
        return null
      }

      setData(result)
      setMeta(extractMeta(result))
      hasFetchedRef.current = true

      return result
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (normalized.isAbort || abortRef.current !== controller) {
        return null
      }

      log.warn('Fetch error', {
        event: 'fetch_error',
        endpoint,
        error_message: normalized.message,
      })

      setError(normalized.message)
      setErrorState(normalized)

      if (showError) {
        addToast(`Failed to fetch: ${normalized.message}`, 'error')
      }

      return null
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setLoading(false)
      }
    }
  }, [addToast, cancel, endpoint, get, isConnected, showError, skip])

  useEffect(() => {
    hasFetchedRef.current = false
    setLoading(Boolean(endpoint) && !skip)
    setError(null)
    setErrorState(null)
    setMeta(null)
    cancel()
  }, [cancel, endpoint, requestKey, skip])

  useEffect(() => {
    void fetchData()
  }, [fetchData, requestKey])

  useEffect(() => cancel, [cancel])

  return {
    data,
    loading,
    error,
    errorState,
    meta,
    refetch: fetchData,
    cancel,
  }
}

export function usePolling(endpoint, params = {}, intervalMs = 15000, options = {}) {
  const { get, isConnected } = useApi()
  const { skip = false, enabled = true } = options
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(Boolean(endpoint) && !skip && enabled)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [errorState, setErrorState] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [meta, setMeta] = useState(null)
  const intervalRef = useRef(null)
  const abortRef = useRef(null)
  const hasFetchedRef = useRef(false)
  const paramsRef = useRef(params)
  const requestKey = useMemo(
    () => (endpoint ? buildEndpointWithParams(endpoint, params) : ''),
    [endpoint, params]
  )
  const pollingEnabled = Boolean(endpoint) && enabled && !skip && isConnected

  useEffect(() => {
    paramsRef.current = params
  }, [params, requestKey])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const clearPolling = useCallback(() => {
    if (intervalRef.current) {
      window.clearInterval(intervalRef.current)
      intervalRef.current = null
    }
  }, [])

  const fetchData = useCallback(async ({ manual = false } = {}) => {
    if (!endpoint || skip || !enabled || !isConnected) {
      if (!endpoint || skip || !enabled) {
        setLoading(false)
        setRefreshing(false)
      }
      return null
    }

    cancel()

    const controller = new AbortController()
    abortRef.current = controller

    if (!hasFetchedRef.current) {
      setLoading(true)
    } else if (manual) {
      setRefreshing(true)
    }

    try {
      const result = await get(endpoint, paramsRef.current, {
        signal: controller.signal,
      })

      if (abortRef.current !== controller || controller.signal.aborted) {
        return null
      }

      setData(result)
      setMeta(extractMeta(result))
      setLastUpdated(new Date())
      setError(null)
      setErrorState(null)
      hasFetchedRef.current = true

      return result
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (normalized.isAbort || abortRef.current !== controller) {
        return null
      }

      log.debug('Poll cycle error', {
        event: 'poll_error',
        endpoint,
        error_message: normalized.message,
      })

      setError(normalized.message)
      setErrorState(normalized)

      return null
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [cancel, enabled, endpoint, get, isConnected, skip])

  useEffect(() => {
    hasFetchedRef.current = false
    setLoading(Boolean(endpoint) && !skip && enabled)
    setRefreshing(false)
    setError(null)
    setErrorState(null)
    setMeta(null)
    clearPolling()
    cancel()
  }, [cancel, clearPolling, enabled, endpoint, requestKey, skip])

  useEffect(() => {
    clearPolling()

    if (!pollingEnabled) {
      if (!endpoint || skip || !enabled) {
        setLoading(false)
      }
      return undefined
    }

    log.debug('Polling started', {
      event: 'poll_start',
      endpoint,
      interval_ms: intervalMs,
    })

    void fetchData()
    intervalRef.current = window.setInterval(() => {
      void fetchData()
    }, intervalMs)

    return () => {
      log.debug('Polling stopped', { event: 'poll_stop', endpoint })
      clearPolling()
      cancel()
    }
  }, [cancel, clearPolling, enabled, endpoint, fetchData, intervalMs, pollingEnabled, requestKey, skip])

  useEffect(() => () => {
    clearPolling()
    cancel()
  }, [cancel, clearPolling])

  const refetch = useCallback(() => fetchData({ manual: true }), [fetchData])

  return {
    data,
    loading,
    refreshing,
    error,
    errorState,
    lastUpdated,
    meta,
    refetch,
    cancel,
  }
}

export function useMarkets() {
  return useFetch('/v1/markets')
}

export function useTimeframes() {
  return useFetch('/v1/timeframes')
}

export function useStrategies() {
  return useFetch('/v1/strategies')
}

export function useOpportunities(params = {}, enabled = true) {
  return usePolling('/v1/opportunities', params, getPollingInterval('opportunities_ms'), { enabled })
}

export function useOpportunity(id) {
  return useFetch(`/v1/opportunities/${id}`, {}, { skip: !id })
}

export function useOpportunityPriceWindow(oppId, params = {}, enabled = true) {
  return useFetch(
    `/v1/opportunities/${oppId}/price-window`,
    params,
    { skip: !enabled || !oppId }
  )
}

export function useLiveOpportunity(setup_id, symbol, tf) {
  return useFetch(
    `/v1/opportunities/live/${setup_id}/${symbol}/${tf}`,
    {},
    { skip: !setup_id || !symbol || !tf }
  )
}

export function useTopSignals(params = {}, enabled = true) {
  return usePolling('/v1/signals/top', params, getPollingInterval('signals_top_ms'), { enabled })
}

export function useRuns(params = {}) {
  return useFetch('/v1/runs', params)
}

export function useLatestRun(runType = 'SCAN') {
  return useFetch('/v1/runs/latest', { run_type: runType }, { showError: false })
}

export function useRun(id) {
  return useFetch(`/v1/runs/${id}`, {}, { skip: !id })
}

export function useRunTrades(runId, params = {}) {
  return useFetch(`/v1/runs/${runId}/trades`, params, { skip: !runId })
}

export function useRunEvents(runId, params = {}) {
  return useFetch(`/v1/runs/${runId}/events`, params, { skip: !runId })
}

export function useQueueStatus() {
  return usePolling('/v1/control/runs/queue', {}, getPollingInterval('queue_status_ms'))
}

export function usePaperStatus(enabled = true) {
  return usePolling('/v1/control/paper/status', {}, getPollingInterval('paper_status_ms'), { enabled })
}

export function useTradingAccount(enabled = true) {
  return usePolling('/v1/trading/account', {}, getPollingInterval('trading_account_ms'), { enabled })
}

export function usePositions(params = {}, enabled = true) {
  return usePolling('/v1/trading/positions', params, getPollingInterval('positions_ms'), { enabled })
}

export function useTrades(params = {}) {
  return useFetch('/v1/trading/trades', params)
}

export function usePerformance() {
  return useFetch('/v1/trading/performance')
}

export function useLiveOHLCV(symbol, tf, limit = 100, intervalMs = getPollingInterval('ohlcv_ms'), enabled = true) {
  return usePolling('/v1/ohlcv', { symbol, tf, limit }, intervalMs, {
    enabled: enabled && Boolean(symbol) && Boolean(tf),
    skip: !enabled || !symbol || !tf,
  })
}

export function useSnapshot(symbol, enabled = true) {
  return usePolling('/v1/snapshot/minute', { symbol, timeframe: '1m' }, getPollingInterval('snapshot_ms'), {
    enabled: enabled && Boolean(symbol),
    skip: !symbol,
  })
}

export function useSqueezeState(symbol, tf) {
  return useFetch('/v1/squeeze/state', { symbol, tf }, { skip: !symbol || !tf, showError: false })
}

export function useBtcSqueezeStatus(tf) {
  return useFetch('/v1/btc/squeeze-status', { tf }, { skip: !tf, showError: false })
}

export function useDataAvailability(symbols, timeframes, options = {}) {
  const params = {}

  if (symbols?.length) params.symbols = symbols.join(',')
  if (timeframes?.length) params.timeframes = timeframes.join(',')

  const skip = !symbols?.length || !timeframes?.length || options.skip

  return useFetch('/v1/control/data/availability', params, { skip, showError: false })
}
