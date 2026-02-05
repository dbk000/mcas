import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Play, Filter, Clock, Hash, Search, GitCompare } from 'lucide-react'
import { useRuns } from '../hooks/useData'
import { useApi } from '../context/ApiContext'
import { 
  Card, 
  CardContent, 
  Badge, 
  StatusBadge,
  Skeleton,
  EmptyState,
  RefreshControl,
  Select,
  Input
} from '../components/ui'
import { formatRelativeTime, formatDateTime } from '../utils/formatters'

export default function Runs() {
  const [filters, setFilters] = useState({
    run_type: '',
    status: '',
    limit: 50,
  })

  const { data, loading, refetch } = useRuns(filters)
  const runs = data?.runs || []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Runs</h1>
          <p className="text-sm text-terminal-400 mt-1">
            Scan, backtest, and paper trading run history
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Link to="/runs/compare" className="btn btn-secondary btn-sm flex items-center gap-1.5">
            <GitCompare className="w-4 h-4" />
            Compare
          </Link>
          <RefreshControl onRefresh={refetch} loading={loading} />
        </div>
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 py-3">
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-terminal-400" />
            <span className="text-sm text-terminal-400">Filters:</span>
          </div>
          
          <Select
            value={filters.run_type}
            onChange={(e) => setFilters({ ...filters, run_type: e.target.value })}
            options={[
              { value: '', label: 'All Types' },
              { value: 'SCAN', label: 'Scan' },
              { value: 'BACKTEST', label: 'Backtest' },
              { value: 'PAPER', label: 'Paper Trading' },
            ]}
            className="w-36"
          />
          
          <Select
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            options={[
              { value: '', label: 'All Status' },
              { value: 'PENDING', label: 'Pending' },
              { value: 'RUNNING', label: 'Running' },
              { value: 'SUCCESS', label: 'Success' },
              { value: 'FAILED', label: 'Failed' },
              { value: 'CANCELED', label: 'Canceled' },
            ]}
            className="w-32"
          />

          <div className="ml-auto text-sm text-terminal-400">
            {runs.length} runs
          </div>
        </CardContent>
      </Card>

      {/* Fingerprint search */}
      <FingerprintSearch />

      {/* Runs list */}
      {loading && !data ? (
        <div className="space-y-4">
          {[1, 2, 3, 4, 5].map(i => (
            <Card key={i}>
              <CardContent className="flex items-center gap-4">
                <Skeleton className="w-24 h-6" />
                <Skeleton className="flex-1" />
                <Skeleton className="w-20" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : runs.length === 0 ? (
        <EmptyState
          icon={Play}
          title="No runs found"
          description="Create a scan or backtest from the Control Plane"
        />
      ) : (
        <div className="space-y-3">
          {runs.map((run) => (
            <RunCard key={run.id} run={run} />
          ))}
        </div>
      )}
    </div>
  )
}

function RunCard({ run }) {
  return (
    <Link to={`/runs/${run.id}`}>
      <Card hover>
        <CardContent className="flex items-center gap-4">
          {/* Type badge */}
          <Badge 
            variant={
              run.run_type === 'SCAN' ? 'info' : 
              run.run_type === 'BACKTEST' ? 'warning' : 
              'default'
            }
            className="w-24 justify-center"
          >
            {run.run_type}
          </Badge>

          {/* Name and details */}
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-terminal-100 truncate">
                {run.name}
              </span>
              <StatusBadge status={run.status} />
            </div>
            <div className="flex items-center gap-3 text-xs text-terminal-400 mt-1">
              {run.fingerprint && (
                <span className="flex items-center gap-1 font-mono">
                  <Hash className="w-3 h-3" />
                  {run.fingerprint.slice(0, 8)}
                </span>
              )}
              {run.created_at && (
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {formatRelativeTime(run.created_at)}
                </span>
              )}
            </div>
          </div>

          {/* Summary preview */}
          {run.summary && Object.keys(run.summary).length > 0 && (
            <div className="hidden md:block text-right">
              <p className="text-xs text-terminal-500">Summary</p>
              <p className="text-sm text-terminal-200 font-mono">
                {Object.entries(run.summary).slice(0, 2).map(([k, v]) => 
                  `${k}: ${typeof v === 'number' ? v.toFixed(1) : v}`
                ).join(' | ')}
              </p>
            </div>
          )}

          {/* Error indicator */}
          {run.error && (
            <Badge variant="error">Error</Badge>
          )}
        </CardContent>
      </Card>
    </Link>
  )
}

function FingerprintSearch() {
  const { get } = useApi()
  const [fingerprint, setFingerprint] = useState('')
  const [results, setResults] = useState(null)
  const [loading, setLoading] = useState(false)

  const handleSearch = async () => {
    if (!fingerprint.trim()) return
    
    setLoading(true)
    try {
      const data = await get(`/v1/runs/fingerprint/${fingerprint.trim()}`)
      setResults(data)
    } catch (err) {
      setResults({ runs: [], error: err.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card>
      <CardContent className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="relative flex-1">
            <Hash className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-terminal-400" />
            <Input
              type="text"
              placeholder="Search by fingerprint..."
              value={fingerprint}
              onChange={(e) => setFingerprint(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
              className="pl-9 w-full font-mono"
            />
          </div>
          <button
            onClick={handleSearch}
            disabled={loading || !fingerprint.trim()}
            className="btn btn-secondary btn-sm"
          >
            <Search className="w-4 h-4" />
            Search
          </button>
        </div>

        {results && (
          <div className="border-t border-terminal-600 pt-3">
            {results.runs?.length > 0 ? (
              <div className="space-y-2">
                <p className="text-sm text-terminal-400">
                  Found {results.runs.length} run(s) with fingerprint {results.fingerprint}
                </p>
                {results.runs.map((run) => (
                  <Link 
                    key={run.id}
                    to={`/runs/${run.id}`}
                    className="block p-2 bg-terminal-900 rounded hover:bg-terminal-700 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-terminal-100">{run.name}</span>
                      <StatusBadge status={run.status} />
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="text-sm text-terminal-400">No runs found with that fingerprint</p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}