import { BarChart2 } from 'lucide-react'
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from '../../components/ui'
import { formatDollar, formatPercent } from '../../utils/formatters'

function StatCard({ label, value, subValue, variant }) {
  const colorClass = variant === 'positive'
    ? 'text-signal-long'
    : variant === 'negative'
      ? 'text-signal-short'
      : 'text-terminal-100'

  return (
    <div className="rounded-lg bg-terminal-700 p-4">
      <span className="text-xs uppercase tracking-wide text-terminal-500">{label}</span>
      <p className={`font-mono text-2xl font-bold ${colorClass}`}>{value}</p>
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
    HORIZON_EXIT: 'info',
  }

  return <Badge variant={variants[reason] || 'default'}>{reason}</Badge>
}

export default function SimulationResultsSummary({
  avgDailyPfSummary,
  displayResults,
  exitReasonEntries,
  isDemoMode,
  losersCount,
  perSetupEntries,
  summary,
  totalCount,
  winnersCount,
}) {
  return (
    <>
      <div className="grid gap-4 md:grid-cols-5">
        <StatCard
          label="Win Rate"
          value={formatPercent(summary.win_rate || 0)}
          subValue={`${winnersCount}W / ${losersCount}L`}
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
        <StatCard
          label="Avg Daily PF"
          value={avgDailyPfSummary.value === Infinity
            ? '∞'
            : Number.isFinite(avgDailyPfSummary.value)
              ? avgDailyPfSummary.value.toFixed(2)
              : '—'}
          subValue={avgDailyPfSummary.daysCountedFinite > 0
            ? `${avgDailyPfSummary.daysCountedFinite} finite days`
            : avgDailyPfSummary.daysExcludedInfiniteProfitable > 0
              ? `${avgDailyPfSummary.daysExcludedInfiniteProfitable} zero-loss days excluded`
              : 'Unavailable'}
          variant={avgDailyPfSummary.value === Infinity || avgDailyPfSummary.value >= 1 ? 'positive' : 'negative'}
        />
      </div>

      <div className="rounded-lg border border-terminal-700/50 bg-terminal-800/40 p-4 text-sm text-terminal-300">
        Avg Daily PF uses realized PnL grouped by day, preferring <code className="font-mono text-terminal-100">exit_ts</code> and falling back to <code className="font-mono text-terminal-100">created_at</code> when needed. Fully profitable zero-loss days are excluded from the finite mean.
        {isDemoMode && ' Showing the authored preview set because no live simulation results are available yet.'}
      </div>

      {perSetupEntries.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart2 className="size-5 text-amber-400" />
              Per-Setup Breakdown
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 md:grid-cols-3">
              {perSetupEntries.map(([setup, stats]) => (
                <div key={setup} className="rounded-lg bg-terminal-700 p-4">
                  <div className="mb-2 flex items-center justify-between">
                    <Badge variant="info">{setup}</Badge>
                    <span className="text-sm text-terminal-400">{stats.trades} trades</span>
                  </div>
                  <div className="grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <span className="text-terminal-500">Win Rate</span>
                      <p className="font-mono text-terminal-100">{formatPercent(stats.win_rate)}</p>
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

      {exitReasonEntries.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Exit Reasons</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex flex-wrap gap-3">
              {exitReasonEntries.map(([reason, count]) => (
                <div key={reason} className="flex items-center gap-2 rounded bg-terminal-700 px-3 py-2">
                  <ExitReasonBadge reason={reason} />
                  <span className="font-mono text-terminal-100">{count}</span>
                  <span className="text-xs text-terminal-500">
                    ({formatPercent(count / displayResults.length)})
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </>
  )
}
