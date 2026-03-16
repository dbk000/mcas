import { useCallback, useEffect, useMemo, useState } from 'react'
import { useFetch, useStrategies } from '../../hooks/useData'
import { getLogger } from '../../utils/logger'

const log = getLogger('correlation')
const ANALYSIS_POLL_MS = 2000
const BACKTEST_RUN_QUERY = {
  run_type: 'BACKTEST',
  status: 'SUCCESS',
  limit: 50,
}
const SIMULATION_RUN_QUERY = {
  run_type: 'SIMULATION',
  status: 'SUCCESS',
  limit: 50,
}
export const MIN_TRADE_OPTIONS = [
  { value: 20, label: '20' },
  { value: 30, label: '30' },
  { value: 50, label: '50' },
  { value: 100, label: '100' },
]

const EMPTY_ARRAY = []

export function useCorrelationAnalysis({ addToast, get, isConnected, post }) {
  const [selectedRuns, setSelectedRuns] = useState([])
  const [setupFilter, setSetupFilter] = useState('')
  const [minTrades, setMinTrades] = useState(30)
  const [analysisId, setAnalysisId] = useState(null)
  const [analysisStatus, setAnalysisStatus] = useState(null)
  const [analysisData, setAnalysisData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [polling, setPolling] = useState(false)

  const { data: strategyData } = useStrategies()
  const { data: backtestData, loading: backtestLoading } = useFetch('/v1/runs', BACKTEST_RUN_QUERY)
  const { data: simulationData, loading: simulationLoading } = useFetch('/v1/runs', SIMULATION_RUN_QUERY)

  const runsLoading = backtestLoading || simulationLoading
  const availableRuns = useMemo(() => (
    [
      ...(backtestData?.runs || EMPTY_ARRAY),
      ...(simulationData?.runs || EMPTY_ARRAY),
    ].sort((left, right) => new Date(right.created_at) - new Date(left.created_at))
  ), [backtestData?.runs, simulationData?.runs])
  const selectedRunIds = useMemo(() => new Set(selectedRuns), [selectedRuns])
  const strategyOptions = useMemo(() => ([
    { value: '', label: 'All Setups' },
    ...((strategyData?.strategies || EMPTY_ARRAY).map((strategy) => ({
      value: strategy.id,
      label: strategy.label,
    }))),
  ]), [strategyData?.strategies])
  const selectedRunCount = selectedRuns.length

  useEffect(() => {
    if (!analysisId || !polling) {
      return undefined
    }

    let cancelled = false
    let timeoutId = null

    const pollAnalysis = async () => {
      try {
        const result = await get(`/v1/analytics/correlation/${analysisId}`)
        if (cancelled) {
          return
        }

        setAnalysisStatus(result.status)

        if (result.status === 'SUCCESS') {
          setAnalysisData(result)
          setPolling(false)
          log.info('Correlation analysis complete', {
            event: 'correlation_complete',
            analysis_id: analysisId,
            total_trades: result.summary?.total_trades,
          })
          return
        }

        if (result.status === 'FAILED') {
          setPolling(false)
          addToast(`Analysis failed: ${result.error_text || 'Unknown error'}`, 'error')
          log.error('Correlation analysis failed', {
            event: 'correlation_failed',
            analysis_id: analysisId,
            error: result.error_text,
          })
          return
        }
      } catch (error) {
        if (!cancelled) {
          log.warn('Poll error', { event: 'correlation_poll_error', error_message: error.message })
        }
      }

      if (!cancelled) {
        timeoutId = window.setTimeout(() => {
          void pollAnalysis()
        }, ANALYSIS_POLL_MS)
      }
    }

    void pollAnalysis()

    return () => {
      cancelled = true
      if (timeoutId) {
        window.clearTimeout(timeoutId)
      }
    }
  }, [addToast, analysisId, get, polling])

  const toggleRun = useCallback((runId) => {
    setSelectedRuns((currentRuns) => (
      currentRuns.includes(runId)
        ? currentRuns.filter((id) => id !== runId)
        : [...currentRuns, runId]
    ))
  }, [])

  const handleAnalyze = useCallback(async () => {
    if (selectedRuns.length === 0) {
      addToast('Please select at least one run to analyze', 'error')
      return
    }

    setLoading(true)
    setAnalysisData(null)
    setAnalysisStatus(null)
    setPolling(false)

    try {
      const response = await post('/v1/analytics/correlation', {
        run_ids: selectedRuns,
        setup_id: setupFilter || null,
        min_trades: minTrades,
      })

      setAnalysisId(response.analysis_id)

      if (response.cached) {
        const result = await get(`/v1/analytics/correlation/${response.analysis_id}`)
        setAnalysisData(result)
        setAnalysisStatus('SUCCESS')
        addToast('Loaded cached analysis', 'success')
      } else {
        setAnalysisStatus('PENDING')
        setPolling(true)
        addToast('Analysis started...', 'info')
        log.info('Correlation analysis queued', {
          event: 'correlation_queued',
          analysis_id: response.analysis_id,
          run_count: selectedRuns.length,
        })
      }
    } catch (error) {
      addToast(`Failed to start analysis: ${error.message}`, 'error')
      log.error('Correlation analysis error', { event: 'correlation_error', error_message: error.message })
    } finally {
      setLoading(false)
    }
  }, [addToast, get, minTrades, post, selectedRuns, setupFilter])

  return {
    analysisData,
    analysisStatus,
    availableRuns,
    handleAnalyze,
    isConnected,
    loading,
    minTrades,
    polling,
    runsLoading,
    selectedRunCount,
    selectedRunIds,
    setupFilter,
    strategyOptions,
    toggleRun,
    setMinTrades,
    setSetupFilter,
  }
}
