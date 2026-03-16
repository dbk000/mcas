import { Activity, Clock } from 'lucide-react'
import { useTrades } from '../../hooks/useData'
import {
  Badge,
  DirectionBadge,
  EmptyState,
  Skeleton,
} from '../../components/ui'
import {
  formatPrice,
  formatPnL,
  formatR,
  formatRelativeTime,
  getPnLColor,
} from '../../utils/formatters'

function ActiveTradesPanel() {
  const { data: trades, loading } = useTrades({ limit: 50 })

  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3, 4, 5].map((index) => (
          <Skeleton key={index} className="h-12" />
        ))}
      </div>
    )
  }

  if (!trades?.trades || trades.trades.length === 0) {
    return (
      <EmptyState
        icon={Clock}
        title="No trades yet"
        description="Completed trades will appear here"
      />
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-terminal-700">
            <th className="px-3 py-2 text-left font-medium text-terminal-400">Symbol</th>
            <th className="px-3 py-2 text-left font-medium text-terminal-400">Side</th>
            <th className="px-3 py-2 text-left font-medium text-terminal-400">Setup</th>
            <th className="px-3 py-2 text-right font-medium text-terminal-400">Entry</th>
            <th className="px-3 py-2 text-right font-medium text-terminal-400">Exit</th>
            <th className="px-3 py-2 text-left font-medium text-terminal-400">Reason</th>
            <th className="px-3 py-2 text-right font-medium text-terminal-400">P&L</th>
            <th className="px-3 py-2 text-right font-medium text-terminal-400">R</th>
            <th className="px-3 py-2 text-right font-medium text-terminal-400">Time</th>
          </tr>
        </thead>
        <tbody>
          {trades.trades.map((trade) => (
            <tr key={trade.id} className="border-b border-terminal-800 hover:bg-terminal-800/30">
              <td className="px-3 py-2 font-mono font-medium text-terminal-100">{trade.symbol}</td>
              <td className="px-3 py-2">
                <DirectionBadge direction={trade.side} />
              </td>
              <td className="px-3 py-2">
                <Badge variant="default">{trade.setup_id}</Badge>
              </td>
              <td className="px-3 py-2 text-right font-mono text-terminal-300">
                {formatPrice(trade.entry_price)}
              </td>
              <td className="px-3 py-2 text-right font-mono text-terminal-300">
                {formatPrice(trade.exit_price)}
              </td>
              <td className="px-3 py-2">
                <Badge
                  variant={
                    trade.exit_reason === 'TP1' || trade.exit_reason === 'TP2'
                      ? 'success'
                      : trade.exit_reason === 'STOP_LOSS' || trade.exit_reason === 'FAIL_FAST'
                        ? 'error'
                        : 'warning'
                  }
                >
                  {trade.exit_reason}
                </Badge>
              </td>
              <td className={`px-3 py-2 text-right font-mono font-medium ${getPnLColor(trade.realized_pnl)}`}>
                {formatPnL(trade.realized_pnl)}
              </td>
              <td className={`px-3 py-2 text-right font-mono ${getPnLColor(trade.r_multiple)}`}>
                {formatR(trade.r_multiple)}
              </td>
              <td className="px-3 py-2 text-right text-terminal-500">
                {formatRelativeTime(trade.exit_ts)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function TradesPanel({ isActive }) {
  if (!isActive) {
    return (
      <EmptyState
        icon={Activity}
        title="No Active Session"
        description="Start a paper trading session to see trades"
      />
    )
  }

  return <ActiveTradesPanel />
}
