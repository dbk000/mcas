import { useParams } from 'react-router-dom'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  Card,
  CardContent,
  Skeleton,
} from '../components/ui'
import SimulationResultsHeader from './simulation-results/SimulationResultsHeader'
import SimulationResultsSummary from './simulation-results/SimulationResultsSummary'
import SimulationResultsTable from './simulation-results/SimulationResultsTable'
import { useSimulationResultsViewModel } from './simulation-results/useSimulationResultsViewModel'

export default function SimulationResults() {
  const { id: runId } = useParams()
  const { request } = useApi()
  const { addToast } = useToast()
  const viewModel = useSimulationResultsViewModel({ addToast, request, runId })
  const {
    avgDailyPfSummary,
    currentPage,
    data,
    displayResults,
    displayRunId,
    exitReasonEntries,
    handleExportCsv,
    handleNextPage,
    handlePreviousPage,
    isDemoMode,
    isExporting,
    lastUpdated,
    loading,
    losersCount,
    meta,
    offset,
    perSetupEntries,
    refetch,
    runStatus,
    summary,
    totalCount,
    totalPages,
    winnersCount,
  } = viewModel

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Skeleton className="size-8" />
          <Skeleton variant="title" className="w-64" />
        </div>
        <div className="grid gap-4 md:grid-cols-4">
          {[1, 2, 3, 4].map(i => (
            <Card key={i}><CardContent><Skeleton /></CardContent></Card>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <SimulationResultsHeader
        displayRunId={displayRunId}
        isExporting={isExporting}
        lastUpdated={lastUpdated}
        loading={loading}
        onExportCsv={handleExportCsv}
        onRefresh={refetch}
        runId={runId}
        runStatus={runStatus}
        serverAsOf={meta?.as_of_ts}
      />

      <SimulationResultsSummary
        avgDailyPfSummary={avgDailyPfSummary}
        displayResults={displayResults}
        exitReasonEntries={exitReasonEntries}
        isDemoMode={isDemoMode}
        losersCount={losersCount}
        perSetupEntries={perSetupEntries}
        summary={summary}
        totalCount={totalCount}
        winnersCount={winnersCount}
      />

      <SimulationResultsTable
        currentPage={currentPage}
        displayResults={displayResults}
        offset={offset}
        onNextPage={handleNextPage}
        onPreviousPage={handlePreviousPage}
        runStatus={runStatus}
        totalCount={totalCount}
        totalPages={totalPages}
      />
    </div>
  )
}
