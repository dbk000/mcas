import { useState, useEffect, useCallback } from 'react'
import {
  BarChart3,
  TrendingDown,
  TrendingUp,
  AlertCircle,
  Search,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  Filter,
  Lightbulb
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { useFetch } from '../hooks/useData'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Badge,
  StatusBadge,
  Skeleton,
  EmptyState,
  Select,
  Spinner
} from '../components/ui'
import { formatRelativeTime } from '../utils/formatters'
import { getLogger } from '../utils/logger'

const log = getLogger('correlation')

export default function CorrelationAnalysis() {
  const { post, get, isConnected } = useApi()
  const { addToast } = useToast()

  // State for run selection
  const [selectedRuns, setSelectedRuns] = useState([])
  const [setupFilter, setSetupFilter] = useState('')
  const [minTrades, setMinTrades] = useState(30)

  // State for analysis
  const [analysisId, setAnalysisId] = useState(null)
  const [analysisStatus, setAnalysisStatus] = useState(null)
  const [analysisData, setAnalysisData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [polling, setPolling] = useState(false)

  // Fetch available backtest and simulation runs
  const { data: backtestData, loading: backtestLoading } = useFetch('/v1/runs', {
    run_type: 'BACKTEST',
    status: 'SUCCESS',
    limit: 50,
  })
  const { data: simulationData, loading: simulationLoading } = useFetch('/v1/runs', {
    run_type: 'SIMULATION',
    status: 'SUCCESS',
    limit: 50,
  })
  const runsLoading = backtestLoading || simulationLoading
  const availableRuns = [
    ...(backtestData?.runs || []),
    ...(simulationData?.runs || []),
  ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at))

  // Poll for analysis results
  useEffect(() => {
    if (!analysisId || !polling) return

    const interval = setInterval(async () => {
      try {
        const result = await get(`/v1/analytics/correlation/${analysisId}`)
        setAnalysisStatus(result.status)

        if (result.status === 'SUCCESS') {
          setAnalysisData(result)
          setPolling(false)
          log.info('Correlation analysis complete', {
            event: 'correlation_complete',
            analysis_id: analysisId,
            total_trades: result.summary?.total_trades,
          })
        } else if (result.status === 'FAILED') {
          setPolling(false)
          addToast(`Analysis failed: ${result.error_text || 'Unknown error'}`, 'error')
          log.error('Correlation analysis failed', {
            event: 'correlation_failed',
            analysis_id: analysisId,
            error: result.error_text,
          })
        }
      } catch (err) {
        log.warn('Poll error', { event: 'correlation_poll_error', error_message: err.message })
      }
    }, 2000)

    return () => clearInterval(interval)
  }, [analysisId, polling, get, addToast])

  // Handle run selection
  const toggleRun = (runId) => {
    setSelectedRuns(prev =>
      prev.includes(runId)
        ? prev.filter(id => id !== runId)
        : [...prev, runId]
    )
  }

  // Start analysis
  const handleAnalyze = async () => {
    if (selectedRuns.length === 0) {
      addToast('Please select at least one run to analyze', 'error')
      return
    }

    setLoading(true)
    setAnalysisData(null)
    setAnalysisStatus(null)

    try {
      const response = await post('/v1/analytics/correlation', {
        run_ids: selectedRuns,
        setup_id: setupFilter || null,
        min_trades: minTrades,
      })

      setAnalysisId(response.analysis_id)

      if (response.cached) {
        // Fetch cached result
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
    } catch (err) {
      addToast(`Failed to start analysis: ${err.message}`, 'error')
      log.error('Correlation analysis error', { event: 'correlation_error', error_message: err.message })
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-terminal-100">Correlation Analysis</h1>
        <p className="text-sm text-terminal-400 mt-1">
          Identify patterns in losing trades by analyzing indicator and signal components
        </p>
      </div>

      {/* Run Selector */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Filter className="w-4 h-4" />
            Select Backtest Runs
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {runsLoading ? (
            <div className="space-y-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          ) : availableRuns.length === 0 ? (
            <p className="text-sm text-terminal-400">No backtest runs available. Run a backtest first.</p>
          ) : (
            <div className="space-y-2 max-h-64 overflow-y-auto">
              {availableRuns.map(run => (
                <label
                  key={run.id}
                  className={`
                    flex items-center gap-3 p-3 rounded-lg cursor-pointer transition-colors
                    ${selectedRuns.includes(run.id)
                      ? 'bg-amber-600/20 border border-amber-600/50'
                      : 'bg-terminal-900 hover:bg-terminal-700 border border-transparent'
                    }
                  `}
                >
                  <input
                    type="checkbox"
                    checked={selectedRuns.includes(run.id)}
                    onChange={() => toggleRun(run.id)}
                    className="w-4 h-4 rounded border-terminal-500 bg-terminal-800 text-amber-500 focus:ring-amber-500"
                  />
                  <Badge
                    variant={run.run_type === 'BACKTEST' ? 'warning' : 'info'}
                    className="text-xs"
                  >
                    {run.run_type === 'BACKTEST' ? 'BT' : 'SIM'}
                  </Badge>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-terminal-100 truncate">{run.name}</p>
                    <p className="text-xs text-terminal-400">
                      {run.summary?.total_trades || run.summary?.simulated_count || 0} trades | {formatRelativeTime(run.created_at)}
                    </p>
                  </div>
                  {run.summary && (
                    <div className="text-right text-xs">
                      <p className={run.summary.win_rate >= 0.5 ? 'text-signal-long' : 'text-signal-short'}>
                        {(run.summary.win_rate * 100).toFixed(1)}% WR
                      </p>
                    </div>
                  )}
                </label>
              ))}
            </div>
          )}

          {/* Filters */}
          <div className="flex flex-wrap items-center gap-4 pt-4 border-t border-terminal-600">
            <div className="flex items-center gap-2">
              <span className="text-sm text-terminal-400">Setup:</span>
              <Select
                value={setupFilter}
                onChange={(e) => setSetupFilter(e.target.value)}
                options={[
                  { value: '', label: 'All Setups' },
                  { value: 'S1', label: 'S1 (BB Squeeze)' },
                  { value: 'S2', label: 'S2 (Golden Cross)' },
                ]}
                className="w-40"
              />
            </div>
            <div className="flex items-center gap-2">
              <span className="text-sm text-terminal-400">Min Trades:</span>
              <Select
                value={minTrades}
                onChange={(e) => setMinTrades(parseInt(e.target.value))}
                options={[
                  { value: 20, label: '20' },
                  { value: 30, label: '30' },
                  { value: 50, label: '50' },
                  { value: 100, label: '100' },
                ]}
                className="w-24"
              />
            </div>
          </div>

          {/* Analyze Button */}
          <div className="flex items-center justify-between pt-4">
            <p className="text-sm text-terminal-400">
              {selectedRuns.length} run{selectedRuns.length !== 1 ? 's' : ''} selected
            </p>
            <button
              onClick={handleAnalyze}
              disabled={!isConnected || loading || polling || selectedRuns.length === 0}
              className="btn btn-primary"
            >
              {loading || polling ? (
                <>
                  <Spinner size="sm" className="mr-2" />
                  {polling ? 'Analyzing...' : 'Starting...'}
                </>
              ) : (
                <>
                  <Search className="w-4 h-4 mr-2" />
                  Analyze Correlations
                </>
              )}
            </button>
          </div>
        </CardContent>
      </Card>

      {/* Status Indicator */}
      {analysisStatus && analysisStatus !== 'SUCCESS' && (
        <Card>
          <CardContent className="flex items-center gap-4">
            <Spinner size="md" />
            <div>
              <p className="text-terminal-100">
                {analysisStatus === 'PENDING' ? 'Waiting for worker...' : 'Running analysis...'}
              </p>
              <p className="text-sm text-terminal-400">This may take a moment</p>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Results */}
      {analysisData && analysisStatus === 'SUCCESS' && (
        <AnalysisResults data={analysisData} />
      )}
    </div>
  )
}

function AnalysisResults({ data }) {
  const [expandedFeature, setExpandedFeature] = useState(null)

  const { summary, top_predictors, feature_analyses, exit_reason_breakdown, insights } = data

  return (
    <div className="space-y-6">
      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="text-center py-4">
            <p className="text-3xl font-bold text-terminal-100">{summary?.total_trades || 0}</p>
            <p className="text-sm text-terminal-400">Total Trades</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-4">
            <p className="text-3xl font-bold text-signal-long">{summary?.winning_trades || 0}</p>
            <p className="text-sm text-terminal-400">Winners</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-4">
            <p className="text-3xl font-bold text-signal-short">{summary?.losing_trades || 0}</p>
            <p className="text-sm text-terminal-400">Losers</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="text-center py-4">
            <p className={`text-3xl font-bold ${(summary?.win_rate || 0) >= 0.5 ? 'text-signal-long' : 'text-signal-short'}`}>
              {((summary?.win_rate || 0) * 100).toFixed(1)}%
            </p>
            <p className="text-sm text-terminal-400">Win Rate</p>
          </CardContent>
        </Card>
      </div>

      {summary?.truncated && (
        <Card className="border-amber-600/50 bg-amber-900/10">
          <CardContent className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-amber-400" />
            <p className="text-sm text-amber-400">
              Analysis was truncated to 2000 trades. Results may not represent the full population.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Insights */}
      {insights && insights.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Lightbulb className="w-4 h-4 text-amber-400" />
              Key Insights
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {insights.map((insight, i) => (
                <li key={i} className="flex items-start gap-2 text-sm">
                  <span className="text-amber-500 mt-1">•</span>
                  <span className="text-terminal-200">{insight}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}

      {/* Top Predictors */}
      {top_predictors && top_predictors.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4" />
              Top Feature Predictors
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {top_predictors.slice(0, 10).map((pred, i) => (
                <FeatureBar
                  key={pred.feature}
                  rank={i + 1}
                  feature={pred.feature}
                  importance={pred.importance}
                  direction={pred.direction}
                  expanded={expandedFeature === pred.feature}
                  onToggle={() => setExpandedFeature(
                    expandedFeature === pred.feature ? null : pred.feature
                  )}
                  details={feature_analyses?.[pred.feature]}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Exit Reason Breakdown */}
      {exit_reason_breakdown && Object.keys(exit_reason_breakdown).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Exit Reason Breakdown</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-terminal-600">
                    <th className="text-left py-2 text-terminal-400 font-medium">Exit Reason</th>
                    <th className="text-right py-2 text-terminal-400 font-medium">Count</th>
                    <th className="text-right py-2 text-terminal-400 font-medium">Win Rate</th>
                    <th className="text-right py-2 text-terminal-400 font-medium">Avg R</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(exit_reason_breakdown)
                    .sort((a, b) => b[1].count - a[1].count)
                    .map(([reason, stats]) => (
                      <tr key={reason} className="border-b border-terminal-700">
                        <td className="py-2 text-terminal-100">{reason}</td>
                        <td className="py-2 text-right text-terminal-200 font-mono">{stats.count}</td>
                        <td className={`py-2 text-right font-mono ${stats.win_rate >= 0.5 ? 'text-signal-long' : 'text-signal-short'}`}>
                          {(stats.win_rate * 100).toFixed(1)}%
                        </td>
                        <td className={`py-2 text-right font-mono ${stats.avg_r >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                          {stats.avg_r >= 0 ? '+' : ''}{stats.avg_r?.toFixed(2)}R
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

function FeatureBar({ rank, feature, importance, direction, expanded, onToggle, details }) {
  const maxImportance = 1.5 // Scale bar to reasonable max
  const barWidth = Math.min(100, (importance / maxImportance) * 100)

  return (
    <div className="space-y-2">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 group"
      >
        <span className="w-6 text-terminal-400 text-sm">{rank}.</span>
        <div className="flex-1">
          <div className="flex items-center justify-between mb-1">
            <span className="text-sm text-terminal-100 font-mono truncate">{feature}</span>
            <div className="flex items-center gap-2">
              {direction === 'positive' ? (
                <TrendingUp className="w-4 h-4 text-signal-long" />
              ) : (
                <TrendingDown className="w-4 h-4 text-signal-short" />
              )}
              <span className="text-sm text-terminal-400 font-mono">d={importance.toFixed(2)}</span>
              {expanded ? (
                <ChevronUp className="w-4 h-4 text-terminal-400" />
              ) : (
                <ChevronDown className="w-4 h-4 text-terminal-400 opacity-0 group-hover:opacity-100 transition-opacity" />
              )}
            </div>
          </div>
          <div className="h-2 bg-terminal-700 rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full ${direction === 'positive' ? 'bg-signal-long' : 'bg-signal-short'}`}
              style={{ width: `${barWidth}%` }}
            />
          </div>
        </div>
      </button>

      {/* Expanded details */}
      {expanded && details && (
        <div className="ml-9 p-3 bg-terminal-900 rounded-lg space-y-3">
          {/* Means comparison */}
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="text-terminal-400">Winner Mean</p>
              <p className="text-signal-long font-mono">{details.winner_mean?.toFixed(4)}</p>
            </div>
            <div>
              <p className="text-terminal-400">Loser Mean</p>
              <p className="text-signal-short font-mono">{details.loser_mean?.toFixed(4)}</p>
            </div>
          </div>

          {/* Binned win rates */}
          {details.bins && details.bins.length > 0 && (
            <div>
              <p className="text-sm text-terminal-400 mb-2">Win Rate by Value Range</p>
              <div className="space-y-1">
                {details.bins.map((bin, i) => (
                  <div key={i} className="flex items-center gap-2 text-xs">
                    <span className="w-24 text-terminal-400 font-mono truncate">{bin.range}</span>
                    <div className="flex-1 h-4 bg-terminal-700 rounded overflow-hidden relative">
                      <div
                        className={`h-full ${bin.win_rate >= 0.5 ? 'bg-signal-long/60' : 'bg-signal-short/60'}`}
                        style={{ width: `${bin.win_rate * 100}%` }}
                      />
                      <span className="absolute inset-0 flex items-center justify-center text-terminal-100">
                        {(bin.win_rate * 100).toFixed(0)}%
                      </span>
                    </div>
                    <span className="w-12 text-right text-terminal-400">n={bin.count}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Insight */}
          {details.insight && (
            <p className="text-sm text-amber-400 italic">{details.insight}</p>
          )}
        </div>
      )}
    </div>
  )
}
