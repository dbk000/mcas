import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { usePolling } from '../../hooks/useData'
import { normalizeRequestError } from '../../api/errors'
import { downloadResponseBlob } from '../../api/downloads'
import { averageDailyPF } from '../../utils/pf'
import { usePublicJson } from '../../utils/publicJson'
import { getPaginationLimit } from '../../config/governed'

export const PAGE_SIZE = getPaginationLimit('simulation_results_default')

const EMPTY_RESULTS = []
const EMPTY_RECORD = {}
const DEMO_SIMULATION_RESULTS = {
  simulation_results: {
    summary: {},
    results: [],
    expected: {
      pnl_field: 'pnl_net',
    },
  },
}

export function useSimulationResultsViewModel({ addToast, request, runId }) {
  const exportAbortRef = useRef(null)
  const [offset, setOffset] = useState(0)
  const [isExporting, setIsExporting] = useState(false)
  const { data: metricsDemo } = usePublicJson('/curation/metrics-demo.v1.json', DEMO_SIMULATION_RESULTS)

  const pollingState = usePolling(
    `/v1/runs/${runId}/simulation-results`,
    { limit: PAGE_SIZE, offset },
    5000,
    { enabled: Boolean(runId), skip: !runId }
  )

  const { data, loading, lastUpdated, refetch, meta } = pollingState
  const demoSimulationResults = metricsDemo?.simulation_results ?? DEMO_SIMULATION_RESULTS.simulation_results
  const liveResults = data?.results
  const results = useMemo(() => liveResults ?? EMPTY_RESULTS, [liveResults])
  const displayResults = useMemo(
    () => (results.length > 0 ? results : (demoSimulationResults.results ?? EMPTY_RESULTS)),
    [demoSimulationResults.results, results]
  )
  const summary = useMemo(
    () => data?.summary ?? demoSimulationResults.summary ?? EMPTY_RECORD,
    [data?.summary, demoSimulationResults.summary]
  )
  const totalCount = data?.total_count ?? displayResults.length
  const runStatus = data?.run_status
  const isDemoMode = results.length === 0
  const displayRunId = runId?.slice(0, 8) || 'unknown'
  const pnlField = demoSimulationResults.expected?.pnl_field || 'pnl_net'

  const derivedSummary = useMemo(() => {
    const exitReasonCounts = {}
    const normalizedItems = []
    let winners = 0
    let losers = 0

    for (const item of displayResults) {
      if (item.pnl_net > 0) {
        winners += 1
      } else if (item.pnl_net <= 0 && (item.data_available ?? true)) {
        losers += 1
      }

      if (item.exit_reason) {
        exitReasonCounts[item.exit_reason] = (exitReasonCounts[item.exit_reason] || 0) + 1
      }

      normalizedItems.push({
        ...item,
        resolved_ts: item.exit_ts || item.created_at || null,
      })
    }

    return {
      avgDailyPfSummary: averageDailyPF(normalizedItems, 'resolved_ts', pnlField),
      exitReasonEntries: Object.entries(exitReasonCounts).sort((left, right) => right[1] - left[1]),
      losersCount: losers,
      winnersCount: winners,
    }
  }, [displayResults, pnlField])

  const perSetupEntries = useMemo(
    () => Object.entries(summary.per_setup ?? EMPTY_RECORD),
    [summary.per_setup]
  )
  const currentPage = Math.floor(offset / PAGE_SIZE) + 1
  const totalPages = Math.ceil(totalCount / PAGE_SIZE)

  useEffect(() => (
    () => {
      exportAbortRef.current?.abort()
    }
  ), [])

  const handlePreviousPage = useCallback(() => {
    setOffset((currentOffset) => Math.max(0, currentOffset - PAGE_SIZE))
  }, [])

  const handleNextPage = useCallback(() => {
    setOffset((currentOffset) => currentOffset + PAGE_SIZE)
  }, [])

  const handleExportCsv = useCallback(async () => {
    if (!runId) {
      addToast('Simulation run is not available for CSV export', 'error')
      return
    }

    exportAbortRef.current?.abort()
    const controller = new AbortController()
    exportAbortRef.current = controller
    setIsExporting(true)

    try {
      const response = await request(`/v1/runs/${runId}/simulation-results/csv`, {
        responseType: 'raw',
        signal: controller.signal,
      })

      await downloadResponseBlob(response, `sim_${displayRunId}.csv`)
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (!normalized.isAbort) {
        addToast(`Failed to export CSV: ${normalized.message}`, 'error')
      }
    } finally {
      if (exportAbortRef.current === controller) {
        exportAbortRef.current = null
      }
      setIsExporting(false)
    }
  }, [addToast, displayRunId, request, runId])

  return {
    currentPage,
    data,
    displayResults,
    displayRunId,
    handleExportCsv,
    handleNextPage,
    handlePreviousPage,
    isDemoMode,
    isExporting,
    lastUpdated,
    loading,
    meta,
    offset,
    perSetupEntries,
    refetch,
    runStatus,
    summary,
    totalCount,
    totalPages,
    ...derivedSummary,
  }
}
