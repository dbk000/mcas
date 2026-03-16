import { Link } from 'react-router-dom'
import {
  Wallet,
  TrendingUp,
  TrendingDown,
  Target,
  Activity,
  BarChart2,
  Settings,
} from 'lucide-react'
import { StatCard } from '../../components/ui'
import {
  formatPrice,
  formatPnL,
  formatPercent,
  formatR,
  getPnLColor,
} from '../../utils/formatters'

export default function AccountOverview({ account, performance, isActive }) {
  if (!isActive) {
    return (
      <div className="p-8 text-center">
        <div className="mx-auto mb-4 flex size-16 items-center justify-center rounded-full bg-terminal-800">
          <Activity className="size-8 text-terminal-500" />
        </div>
        <h3 className="mb-2 text-lg font-medium text-terminal-300">No Active Session</h3>
        <p className="mb-4 text-terminal-500">Start a paper trading session from the Control Plane.</p>
        <Link
          to="/app/control"
          className="inline-flex items-center gap-2 rounded-lg bg-amber-500 px-4 py-2 font-medium text-terminal-900 transition-colors hover:bg-amber-400"
        >
          <Settings className="size-4" />
          Go to Control Plane
        </Link>
      </div>
    )
  }

  const returnPct = account?.total_return_pct || 0
  const isProfit = returnPct >= 0

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <StatCard
          icon={Wallet}
          label="Current Capital"
          value={formatPrice(account?.current_capital || 0)}
          subValue={`Initial: ${formatPrice(account?.initial_capital || 0)}`}
        />
        <StatCard
          icon={isProfit ? TrendingUp : TrendingDown}
          label="Total Return"
          value={formatPercent(returnPct)}
          valueColor={getPnLColor(returnPct)}
          subValue={formatPnL(account?.total_pnl || 0)}
        />
        <StatCard
          icon={Target}
          label="Win Rate"
          value={formatPercent((performance?.win_rate || 0) * 100)}
          subValue={`${performance?.winning_trades || 0}W / ${performance?.losing_trades || 0}L`}
        />
        <StatCard
          icon={BarChart2}
          label="Avg R-Multiple"
          value={formatR(performance?.average_r || 0)}
          subValue={`PF: ${(performance?.profit_factor || 0).toFixed(2)}`}
        />
      </div>

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <div className="rounded-lg bg-terminal-800/50 p-4">
          <div className="mb-1 text-xs text-terminal-500">Total Trades</div>
          <div className="font-mono text-xl font-bold text-terminal-100">
            {performance?.total_trades || 0}
          </div>
        </div>
        <div className="rounded-lg bg-terminal-800/50 p-4">
          <div className="mb-1 text-xs text-terminal-500">Max Drawdown</div>
          <div className="font-mono text-xl font-bold text-red-400">
            {formatPercent(account?.max_drawdown_pct || 0)}
          </div>
        </div>
        <div className="rounded-lg bg-terminal-800/50 p-4">
          <div className="mb-1 text-xs text-terminal-500">Daily P&L</div>
          <div className={`font-mono text-xl font-bold ${getPnLColor(account?.daily_pnl || 0)}`}>
            {formatPnL(account?.daily_pnl || 0)}
          </div>
        </div>
        <div className="rounded-lg bg-terminal-800/50 p-4">
          <div className="mb-1 text-xs text-terminal-500">Fees Paid</div>
          <div className="font-mono text-xl font-bold text-terminal-300">
            {formatPrice(account?.total_fees || 0)}
          </div>
        </div>
      </div>

      <div className="rounded-lg bg-terminal-800/50 p-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-sm text-terminal-400">Capital Allocation</span>
          <span className="font-mono text-sm text-terminal-300">
            {formatPrice(account?.current_exposure || 0)} / {formatPrice(account?.current_capital || 0)}
          </span>
        </div>
        <div className="h-2 overflow-hidden rounded-full bg-terminal-700">
          <div
            className="h-full rounded-full bg-amber-500 transition-all"
            style={{
              width: `${Math.min(100, ((account?.current_exposure || 0) / (account?.current_capital || 1)) * 100)}%`,
            }}
          />
        </div>
        <div className="mt-1 flex items-center justify-between text-xs text-terminal-500">
          <span>Exposed: {formatPercent(((account?.current_exposure || 0) / (account?.current_capital || 1)) * 100)}</span>
          <span>Available: {formatPrice(account?.available_capital || 0)}</span>
        </div>
      </div>
    </div>
  )
}
