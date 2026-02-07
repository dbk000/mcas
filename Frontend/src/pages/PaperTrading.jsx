import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import { getLogger } from '../utils/logger'

const log = getLogger('paper_trading')
import { 
  useTradingAccount, usePositions, useTrades, 
  usePerformance, usePaperStatus 
} from '../hooks/useData'
import { 
  Card, CardHeader, CardTitle, CardContent,
  Badge, DirectionBadge, StatusBadge, StatCard,
  Skeleton, SkeletonCard, EmptyState, RefreshControl, 
  Tabs, Modal, CopyButton
} from '../components/ui'
import { 
  formatPrice, formatPnL, formatPercent, formatR, 
  formatDateTime, formatRelativeTime, formatTradePlan, 
  getPnLColor 
} from '../utils/formatters'
import { 
  Wallet, TrendingUp, TrendingDown, Target, Activity,
  DollarSign, BarChart2, Clock, AlertCircle, XCircle,
  ChevronRight, Eye, Settings
} from 'lucide-react'

// Account overview panel
function AccountOverview({ account, performance, isActive }) {
  if (!isActive) {
    return (
      <div className="p-8 text-center">
        <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-terminal-800 flex items-center justify-center">
          <Activity className="w-8 h-8 text-terminal-500" />
        </div>
        <h3 className="text-lg font-medium text-terminal-300 mb-2">No Active Session</h3>
        <p className="text-terminal-500 mb-4">Start a paper trading session from the Control Plane.</p>
        <Link
          to="/control"
          className="inline-flex items-center gap-2 px-4 py-2 bg-amber-500 hover:bg-amber-400 text-terminal-900 font-medium rounded-lg transition-colors"
        >
          <Settings className="w-4 h-4" />
          Go to Control Plane
        </Link>
      </div>
    )
  }
  
  const returnPct = account?.total_return_pct || 0
  const isProfit = returnPct >= 0
  
  return (
    <div className="space-y-6">
      {/* Main stats row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
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
      
      {/* Secondary stats */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div className="p-4 bg-terminal-800/50 rounded-lg">
          <div className="text-xs text-terminal-500 mb-1">Total Trades</div>
          <div className="text-xl font-mono font-bold text-terminal-100">
            {performance?.total_trades || 0}
          </div>
        </div>
        <div className="p-4 bg-terminal-800/50 rounded-lg">
          <div className="text-xs text-terminal-500 mb-1">Max Drawdown</div>
          <div className="text-xl font-mono font-bold text-red-400">
            {formatPercent(account?.max_drawdown_pct || 0)}
          </div>
        </div>
        <div className="p-4 bg-terminal-800/50 rounded-lg">
          <div className="text-xs text-terminal-500 mb-1">Daily P&L</div>
          <div className={`text-xl font-mono font-bold ${getPnLColor(account?.daily_pnl || 0)}`}>
            {formatPnL(account?.daily_pnl || 0)}
          </div>
        </div>
        <div className="p-4 bg-terminal-800/50 rounded-lg">
          <div className="text-xs text-terminal-500 mb-1">Fees Paid</div>
          <div className="text-xl font-mono font-bold text-terminal-300">
            {formatPrice(account?.total_fees || 0)}
          </div>
        </div>
      </div>
      
      {/* Exposure bar */}
      <div className="p-4 bg-terminal-800/50 rounded-lg">
        <div className="flex items-center justify-between mb-2">
          <span className="text-sm text-terminal-400">Capital Allocation</span>
          <span className="text-sm font-mono text-terminal-300">
            {formatPrice(account?.current_exposure || 0)} / {formatPrice(account?.current_capital || 0)}
          </span>
        </div>
        <div className="h-2 bg-terminal-700 rounded-full overflow-hidden">
          <div 
            className="h-full bg-amber-500 rounded-full transition-all"
            style={{ 
              width: `${Math.min(100, ((account?.current_exposure || 0) / (account?.current_capital || 1)) * 100)}%` 
            }}
          />
        </div>
        <div className="flex items-center justify-between mt-1 text-xs text-terminal-500">
          <span>Exposed: {formatPercent(((account?.current_exposure || 0) / (account?.current_capital || 1)) * 100)}</span>
          <span>Available: {formatPrice(account?.available_capital || 0)}</span>
        </div>
      </div>
    </div>
  )
}

// Position card component
function PositionCard({ position, onClose }) {
  const isOpen = position.status === 'OPEN'
  const pnl = isOpen ? position.unrealized_pnl : position.realized_pnl
  const pnlColor = getPnLColor(pnl)
  
  return (
    <div className="p-4 bg-terminal-800/50 rounded-lg border border-terminal-700/50 hover:border-terminal-600/50 transition-colors">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="font-mono font-bold text-terminal-100">{position.symbol}</span>
          <DirectionBadge direction={position.side} />
          <Badge variant="default">{position.setup_id}</Badge>
        </div>
        <StatusBadge status={position.status} />
      </div>
      
      <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm mb-3">
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
      
      <div className="flex items-center justify-between pt-3 border-t border-terminal-700/50">
        <div>
          <span className="text-terminal-500 text-sm">
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
            className="flex items-center gap-1 px-3 py-1.5 text-sm text-red-400 hover:text-red-300 hover:bg-red-400/10 rounded transition-colors"
          >
            <XCircle className="w-4 h-4" />
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

// Positions tab
function PositionsTab({ isActive }) {
  const { post } = useApi()
  const { addToast } = useToast()
  const [statusFilter, setStatusFilter] = useState('OPEN')
  const [closingPosition, setClosingPosition] = useState(null)
  const [closing, setClosing] = useState(false)
  
  const { data: positions, loading, refetch } = usePositions({ status: statusFilter }, isActive)
  
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
    } catch (err) {
      log.error('Position close failed', { event: 'position_close_error', position_id: closingPosition.id, error_message: err.message })
      addToast(`Failed to close position: ${err.message}`, 'error')
    } finally {
      setClosing(false)
    }
  }
  
  if (!isActive) {
    return (
      <EmptyState
        icon={Activity}
        title="No Active Session"
        description="Start a paper trading session to see positions"
      />
    )
  }
  
  return (
    <div className="space-y-4">
      {/* Filter */}
      <div className="flex items-center gap-2">
        <span className="text-sm text-terminal-400">Status:</span>
        {['OPEN', 'CLOSED', 'ALL'].map((status) => (
          <button
            key={status}
            onClick={() => setStatusFilter(status)}
            className={`px-3 py-1 text-sm rounded-full transition-colors ${
              statusFilter === status
                ? 'bg-amber-500 text-terminal-900'
                : 'bg-terminal-800 text-terminal-300 hover:bg-terminal-700'
            }`}
          >
            {status}
          </button>
        ))}
      </div>
      
      {/* Positions grid */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3, 4].map((i) => <SkeletonCard key={i} />)}
        </div>
      ) : !positions?.positions || positions.positions.length === 0 ? (
        <EmptyState
          icon={Target}
          title={`No ${statusFilter.toLowerCase()} positions`}
          description={statusFilter === 'OPEN' ? 'Waiting for signals...' : 'No trades in history'}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {positions.positions.map((position) => (
            <PositionCard
              key={position.id}
              position={position}
              onClose={statusFilter === 'OPEN' ? setClosingPosition : null}
            />
          ))}
        </div>
      )}
      
      {/* Close confirmation modal */}
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
            <div className="p-3 bg-terminal-800/50 rounded-lg">
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
                className="flex-1 px-4 py-2 bg-terminal-700 hover:bg-terminal-600 text-terminal-200 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleClose}
                disabled={closing}
                className="flex-1 px-4 py-2 bg-red-500 hover:bg-red-400 text-white rounded-lg transition-colors disabled:opacity-50"
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

