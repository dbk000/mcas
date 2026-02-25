import { useState, useEffect, useCallback, useRef } from 'react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { getLogger } from '../utils/logger'

const log = getLogger('data')

// Generic data fetching hook
export function useFetch(endpoint, params = {}, options = {}) {
  const { get, isConnected } = useApi()
  const { addToast } = useToast()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true) // Start true until first fetch completes
  const [error, setError] = useState(null)
  const [meta, setMeta] = useState(null) // Canon §1.5: envelope meta (as_of_ts etc.)
  const { skip = false, showError = true } = options
  const hasFetchedRef = useRef(false)

  const paramsString = JSON.stringify(params)

  const fetchData = useCallback(async () => {
    if (!isConnected || skip || !endpoint) {
      // If skipped, not loading
      if (skip) setLoading(false)
      return
    }

    if (!hasFetchedRef.current) {
      setLoading(true)
    }
    setError(null)

    try {
      const result = await get(endpoint, params)
      setData(result)
      if (result?.__meta) setMeta(result.__meta)
      hasFetchedRef.current = true
    } catch (err) {
      log.warn('Fetch error', { event: 'fetch_error', endpoint, error_message: err.message })
      setError(err.message)
      if (showError) {
        addToast(`Failed to fetch: ${err.message}`, 'error')
      }
    } finally {
      setLoading(false)
    }
  }, [endpoint, paramsString, isConnected, skip, get, addToast, showError])

  // Reset on params change
  useEffect(() => {
    hasFetchedRef.current = false
    setLoading(true)
  }, [paramsString])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  return { data, loading, error, meta, refetch: fetchData }
}

