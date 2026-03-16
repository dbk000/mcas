import { Activity, BarChart2 } from 'lucide-react'
import { usePerformance } from '../../hooks/useData'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  EmptyState,
  SkeletonCard,
} from '../../components/ui'
import {
  formatPercent,
  formatPnL,
  formatR,
  getPnLColor,
} from '../../utils/formatters'

function ActivePerformancePanel() {
  const { data: performance, loading } = usePerformance()

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    )
  }

  if (!performance || performance.error) {
    return (
      <EmptyState
        icon={BarChart2}
        title="No performance data"
        description={performance?.error || 'Complete some trades to see analytics'}
      />
    )
  }

  const overall = performance.overall || {}

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Overall Statistics</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Total Trades</div>
              <div className="font-mono text-2xl font-bold text-terminal-100">{overall.total_trades || 0}</div>
            </div>
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Win Rate</div>
              <div className="font-mono text-2xl font-bold text-terminal-100">
                {formatPercent((overall.win_rate || 0) * 100)}
              </div>
            </div>
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Best Trade</div>
              <div className={`font-mono text-2xl font-bold ${getPnLColor(overall.best_r || 0)}`}>
                {formatR(overall.best_r || 0)}
              </div>
            </div>
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Worst Trade</div>
              <div className={`font-mono text-2xl font-bold ${getPnLColor(overall.worst_r || 0)}`}>
                {formatR(overall.worst_r || 0)}
              </div>
            </div>
          </div>

          <div className="mt-4 grid grid-cols-2 gap-4 md:grid-cols-4">
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Avg Win</div>
              <div className="font-mono text-lg text-green-400">{formatPnL(overall.avg_win || 0)}</div>
            </div>
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Avg Loss</div>
              <div className="font-mono text-lg text-red-400">{formatPnL(overall.avg_loss || 0)}</div>
            </div>
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Avg R-Multiple</div>
              <div className={`font-mono text-lg ${getPnLColor(overall.avg_r || 0)}`}>
                {formatR(overall.avg_r || 0)}
              </div>
            </div>
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="mb-1 text-xs text-terminal-500">Avg Bars Held</div>
              <div className="font-mono text-lg text-terminal-100">
                {(overall.avg_bars_held || 0).toFixed(1)}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {performance.by_setup && performance.by_setup.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Performance by Setup</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {performance.by_setup.map((setup) => (
                <div key={setup.setup_id} className="flex items-center justify-between rounded-lg bg-terminal-800/50 p-3">
                  <div className="flex items-center gap-3">
                    <Badge variant="info">{setup.setup_id}</Badge>
                    <span className="text-terminal-300">{setup.trades} trades</span>
                  </div>
                  <div className="flex items-center gap-6">
                    <div className="text-right">
                      <div className="text-xs text-terminal-500">Win Rate</div>
                      <div className="font-mono text-terminal-100">
                        {formatPercent((setup.win_rate || 0) * 100)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-xs text-terminal-500">Avg R</div>
                      <div className={`font-mono ${getPnLColor(setup.avg_r || 0)}`}>
                        {formatR(setup.avg_r || 0)}
                      </div>
                    </div>
                    <div className="text-right">
                      <div className="text-xs text-terminal-500">P&L</div>
                      <div className={`font-mono font-medium ${getPnLColor(setup.pnl || 0)}`}>
                        {formatPnL(setup.pnl || 0)}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {performance.top_symbols && performance.top_symbols.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Top Symbols by P&L</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {performance.top_symbols.map((symbol, index) => (
                <div key={symbol.symbol} className="flex items-center justify-between rounded-lg bg-terminal-800/50 p-3">
                  <div className="flex items-center gap-3">
                    <span className="w-6 text-center text-sm text-terminal-500">#{index + 1}</span>
                    <span className="font-mono font-medium text-terminal-100">{symbol.symbol}</span>
                    <span className="text-sm text-terminal-500">{symbol.trades} trades</span>
                  </div>
                  <div className="flex items-center gap-4">
                    <div className={`font-mono ${getPnLColor(symbol.avg_r || 0)}`}>
                      {formatR(symbol.avg_r || 0)} avg
                    </div>
                    <div className={`font-mono font-bold ${getPnLColor(symbol.pnl || 0)}`}>
                      {formatPnL(symbol.pnl || 0)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {performance.daily_pnl && performance.daily_pnl.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Daily P&L</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              {performance.daily_pnl.slice(-14).map((day) => (
                <div key={day.date} className="flex items-center gap-2">
                  <span className="w-24 text-sm text-terminal-500">{day.date}</span>
                  <span className="w-16 text-sm text-terminal-400">{day.trades} trades</span>
                  <div className="relative h-5 flex-1">
                    <div
                      className={`absolute h-full rounded ${day.pnl >= 0 ? 'bg-green-500/50' : 'bg-red-500/50'}`}
                      style={{
                        width: `${Math.min(100, Math.abs(day.pnl) / 100)}%`,
                        left: day.pnl >= 0 ? '50%' : undefined,
                        right: day.pnl < 0 ? '50%' : undefined,
                      }}
                    />
                    <div className="absolute inset-y-0 left-1/2 w-px bg-terminal-600" />
                  </div>
                  <span className={`w-24 text-right font-mono text-sm ${getPnLColor(day.pnl)}`}>
                    {formatPnL(day.pnl)}
                  </span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}

export default function PerformancePanel({ isActive }) {
  if (!isActive) {
    return (
      <EmptyState
        icon={Activity}
        title="No Active Session"
        description="Start a paper trading session to see performance"
      />
    )
  }

  return <ActivePerformancePanel />
}
