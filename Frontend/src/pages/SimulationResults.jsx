import { useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, TrendingUp, TrendingDown, Target, BarChart2 } from 'lucide-react'
import { usePolling } from '../hooks/useData'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  DirectionBadge,
  Skeleton,
  EmptyState,
  RefreshControl,
} from '../components/ui'
import { formatPrice, formatDollar, formatPercent, formatRelativeTime } from '../utils/formatters'

function StatCard({ label, value, subValue, variant }) {
  const colorClass = variant === 'positive' ? 'text-signal-long' :
                     variant === 'negative' ? 'text-signal-short' :
                     'text-terminal-100'

  return (
    <div className="bg-terminal-700 rounded-lg p-4">
      <span className="text-xs text-terminal-500 uppercase tracking-wide">{label}</span>
      <p className={`text-2xl font-bold font-mono ${colorClass}`}>{value}</p>
      {subValue && <span className="text-xs text-terminal-400">{subValue}</span>}
    </div>
  )
}

function ExitReasonBadge({ reason }) {
  const variants = {
    STOP_LOSS: 'error',
    FAIL_FAST: 'error',
    TIME_STOP: 'warning',
    TRAIL_STOP: 'success',
    TP1: 'success',
    TP2: 'success',
  }
  return <Badge variant={variants[reason] || 'default'}>{reason}</Badge>
}

