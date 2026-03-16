import { Target } from 'lucide-react'
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DirectionBadge,
  EmptyState,
} from '../../components/ui'
import { formatDollar, formatPrice } from '../../utils/formatters'
import { PAGE_SIZE } from './useSimulationResultsViewModel'

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

export default function SimulationResultsTable({
  currentPage,
  displayResults,
  offset,
  onNextPage,
  onPreviousPage,
  runStatus,
  totalCount,
  totalPages,
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <span>Individual Results</span>
          <span className="text-sm font-normal text-terminal-400">
            Showing {displayResults.length} of {totalCount}
          </span>
        </CardTitle>
      </CardHeader>
      <CardContent>
        {displayResults.length === 0 ? (
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
                  <th className="pb-2 text-right font-medium">Entry</th>
                  <th className="pb-2 text-right font-medium">Exit</th>
                  <th className="pb-2 font-medium">Exit Reason</th>
                  <th className="pb-2 text-right font-medium">Bars</th>
                  <th className="pb-2 text-right font-medium">R-Multiple</th>
                  <th className="pb-2 text-right font-medium">PnL</th>
                </tr>
              </thead>
              <tbody>
                {displayResults.map((result) => (
                  <tr
                    key={result.id}
                    className="border-b border-terminal-700 hover:bg-terminal-700/50"
                  >
                    <td className="py-3">
                      <span className="font-mono font-bold text-terminal-100">{result.symbol}</span>
                      <span className="ml-1 text-xs text-terminal-500">{result.timeframe}</span>
                    </td>
                    <td className="py-3">
                      <Badge variant="info">{result.setup_id}</Badge>
                    </td>
                    <td className="py-3">
                      <DirectionBadge direction={result.direction} />
                    </td>
                    <td className="py-3 text-right font-mono text-terminal-100">
                      {result.entry_price ? formatPrice(result.entry_price) : '-'}
                    </td>
                    <td className="py-3 text-right font-mono text-terminal-100">
                      {result.exit_price ? formatPrice(result.exit_price) : '-'}
                    </td>
                    <td className="py-3">
                      {result.exit_reason ? (
                        <ExitReasonBadge reason={result.exit_reason} />
                      ) : result.data_available ? (
                        <span className="text-terminal-500">-</span>
                      ) : (
                        <Badge variant="warning">No Data</Badge>
                      )}
                    </td>
                    <td className="py-3 text-right font-mono text-terminal-400">
                      {result.bars_held ?? '-'}
                    </td>
                    <td className="py-3 text-right">
                      {result.r_multiple != null ? (
                        <span className={`font-mono font-bold ${result.r_multiple >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                          {result.r_multiple >= 0 ? '+' : ''}{result.r_multiple.toFixed(2)}R
                        </span>
                      ) : (
                        <span className="text-terminal-500">-</span>
                      )}
                    </td>
                    <td className="py-3 text-right">
                      {result.pnl_net != null ? (
                        <span className={`font-mono ${result.pnl_net >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                          {formatDollar(result.pnl_net)}
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

        {totalCount > PAGE_SIZE && (
          <div className="mt-4 flex items-center justify-center gap-4 border-t border-terminal-600 pt-4">
            <button
              onClick={onPreviousPage}
              disabled={offset === 0}
              className="btn btn-ghost btn-sm"
            >
              Previous
            </button>
            <span className="text-sm text-terminal-400">
              Page {currentPage} of {totalPages}
            </span>
            <button
              onClick={onNextPage}
              disabled={offset + PAGE_SIZE >= totalCount}
              className="btn btn-ghost btn-sm"
            >
              Next
            </button>
          </div>
        )}
      </CardContent>
    </Card>
  )
}
