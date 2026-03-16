import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  JsonView,
  DataRow,
} from '../../components/ui'
import { averageDailyPF } from '../../utils/pf'
import {
  formatDateTime,
  formatPnL,
  formatR,
  formatNumber,
  formatPercent,
  formatDollar,
} from '../../utils/formatters'
import RunStatCard from './RunStatCard'
import { formatAverageDailyPfValue } from './runDetailUtils'

export default function RunOverviewTab({ run, trades, metricsDemo }) {
  const summary = run.summary || {}
  const isBacktest = run.run_type === 'BACKTEST'
  const isSimulation = run.run_type === 'SIMULATION'
  const hasStats = isBacktest || isSimulation
  const liveTrades = useMemo(() => (Array.isArray(trades) ? trades : []), [trades])
  const demoTrades = useMemo(() => metricsDemo?.run_detail?.trades || [], [metricsDemo?.run_detail?.trades])
  const timestampField = metricsDemo?.run_detail?.expected?.timestamp_field || 'exit_ts'
  const pnlField = metricsDemo?.run_detail?.expected?.pnl_field || 'pnl_net'
  const usingDemoTrades = hasStats && liveTrades.length === 0
  const averageDailyPfSummary = useMemo(() => {
    const sourceTrades = liveTrades.length > 0 ? liveTrades : demoTrades
    return averageDailyPF(sourceTrades, timestampField, pnlField)
  }, [demoTrades, liveTrades, pnlField, timestampField])
  const avgDailyPfSubtext = averageDailyPfSummary.daysCountedFinite > 0
    ? `${averageDailyPfSummary.daysCountedFinite} finite day${averageDailyPfSummary.daysCountedFinite === 1 ? '' : 's'}`
    : averageDailyPfSummary.daysExcludedInfiniteProfitable > 0
      ? `${averageDailyPfSummary.daysExcludedInfiniteProfitable} zero-loss day${averageDailyPfSummary.daysExcludedInfiniteProfitable === 1 ? '' : 's'}`
      : 'Unavailable'

  return (
    <div className="space-y-6" data-operator-surface>
      {hasStats && summary.total_trades > 0 && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-7">
            <RunStatCard
              label="Net PnL"
              value={formatPnL(summary.net_pnl)}
              color={summary.net_pnl >= 0 ? 'text-signal-long' : 'text-signal-short'}
            />
            <RunStatCard
              label="Return"
              value={formatPercent(summary.return_pct)}
              color={summary.return_pct >= 0 ? 'text-signal-long' : 'text-signal-short'}
            />
            <RunStatCard
              label="Win Rate"
              value={formatPercent(summary.win_rate * 100)}
              sub={`${summary.winning_trades}W / ${summary.losing_trades}L`}
            />
            <RunStatCard
              label="Profit Factor"
              value={formatNumber(summary.profit_factor)}
              color={summary.profit_factor >= 1 ? 'text-signal-long' : 'text-signal-short'}
            />
            <RunStatCard
              label="Avg Daily PF"
              value={formatAverageDailyPfValue(averageDailyPfSummary.value)}
              sub={avgDailyPfSubtext}
              color={averageDailyPfSummary.value === Infinity || averageDailyPfSummary.value >= 1 ? 'text-signal-long' : 'text-signal-short'}
            />
            <RunStatCard
              label="Max Drawdown"
              value={formatPercent(summary.max_drawdown_pct)}
              color="text-signal-short"
            />
            <RunStatCard
              label="Total Trades"
              value={summary.total_trades}
              sub={`${summary.signals_generated} signals`}
            />
          </div>

          <div className="rounded-lg border border-terminal-700/50 bg-terminal-800/40 p-4 text-sm text-terminal-300">
            Avg Daily PF uses realized per-trade PnL grouped by day. Fully profitable zero-loss days are excluded from the finite daily average to avoid meaningless infinite arithmetic means.
            {usingDemoTrades && ' Showing the authored preview set because realized trade rows are not available on this run payload.'}
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
            <RunStatCard label="Avg R" value={formatR(summary.avg_r)} color={summary.avg_r >= 0 ? 'text-signal-long' : 'text-signal-short'} />
            <RunStatCard label="Total R" value={formatR(summary.total_r)} />
            <RunStatCard label="Best R" value={formatR(summary.max_r)} color="text-signal-long" />
            <RunStatCard label="Worst R" value={formatR(summary.min_r)} color="text-signal-short" />
            <RunStatCard label="Sharpe" value={formatNumber(summary.sharpe_ratio, 3)} />
            <RunStatCard label="Sortino" value={formatNumber(summary.sortino_ratio, 3)} />
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
            <RunStatCard label="Calmar" value={formatNumber(summary.calmar_ratio, 3)} />
            <RunStatCard label="Payoff Ratio" value={formatNumber(summary.payoff_ratio, 2)} />
            <RunStatCard label="Avg Win" value={formatR(summary.avg_win_r)} color="text-signal-long" />
            <RunStatCard label="Avg Loss" value={formatR(summary.avg_loss_r)} color="text-signal-short" />
            <RunStatCard label="Max Con. Wins" value={summary.max_consecutive_wins} />
            <RunStatCard label="Max Con. Losses" value={summary.max_consecutive_losses} />
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <RunStatCard label="Starting Capital" value={formatDollar(summary.starting_capital)} />
            <RunStatCard
              label="Final Equity"
              value={formatDollar(summary.final_equity)}
              color={summary.final_equity >= summary.starting_capital ? 'text-signal-long' : 'text-signal-short'}
            />
            <RunStatCard label="Total Fees" value={formatDollar(summary.total_fees)} color="text-amber-400" />
            <RunStatCard label="Total Slippage" value={formatDollar(summary.total_slippage)} color="text-amber-400" />
          </div>

          {summary.per_setup && Object.keys(summary.per_setup).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Per-Setup Breakdown</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-terminal-700 text-terminal-500">
                        <th className="py-2 pr-4 text-left">Setup</th>
                        <th className="px-4 py-2 text-right">Trades</th>
                        <th className="px-4 py-2 text-right">Win Rate</th>
                        <th className="px-4 py-2 text-right">Net PnL</th>
                        <th className="px-4 py-2 text-right">Total R</th>
                        <th className="py-2 pl-4 text-right">Avg R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(summary.per_setup).map(([setupId, data]) => (
                        <tr key={setupId} className="border-b border-terminal-800">
                          <td className="py-2 pr-4 font-mono font-bold text-terminal-100">{setupId}</td>
                          <td className="px-4 py-2 text-right">{data.trades}</td>
                          <td className="px-4 py-2 text-right">{formatPercent(data.win_rate * 100)}</td>
                          <td className={`px-4 py-2 text-right font-mono ${data.pnl_net >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>{formatPnL(data.pnl_net)}</td>
                          <td className="px-4 py-2 text-right font-mono">{formatR(data.total_r)}</td>
                          <td className="py-2 pl-4 text-right font-mono">{formatR(data.avg_r)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}

          {summary.per_symbol && Object.keys(summary.per_symbol).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Per-Symbol Breakdown (Top 20)</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-terminal-700 text-terminal-500">
                        <th className="py-2 pr-4 text-left">Symbol</th>
                        <th className="px-4 py-2 text-right">Trades</th>
                        <th className="px-4 py-2 text-right">Win Rate</th>
                        <th className="px-4 py-2 text-right">Net PnL</th>
                        <th className="px-4 py-2 text-right">Total R</th>
                        <th className="py-2 pl-4 text-right">Avg R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(summary.per_symbol)
                        .sort(([, a], [, b]) => Math.abs(b.pnl_net) - Math.abs(a.pnl_net))
                        .map(([symbol, data]) => (
                          <tr key={symbol} className="border-b border-terminal-800">
                            <td className="py-2 pr-4 font-mono font-bold text-terminal-100">{symbol}</td>
                            <td className="px-4 py-2 text-right">{data.trades}</td>
                            <td className="px-4 py-2 text-right">{formatPercent(data.win_rate * 100)}</td>
                            <td className={`px-4 py-2 text-right font-mono ${data.pnl_net >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>{formatPnL(data.pnl_net)}</td>
                            <td className="px-4 py-2 text-right font-mono">{formatR(data.total_r)}</td>
                            <td className="py-2 pl-4 text-right font-mono">{formatR(data.avg_r)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <RunStatCard label="Avg Holding" value={`${formatNumber(summary.avg_holding_bars, 1)} bars`} />
            <RunStatCard label="Bars Processed" value={summary.bars_processed?.toLocaleString()} />
            <RunStatCard label="Assets Processed" value={summary.assets_processed} sub={summary.assets_skipped ? `${summary.assets_skipped} skipped` : undefined} />
            <RunStatCard label="Execution Time" value={`${formatNumber(summary.execution_time_seconds, 1)}s`} />
          </div>
        </>
      )}

      {(!hasStats || !summary.total_trades) && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle>Timeline</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <DataRow label="Created" value={formatDateTime(run.created_at)} />
              <DataRow label="Started" value={formatDateTime(run.started_at)} />
              <DataRow label="Finished" value={formatDateTime(run.finished_at)} />
              <DataRow label="Engine Version" value={run.engine_version || '—'} />
              {run.parent_run_id && (
                <DataRow
                  label="Parent Run"
                  value={(
                    <Link to={`/app/runs/${run.parent_run_id}`} className="text-amber-400 hover:underline">
                      {run.parent_run_id.slice(0, 8)}...
                    </Link>
                  )}
                />
              )}
            </CardContent>
          </Card>

          {run.summary && Object.keys(run.summary).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Summary</CardTitle>
              </CardHeader>
              <CardContent>
                <JsonView data={run.summary} expanded />
              </CardContent>
            </Card>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {hasStats && summary.total_trades > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Timeline</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <DataRow label="Created" value={formatDateTime(run.created_at)} />
              <DataRow label="Started" value={formatDateTime(run.started_at)} />
              <DataRow label="Finished" value={formatDateTime(run.finished_at)} />
              <DataRow label="Engine Version" value={run.engine_version || '—'} />
            </CardContent>
          </Card>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Universe</CardTitle>
          </CardHeader>
          <CardContent>
            <JsonView data={run.universe} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Strategy</CardTitle>
          </CardHeader>
          <CardContent>
            <JsonView data={run.strategy} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Execution</CardTitle>
          </CardHeader>
          <CardContent>
            <JsonView data={run.execution} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Data</CardTitle>
          </CardHeader>
          <CardContent>
            <JsonView data={run.data_spec || run.data} />
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