export default function SimulationResults() {
  const { id: runId } = useParams()
  const [limit] = useState(100)
  const [offset, setOffset] = useState(0)

  const { data, loading, lastUpdated, refetch, meta } = usePolling(
    `/v1/runs/${runId}/simulation-results`,
    { limit, offset },
    5000
  )

  const summary = data?.summary || {}
  const results = data?.results || []
  const totalCount = data?.total_count || 0
  const runStatus = data?.run_status

  const winners = results.filter(r => r.pnl_net > 0)
  const losers = results.filter(r => r.pnl_net <= 0 && r.data_available)

  // Exit reason distribution
  const exitReasons = {}
  results.forEach(r => {
    if (r.exit_reason) {
      exitReasons[r.exit_reason] = (exitReasons[r.exit_reason] || 0) + 1
    }
  })

  // Per-setup breakdown
  const perSetup = summary.per_setup || {}

  if (loading && !data) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Skeleton className="w-8 h-8" />
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
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to={`/runs/${runId}`} className="btn btn-ghost btn-sm">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-terminal-100">Simulation Results</h1>
            <p className="text-sm text-terminal-400 mt-1">
              Run {runId?.slice(0, 8)} - {runStatus}
            </p>
          </div>
        </div>
        <RefreshControl lastUpdated={lastUpdated} onRefresh={refetch} loading={loading} serverAsOf={meta?.as_of_ts} />
      </div>

      {/* Summary Stats */}
      <div className="grid gap-4 md:grid-cols-4">
        <StatCard
          label="Win Rate"
          value={formatPercent(summary.win_rate || 0)}
          subValue={`${winners.length}W / ${losers.length}L`}
          variant={(summary.win_rate || 0) >= 0.5 ? 'positive' : 'negative'}
        />
        <StatCard
          label="Total R"
          value={`${(summary.total_r || 0).toFixed(1)}R`}
          subValue={`Avg: ${(summary.avg_r || 0).toFixed(2)}R`}
          variant={(summary.total_r || 0) >= 0 ? 'positive' : 'negative'}
        />
        <StatCard
          label="Net PnL"
          value={formatDollar(summary.total_pnl_net || summary.net_pnl || 0)}
          subValue={`@ $${summary.position_size_usd || 1000} per trade`}
          variant={(summary.total_pnl_net || summary.net_pnl || 0) >= 0 ? 'positive' : 'negative'}
        />
        <StatCard
          label="Profit Factor"
          value={(summary.profit_factor || 0).toFixed(2)}
          subValue={`${summary.simulated_count || totalCount} simulated`}
          variant={(summary.profit_factor || 0) >= 1 ? 'positive' : 'negative'}
        />
      </div>

      {/* Per-Setup Breakdown */}
      {Object.keys(perSetup).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart2 className="w-5 h-5 text-amber-400" />
              Per-Setup Breakdown
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 md:grid-cols-3">
              {Object.entries(perSetup).map(([setup, stats]) => (
                <div key={setup} className="bg-terminal-700 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2">
                    <Badge variant="info">{setup}</Badge>
                    <span className="text-sm text-terminal-400">{stats.trades} trades</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <span className="text-terminal-500">Win Rate</span>
                      <p className="font-mono text-terminal-100">
                        {formatPercent(stats.win_rate)}
                      </p>
                    </div>
                    <div>
                      <span className="text-terminal-500">Avg R</span>
                      <p className={`font-mono ${stats.avg_r >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                        {stats.avg_r?.toFixed(2)}R
                      </p>
                    </div>
                    <div>
                      <span className="text-terminal-500">Total R</span>
                      <p className={`font-mono ${stats.total_r >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                        {stats.total_r?.toFixed(1)}R
                      </p>
                    </div>
                    <div>
                      <span className="text-terminal-500">Net PnL</span>
                      <p className={`font-mono ${stats.pnl_net >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                        {formatDollar(stats.pnl_net)}
                      </p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Exit Reason Distribution */}
      {Object.keys(exitReasons).length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Exit Reasons</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3">
              {Object.entries(exitReasons).sort((a, b) => b[1] - a[1]).map(([reason, count]) => (
                <div key={reason} className="flex items-center gap-2 bg-terminal-700 rounded px-3 py-2">
                  <ExitReasonBadge reason={reason} />
                  <span className="font-mono text-terminal-100">{count}</span>
                  <span className="text-xs text-terminal-500">
                    ({formatPercent(count / results.length)})
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Individual Results Table */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>Individual Results</span>
            <span className="text-sm font-normal text-terminal-400">
              Showing {results.length} of {totalCount}
            </span>
          </CardTitle>
        </CardHeader>
        <CardContent>
          {results.length === 0 ? (
            <EmptyState
              icon={Target}
              title="No results yet"
              description={runStatus === 'RUNNING' ? 'Simulation in progress...' : 'No simulation results found'}
            />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-terminal-600 text-left text-terminal-400">
                    <th className="pb-2 font-medium">Symbol</th>
                    <th className="pb-2 font-medium">Setup</th>
                    <th className="pb-2 font-medium">Direction</th>
                    <th className="pb-2 font-medium text-right">Entry</th>
                    <th className="pb-2 font-medium text-right">Exit</th>
                    <th className="pb-2 font-medium">Exit Reason</th>
                    <th className="pb-2 font-medium text-right">Bars</th>
                    <th className="pb-2 font-medium text-right">R-Multiple</th>
                    <th className="pb-2 font-medium text-right">PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {results.map((r) => (
                    <tr
                      key={r.id}
                      className="border-b border-terminal-700 hover:bg-terminal-700/50"
                    >
                      <td className="py-3">
                        <span className="font-mono font-bold text-terminal-100">{r.symbol}</span>
                        <span className="text-terminal-500 ml-1 text-xs">{r.timeframe}</span>
                      </td>
                      <td className="py-3">
                        <Badge variant="info">{r.setup_id}</Badge>
                      </td>
                      <td className="py-3">
                        <DirectionBadge direction={r.direction} />
                      </td>
                      <td className="py-3 text-right font-mono text-terminal-100">
                        {r.entry_price ? formatPrice(r.entry_price) : '-'}
                      </td>
                      <td className="py-3 text-right font-mono text-terminal-100">
                        {r.exit_price ? formatPrice(r.exit_price) : '-'}
                      </td>
                      <td className="py-3">
                        {r.exit_reason ? (
                          <ExitReasonBadge reason={r.exit_reason} />
                        ) : r.data_available ? (
                          <span className="text-terminal-500">-</span>
                        ) : (
                          <Badge variant="warning">No Data</Badge>
                        )}
                      </td>
                      <td className="py-3 text-right font-mono text-terminal-400">
                        {r.bars_held ?? '-'}
                      </td>
                      <td className="py-3 text-right">
                        {r.r_multiple != null ? (
                          <span className={`font-mono font-bold ${
                            r.r_multiple >= 0 ? 'text-signal-long' : 'text-signal-short'
                          }`}>
                            {r.r_multiple >= 0 ? '+' : ''}{r.r_multiple.toFixed(2)}R
                          </span>
                        ) : (
                          <span className="text-terminal-500">-</span>
                        )}
                      </td>
                      <td className="py-3 text-right">
                        {r.pnl_net != null ? (
                          <span className={`font-mono ${
                            r.pnl_net >= 0 ? 'text-signal-long' : 'text-signal-short'
                          }`}>
                            {formatDollar(r.pnl_net)}
                          </span>
                        ) : (
                          <span className="text-terminal-500">-</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {totalCount > limit && (
            <div className="flex items-center justify-center gap-4 mt-4 pt-4 border-t border-terminal-600">
              <button
                onClick={() => setOffset(Math.max(0, offset - limit))}
                disabled={offset === 0}
                className="btn btn-ghost btn-sm"
              >
                Previous
              </button>
              <span className="text-sm text-terminal-400">
                Page {Math.floor(offset / limit) + 1} of {Math.ceil(totalCount / limit)}
              </span>
              <button
                onClick={() => setOffset(offset + limit)}
                disabled={offset + limit >= totalCount}
                className="btn btn-ghost btn-sm"
              >
                Next
              </button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