// Polling hook - loading only true for first fetch, refreshing for subsequent polls
export function usePolling(endpoint, params = {}, intervalMs = 15000, options = {}) {
  const { get, isConnected } = useApi()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true) // True initially until first data
  const [refreshing, setRefreshing] = useState(false) // For poll updates
  const [error, setError] = useState(null)
  const [lastUpdated, setLastUpdated] = useState(null)
  const [meta, setMeta] = useState(null) // Canon §1.5: envelope meta (as_of_ts etc.)
  const { skip = false, enabled = true } = options
  const intervalRef = useRef(null)
  const hasFetchedRef = useRef(false)
  const paramsString = JSON.stringify(params)

  const fetchData = useCallback(async (isManualRefresh = false) => {
    if (!isConnected || skip || !endpoint || !enabled) return

    // Only show loading spinner on first fetch
    if (!hasFetchedRef.current) {
      setLoading(true)
    } else if (isManualRefresh) {
      setRefreshing(true)
    }
    
    try {
      const result = await get(endpoint, params)
      setData(result)
      if (result?.__meta) setMeta(result.__meta)
      setLastUpdated(new Date())
      setError(null)
      hasFetchedRef.current = true
    } catch (err) {
      log.debug('Poll cycle error', { event: 'poll_error', endpoint, error_message: err.message })
      setError(err.message)
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [endpoint, paramsString, isConnected, skip, enabled, get])

  // Reset hasFetched when params change
  useEffect(() => {
    hasFetchedRef.current = false
    setLoading(true)
  }, [paramsString])

  // Initial fetch
  useEffect(() => {
    fetchData()
  }, [fetchData])

  // Polling
  useEffect(() => {
    if (!enabled || !isConnected || skip) {
      if (intervalRef.current) {
        log.debug('Polling stopped', { event: 'poll_stop', endpoint })
        clearInterval(intervalRef.current)
      }
      return
    }

    log.debug('Polling started', { event: 'poll_start', endpoint, interval_ms: intervalMs })
    intervalRef.current = setInterval(() => fetchData(false), intervalMs)

    return () => {
      if (intervalRef.current) {
        log.debug('Polling stopped', { event: 'poll_stop', endpoint })
        clearInterval(intervalRef.current)
      }
    }
  }, [enabled, isConnected, skip, intervalMs, fetchData])

  // Manual refetch triggers refreshing indicator
  const refetch = useCallback(() => fetchData(true), [fetchData])

  return { data, loading, refreshing, error, lastUpdated, meta, refetch }
}

// Markets hook
export function useMarkets() {
  return useFetch('/v1/markets')
}

// Timeframes hook
export function useTimeframes() {
  return useFetch('/v1/timeframes')
}

// Strategies hook — single source of truth for all strategy dropdowns
export function useStrategies() {
  return useFetch('/v1/strategies')
}

// Opportunities hook with polling
export function useOpportunities(params = {}, enabled = true) {
  return usePolling('/v1/opportunities', params, 15000, { enabled })
}

// Single opportunity hook
export function useOpportunity(id) {
  return useFetch(`/v1/opportunities/${id}`, {}, { skip: !id })
}

// Live opportunity hook (Redis-sourced, composite key)
export function useLiveOpportunity(setup_id, symbol, tf) {
  return useFetch(
    `/v1/opportunities/live/${setup_id}/${symbol}/${tf}`,
    {},
    { skip: !setup_id || !symbol || !tf }
  )
}

// Top signals hook with polling
export function useTopSignals(params = {}, enabled = true) {
  return usePolling('/v1/signals/top', params, 30000, { enabled })
}

// Runs hook
export function useRuns(params = {}) {
  return useFetch('/v1/runs', params)
}

// Latest run hook
export function useLatestRun(runType = 'SCAN') {
  return useFetch('/v1/runs/latest', { run_type: runType }, { showError: false })
}

// Single run hook
export function useRun(id) {
  return useFetch(`/v1/runs/${id}`, {}, { skip: !id })
}

// Run trades hook
export function useRunTrades(runId, params = {}) {
  return useFetch(`/v1/runs/${runId}/trades`, params, { skip: !runId })
}

// Run events hook
export function useRunEvents(runId, params = {}) {
  return useFetch(`/v1/runs/${runId}/events`, params, { skip: !runId })
}

// Queue status hook
export function useQueueStatus() {
  return usePolling('/v1/control/queue', {}, 15000)
}

// Paper status hook with polling
export function usePaperStatus(enabled = true) {
  return usePolling('/v1/control/paper/status', {}, 5000, { enabled })
}

// Trading account hook with polling
export function useTradingAccount(enabled = true) {
  return usePolling('/v1/trading/account', {}, 5000, { enabled })
}

// Positions hook with polling
export function usePositions(params = {}, enabled = true) {
  return usePolling('/v1/trading/positions', params, 5000, { enabled })
}

// Position detail hook
export function usePosition(id) {
  return useFetch(`/v1/trading/positions/${id}`, {}, { skip: !id })
}

// Trades hook
export function useTrades(params = {}) {
  return useFetch('/v1/trading/trades', params)
}

// Performance hook
export function usePerformance() {
  return useFetch('/v1/trading/performance')
}

// OHLCV hook
export function useOHLCV(symbol, tf, limit = 100) {
  return useFetch('/v1/ohlcv', { symbol, tf, limit }, { skip: !symbol || !tf })
}

// Live snapshot hook with fast polling
export function useSnapshot(symbol, enabled = true) {
  return usePolling('/v1/snapshot/minute', { symbol, timeframe: '1m' }, 5000, { 
    enabled: enabled && !!symbol,
    skip: !symbol 
  })
}

// Squeeze state hook
export function useSqueezeState(symbol, tf) {
  return useFetch('/v1/squeeze/state', { symbol, tf }, { skip: !symbol || !tf, showError: false })
}

// BTC squeeze status hook
export function useBtcSqueezeStatus(tf) {
  return useFetch('/v1/btc/squeeze-status', { tf }, { skip: !tf, showError: false })
}

// Data availability hook (P5-27)
export function useDataAvailability(symbols, timeframes, options = {}) {
  const params = {}
  if (symbols?.length) params.symbols = symbols.join(',')
  if (timeframes?.length) params.timeframes = timeframes.join(',')
  const skip = !symbols?.length || !timeframes?.length || options.skip
  return useFetch('/v1/control/data/availability', params, { skip, showError: false })
}
