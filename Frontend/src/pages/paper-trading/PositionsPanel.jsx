import { useState } from 'react'
import { Activity, Target, XCircle } from 'lucide-react'
import { useApi } from '../../context/ApiContext'
import { useToast } from '../../context/ToastContext'
import { usePositions } from '../../hooks/useData'
import { getLogger } from '../../utils/logger'
import {
  Badge,
  DirectionBadge,
  StatusBadge,
  SkeletonCard,
  EmptyState,
  Modal,
} from '../../components/ui'
import {
  formatPrice,
  formatPnL,
  formatR,
  getPnLColor,
} from '../../utils/formatters'

const log = getLogger('paper_trading')

function PositionCard({ position, onClose }) {
  const isOpen = position.status === 'OPEN'
  const pnl = isOpen ? position.unrealized_pnl : position.realized_pnl
  const pnlColor = getPnLColor(pnl)

  return (
    <div className="rounded-lg border border-terminal-700/50 bg-terminal-800/50 p-4 transition-colors hover:border-terminal-600/50">
      <div className="mb-3 flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className="font-mono font-bold text-terminal-100">{position.symbol}</span>
          <DirectionBadge direction={position.side} />
          <Badge variant="default">{position.setup_id}</Badge>
        </div>
        <StatusBadge status={position.status} />
      </div>

      <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
        <div>
          <span className="text-terminal-500">Entry:</span>
          <span className="ml-2 font-mono text-terminal-200">{formatPrice(position.entry_price)}</span>
        </div>
        <div>
          <span className="text-terminal-500">Stop:</span>
          <span className="ml-2 font-mono text-red-400">{formatPrice(position.stop_price)}</span>
        </div>
        <div>
          <span className="text-terminal-500">TP1:</span>
          <span className="ml-2 font-mono text-green-400">{formatPrice(position.tp1_price)}</span>
        </div>
        <div>
          <span className="text-terminal-500">Current:</span>
          <span className="ml-2 font-mono text-terminal-200">{formatPrice(position.current_price)}</span>
        </div>
      </div>

      <div className="flex items-center justify-between border-t border-terminal-700/50 pt-3">
        <div>
          <span className="text-sm text-terminal-500">
            {isOpen ? 'Unrealized' : 'Realized'} P&L:
          </span>
          <span className={`ml-2 font-mono font-bold ${pnlColor}`}>
            {formatPnL(pnl)}
          </span>
          {position.r_multiple !== null && (
            <span className={`ml-2 text-sm ${pnlColor}`}>
              ({formatR(position.r_multiple)})
            </span>
          )}
        </div>

        {isOpen && onClose && (
          <button
            onClick={() => onClose(position)}
            className="flex items-center gap-1 rounded px-3 py-1.5 text-sm text-red-400 transition-colors hover:bg-red-400/10 hover:text-red-300"
          >
            <XCircle className="size-4" />
            Close
          </button>
        )}

        {!isOpen && position.exit_reason && (
          <Badge variant={position.exit_reason === 'TP1' || position.exit_reason === 'TP2' ? 'success' : 'error'}>
            {position.exit_reason}
          </Badge>
        )}
      </div>

      {isOpen && position.bars_held > 0 && (
        <div className="mt-2 text-xs text-terminal-500">
          Bars held: {position.bars_held}
        </div>
      )}
    </div>
  )
}

function ActivePositionsPanel() {
  const { post } = useApi()
  const { addToast } = useToast()
  const [statusFilter, setStatusFilter] = useState('OPEN')
  const [closingPosition, setClosingPosition] = useState(null)
  const [closing, setClosing] = useState(false)
  const { data: positions, loading, refetch } = usePositions({ status: statusFilter })

  const handleClose = async () => {
    if (!closingPosition) return

    log.info('Closing position', { event: 'position_close_start', position_id: closingPosition.id, symbol: closingPosition.symbol })
    setClosing(true)
    try {
      const result = await post(`/v1/trading/positions/${closingPosition.id}/close`, {})
      log.info('Position closed', { event: 'position_closed', position_id: closingPosition.id, symbol: closingPosition.symbol })
      addToast(`Closed ${closingPosition.symbol} at ${formatPrice(result.exit_price)} (${formatR(result.r_multiple)})`, 'success')
      setClosingPosition(null)
      refetch()
    } catch (error) {
      log.error('Position close failed', { event: 'position_close_error', position_id: closingPosition.id, error_message: error.message })
      addToast(`Failed to close position: ${error.message}`, 'error')
    } finally {
      setClosing(false)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-sm text-terminal-400">Status:</span>
        {['OPEN', 'CLOSED', 'ALL'].map((status) => (
          <button
            key={status}
            onClick={() => setStatusFilter(status)}
            className={`rounded-full px-3 py-1 text-sm transition-colors ${
              statusFilter === status
                ? 'bg-amber-500 text-terminal-900'
                : 'bg-terminal-800 text-terminal-300 hover:bg-terminal-700'
            }`}
          >
            {status}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {[1, 2, 3, 4].map((index) => <SkeletonCard key={index} />)}
        </div>
      ) : !positions?.positions || positions.positions.length === 0 ? (
        <EmptyState
          icon={Target}
          title={`No ${statusFilter.toLowerCase()} positions`}
          description={statusFilter === 'OPEN' ? 'Waiting for signals...' : 'No trades in history'}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {positions.positions.map((position) => (
            <PositionCard
              key={position.id}
              position={position}
              onClose={statusFilter === 'OPEN' ? setClosingPosition : null}
            />
          ))}
        </div>
      )}

      <Modal
        isOpen={!!closingPosition}
        onClose={() => setClosingPosition(null)}
        title="Close Position"
      >
        {closingPosition && (
          <div className="space-y-4">
            <p className="text-terminal-300">
              Close <strong>{closingPosition.symbol}</strong> at market price?
            </p>
            <div className="rounded-lg bg-terminal-800/50 p-3">
              <div className="flex items-center justify-between text-sm">
                <span className="text-terminal-500">Entry:</span>
                <span className="font-mono">{formatPrice(closingPosition.entry_price)}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-terminal-500">Current:</span>
                <span className="font-mono">{formatPrice(closingPosition.current_price)}</span>
              </div>
              <div className="flex items-center justify-between text-sm">
                <span className="text-terminal-500">Unrealized P&L:</span>
                <span className={`font-mono ${getPnLColor(closingPosition.unrealized_pnl)}`}>
                  {formatPnL(closingPosition.unrealized_pnl)}
                </span>
              </div>
            </div>
            <div className="flex gap-3">
              <button
                onClick={() => setClosingPosition(null)}
                className="flex-1 rounded-lg bg-terminal-700 px-4 py-2 text-terminal-200 transition-colors hover:bg-terminal-600"
              >
                Cancel
              </button>
              <button
                onClick={handleClose}
                disabled={closing}
                className="flex-1 rounded-lg bg-red-500 px-4 py-2 text-white transition-colors hover:bg-red-400 disabled:opacity-50"
              >
                {closing ? 'Closing...' : 'Close Position'}
              </button>
            </div>
          </div>
        )}
      </Modal>
    </div>
  )
}

export default function PositionsPanel({ isActive }) {
  if (!isActive) {
    return (
      <EmptyState
        icon={Activity}
        title="No Active Session"
        description="Start a paper trading session to see positions"
      />
    )
  }

  return <ActivePositionsPanel />
}
