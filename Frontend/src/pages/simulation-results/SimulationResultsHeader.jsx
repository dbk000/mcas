import { ArrowLeft, Download } from 'lucide-react'
import { Link } from 'react-router-dom'
import { RefreshControl } from '../../components/ui'

export default function SimulationResultsHeader({
  displayRunId,
  isExporting,
  lastUpdated,
  loading,
  onExportCsv,
  onRefresh,
  runId,
  runStatus,
  serverAsOf,
}) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-4">
        <Link to={`/app/runs/${runId}`} className="btn btn-ghost btn-sm">
          <ArrowLeft className="size-4" />
        </Link>
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Simulation Results</h1>
          <p className="mt-1 text-sm text-terminal-400">
            Run {displayRunId} - {runStatus}
          </p>
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button
          onClick={onExportCsv}
          disabled={isExporting}
          className="btn btn-ghost btn-sm flex items-center gap-1 disabled:cursor-not-allowed disabled:opacity-60"
        >
          <Download className="size-4" />
          {isExporting ? 'Exporting...' : 'CSV'}
        </button>
        <RefreshControl
          lastUpdated={lastUpdated}
          onRefresh={onRefresh}
          loading={loading}
          serverAsOf={serverAsOf}
        />
      </div>
    </div>
  )
}
