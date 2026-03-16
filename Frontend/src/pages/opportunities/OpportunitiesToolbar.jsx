import {
  AlertTriangle,
  BarChart3,
  Download,
  Filter,
  PlayCircle,
  RefreshCw,
} from 'lucide-react'
import { FeedbackOnset } from '../../components/FeedbackOnset'
import {
  Card,
  CardContent,
  Input,
  RefreshControl,
  Select,
} from '../../components/ui'
import { integrityWarningLabel } from './opportunitiesUtils'

export default function OpportunitiesToolbar({
  filters,
  setFilters,
  sortBy,
  setSortBy,
  setupOptions,
  opportunitiesCount,
  selectedCount,
  lastUpdated,
  meta,
  loading,
  refetch,
  integrityStatus,
  showIntegrityWarning,
  onExportCsv,
  onOpenSurvey,
  onOpenSimulation,
}) {
  return (
    <>
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-terminal-100">Opportunities</h1>
            {showIntegrityWarning && (
              <div
                className="inline-flex items-center gap-2 rounded-full border border-red-500/30 bg-red-500/10 px-3 py-1 text-xs uppercase tracking-[0.22em] text-red-400"
                title="Data Inconsistency Detected"
              >
                <AlertTriangle className="size-3.5" />
                {integrityWarningLabel(integrityStatus)}
              </div>
            )}
          </div>
          <p className="mt-1 text-sm text-terminal-400">
            Ranked trade opportunities from latest scans
          </p>
        </div>
        <RefreshControl lastUpdated={lastUpdated} onRefresh={refetch} loading={loading} serverAsOf={meta?.as_of_ts} />
      </div>

      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 py-3">
          <div className="flex items-center gap-2">
            <Filter className="size-4 text-terminal-400" />
            <span className="text-sm text-terminal-400">Filters:</span>
          </div>

          <Select
            value={filters.status}
            onChange={(event) => setFilters({ ...filters, status: event.target.value })}
            options={[
              { value: 'active', label: 'Active' },
              { value: 'expired', label: 'Expired' },
              { value: 'traded', label: 'Traded' },
            ]}
            className="w-32"
          />

          <Select
            value={filters.setup_id}
            onChange={(event) => setFilters({ ...filters, setup_id: event.target.value })}
            options={setupOptions}
            className="w-32"
          />

          <Select
            value={sortBy}
            onChange={(event) => setSortBy(event.target.value)}
            options={[
              { value: 'score', label: 'Score' },
              { value: 'newest', label: 'Newest' },
              { value: 'expiring', label: 'Expiring Soon' },
            ]}
            className="w-40"
          />

          <div className="flex items-center gap-2">
            <span className="text-sm text-terminal-400">Min Score:</span>
            <Input
              type="number"
              value={filters.min_score}
              onChange={(event) => setFilters({ ...filters, min_score: parseFloat(event.target.value) || 0 })}
              className="w-20"
              min={0}
              step={0.1}
            />
          </div>

          <div className="flex items-center gap-2">
            <span className="text-sm text-terminal-400">Limit:</span>
            <Select
              value={filters.limit}
              onChange={(event) => setFilters({ ...filters, limit: parseInt(event.target.value, 10) })}
              options={[
                { value: 20, label: '20' },
                { value: 50, label: '50' },
                { value: 100, label: '100' },
              ]}
              className="w-20"
            />
          </div>

          <FeedbackOnset onClick={refetch} latencyLabel="Filter">
            <button className="btn btn-secondary btn-sm flex items-center gap-2">
              <RefreshCw className="size-4" />
              Apply
            </button>
          </FeedbackOnset>

          <div className="ml-auto flex items-center gap-4">
            <span className="text-sm text-terminal-400">{opportunitiesCount} opportunities</span>
            {filters.status === 'expired' && (
              <span className="text-sm text-terminal-400">{selectedCount} selected</span>
            )}
            <button
              onClick={onExportCsv}
              className="btn btn-ghost btn-sm flex items-center gap-2"
              title="Export opportunities as CSV"
            >
              <Download className="size-4" />
              Export CSV
            </button>
            {filters.status === 'expired' && (
              <button onClick={onOpenSurvey} className="btn btn-secondary btn-sm flex items-center gap-2">
                <BarChart3 className="size-4" />
                Survey Mode
              </button>
            )}
            {filters.status === 'expired' && (
              <FeedbackOnset onClick={onOpenSimulation} latencyLabel="OpenSimModal">
                <button className="btn btn-primary btn-sm flex items-center gap-2">
                  <PlayCircle className="size-4" />
                  Simulate Expired
                </button>
              </FeedbackOnset>
            )}
          </div>
        </CardContent>
      </Card>
    </>
  )
}