// Trades tab
function TradesTab({ isActive }) {
  const [limit, setLimit] = useState(50)
  const { data: trades, loading } = useTrades({ limit }, isActive)
  
  if (!isActive) {
    return (
      <EmptyState
        icon={Activity}
        title="No Active Session"
        description="Start a paper trading session to see trades"
      />
    )
  }
  
  if (loading) {
    return (
      <div className="space-y-2">
        {[1, 2, 3, 4, 5].map((i) => (
          <Skeleton key={i} className="h-12" />
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
            <th className="px-3 py-2 text-left text-terminal-400 font-medium">Symbol</th>
            <th className="px-3 py-2 text-left text-terminal-400 font-medium">Side</th>
            <th className="px-3 py-2 text-left text-terminal-400 font-medium">Setup</th>
            <th className="px-3 py-2 text-right text-terminal-400 font-medium">Entry</th>
            <th className="px-3 py-2 text-right text-terminal-400 font-medium">Exit</th>
            <th className="px-3 py-2 text-left text-terminal-400 font-medium">Reason</th>
            <th className="px-3 py-2 text-right text-terminal-400 font-medium">P&L</th>
            <th className="px-3 py-2 text-right text-terminal-400 font-medium">R</th>
            <th className="px-3 py-2 text-right text-terminal-400 font-medium">Time</th>
          </tr>
        </thead>
        <tbody>
          {trades.trades.map((trade) => (
            <tr key={trade.id} className="border-b border-terminal-800 hover:bg-terminal-800/30">
              <td className="px-3 py-2 font-mono font-medium text-terminal-100">
                {trade.symbol}
              </td>
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
                      : trade.exit_reason === 'STOP' 
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

// Performance tab
function PerformanceTab({ isActive }) {
  const { data: perf, loading } = usePerformance()
  
  if (!isActive) {
    return (
      <EmptyState
        icon={Activity}
        title="No Active Session"
        description="Start a paper trading session to see performance"
      />
    )
  }
  
  if (loading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    )
  }
  
  if (!perf || perf.error) {
    return (
      <EmptyState
        icon={BarChart2}
        title="No performance data"
        description={perf?.error || 'Complete some trades to see analytics'}
      />
    )
  }
  
  const overall = perf.overall || {}
  
  return (
    <div className="space-y-6">
      {/* Overall metrics */}
      <Card>
        <CardHeader>
          <CardTitle>Overall Statistics</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Total Trades</div>
              <div className="text-2xl font-mono font-bold text-terminal-100">{overall.total_trades || 0}</div>
            </div>
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Win Rate</div>
              <div className="text-2xl font-mono font-bold text-terminal-100">
                {formatPercent((overall.win_rate || 0) * 100)}
              </div>
            </div>
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Best Trade</div>
              <div className={`text-2xl font-mono font-bold ${getPnLColor(overall.best_r || 0)}`}>
                {formatR(overall.best_r || 0)}
              </div>
            </div>
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Worst Trade</div>
              <div className={`text-2xl font-mono font-bold ${getPnLColor(overall.worst_r || 0)}`}>
                {formatR(overall.worst_r || 0)}
              </div>
            </div>
          </div>
          
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Avg Win</div>
              <div className="text-lg font-mono text-green-400">{formatPnL(overall.avg_win || 0)}</div>
            </div>
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Avg Loss</div>
              <div className="text-lg font-mono text-red-400">{formatPnL(overall.avg_loss || 0)}</div>
            </div>
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Avg R-Multiple</div>
              <div className={`text-lg font-mono ${getPnLColor(overall.avg_r || 0)}`}>
                {formatR(overall.avg_r || 0)}
              </div>
            </div>
            <div className="p-3 bg-terminal-800/50 rounded-lg">
              <div className="text-xs text-terminal-500 mb-1">Avg Bars Held</div>
              <div className="text-lg font-mono text-terminal-100">
                {(overall.avg_bars_held || 0).toFixed(1)}
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
      
      {/* By setup */}
      {perf.by_setup && perf.by_setup.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Performance by Setup</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-3">
              {perf.by_setup.map((setup) => (
                <div key={setup.setup_id} className="flex items-center justify-between p-3 bg-terminal-800/50 rounded-lg">
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
      
      {/* Top symbols */}
      {perf.top_symbols && perf.top_symbols.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Top Symbols by P&L</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {perf.top_symbols.map((sym, idx) => (
                <div key={sym.symbol} className="flex items-center justify-between p-3 bg-terminal-800/50 rounded-lg">
                  <div className="flex items-center gap-3">
                    <span className="w-6 text-center text-terminal-500 text-sm">#{idx + 1}</span>
                    <span className="font-mono font-medium text-terminal-100">{sym.symbol}</span>
                    <span className="text-terminal-500 text-sm">{sym.trades} trades</span>
                  </div>
                  <div className="flex items-center gap-4">
                    <div className={`font-mono ${getPnLColor(sym.avg_r || 0)}`}>
                      {formatR(sym.avg_r || 0)} avg
                    </div>
                    <div className={`font-mono font-bold ${getPnLColor(sym.pnl || 0)}`}>
                      {formatPnL(sym.pnl || 0)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
      
      {/* Daily P&L */}
      {perf.daily_pnl && perf.daily_pnl.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Daily P&L</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              {perf.daily_pnl.slice(-14).map((day) => (
                <div key={day.date} className="flex items-center gap-2">
                  <span className="text-sm text-terminal-500 w-24">{day.date}</span>
                  <span className="text-sm text-terminal-400 w-16">{day.trades} trades</span>
                  <div className="flex-1 h-5 relative">
                    <div 
                      className={`absolute h-full ${day.pnl >= 0 ? 'bg-green-500/50' : 'bg-red-500/50'} rounded`}
                      style={{
                        width: `${Math.min(100, Math.abs(day.pnl) / 100)}%`,
                        left: day.pnl >= 0 ? '50%' : undefined,
                        right: day.pnl < 0 ? '50%' : undefined,
                      }}
                    />
                    <div className="absolute left-1/2 top-0 bottom-0 w-px bg-terminal-600" />
                  </div>
                  <span className={`text-sm font-mono w-24 text-right ${getPnLColor(day.pnl)}`}>
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

// Main page component
export default function PaperTrading() {
  const [activeTab, setActiveTab] = useState('account')
  
  const { data: accountData, loading: accountLoading, refetch: refetchAccount } = useTradingAccount()
  const { data: paperStatus } = usePaperStatus()
  
  const isActive = accountData?.active || false
  const account = accountData?.account || {}
  const performance = accountData?.performance || {}
  
  const tabs = [
    { value: 'account', label: 'Account', icon: Wallet },
    { value: 'positions', label: 'Positions', icon: Target },
    { value: 'trades', label: 'Trades', icon: Clock },
    { value: 'performance', label: 'Performance', icon: BarChart2 },
  ]
  
  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-terminal-100">Paper Trading</h1>
            {isActive ? (
              <span className="flex items-center gap-1.5 px-2 py-0.5 bg-green-500/20 text-green-400 text-sm rounded-full">
                <span className="w-1.5 h-1.5 bg-green-400 rounded-full animate-pulse" />
                Active
              </span>
            ) : (
              <span className="px-2 py-0.5 bg-terminal-700 text-terminal-400 text-sm rounded-full">
                Inactive
              </span>
            )}
          </div>
          {isActive && accountData?.run_name && (
            <p className="text-terminal-400">
              Session: <span className="font-mono text-terminal-300">{accountData.run_name}</span>
              {accountData.started_at && (
                <span className="ml-2 text-terminal-500">
                  (started {formatRelativeTime(accountData.started_at)})
                </span>
              )}
            </p>
          )}
        </div>
        
        <RefreshControl
          lastUpdated={null}
          onRefresh={refetchAccount}
        />
      </div>
      
      {/* Tabs */}
      <Card>
        <CardHeader className="border-b border-terminal-700">
          <Tabs
            tabs={tabs}
            activeTab={activeTab}
            onChange={setActiveTab}
          />
        </CardHeader>
        <CardContent className="pt-6">
          {activeTab === 'account' && (
            <AccountOverview
              account={account}
              performance={performance}
              isActive={isActive}
            />
          )}
          {activeTab === 'positions' && (
            <PositionsTab isActive={isActive} />
          )}
          {activeTab === 'trades' && (
            <TradesTab isActive={isActive} />
          )}
          {activeTab === 'performance' && (
            <PerformanceTab isActive={isActive} />
          )}
        </CardContent>
      </Card>
    </div>
  )
}
