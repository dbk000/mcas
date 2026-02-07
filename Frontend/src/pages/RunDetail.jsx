import { useState, useMemo } from 'react'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  Play,
  Clock,
  Hash,
  AlertCircle,
  FileText,
  Activity,
  XCircle,
  Download,
  ArrowUpDown,
  ChevronDown,
  ChevronUp,
  BarChart2,
} from 'lucide-react'
import { useRun, useRunTrades, useRunEvents } from '../hooks/useData'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  StatusBadge,
  DirectionBadge,
  Skeleton,
  EmptyState,
  JsonView,
  DataRow,
  Modal,
  Tabs
} from '../components/ui'
import {
  formatDateTime,
  formatRelativeTime,
  formatPrice,
  formatPnL,
  formatR,
  formatNumber,
  formatPercent,
  formatDollar,
} from '../utils/formatters'

export default function RunDetail() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { post } = useApi()
  const { addToast } = useToast()

  const { data: run, loading, error, refetch } = useRun(id)
  const [activeTab, setActiveTab] = useState('overview')
  const [showCancelModal, setShowCancelModal] = useState(false)
  const [canceling, setCanceling] = useState(false)

  const handleCancel = async () => {
    setCanceling(true)
    try {
      await post(`/v1/control/runs/${id}/cancel`)
      addToast('Run canceled', 'success')
      setShowCancelModal(false)
      refetch()
    } catch (err) {
      addToast(`Failed to cancel: ${err.message}`, 'error')
    } finally {
      setCanceling(false)
    }
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton variant="title" className="w-48" />
        <Card>
          <CardContent className="space-y-4">
            <Skeleton />
            <Skeleton />
            <Skeleton className="w-2/3" />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !run) {
    return (
      <div className="text-center py-12">
        <p className="text-terminal-400">Run not found</p>
        <Link to="/runs" className="btn btn-secondary mt-4">
          <ArrowLeft className="w-4 h-4" />
          Back to Runs
        </Link>
      </div>
    )
  }

  const canCancel = run.status === 'PENDING' || run.status === 'RUNNING'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/runs" className="btn btn-ghost btn-sm">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-terminal-100">{run.name}</h1>
              <Badge
                variant={
                  run.run_type === 'SCAN' ? 'info' :
                  run.run_type === 'BACKTEST' ? 'warning' :
                  run.run_type === 'SIMULATION' ? 'success' :
                  'default'
                }
              >
                {run.run_type}
              </Badge>
              <StatusBadge status={run.status} />
            </div>
            <div className="flex items-center gap-4 text-sm text-terminal-400 mt-1">
              <span className="flex items-center gap-1 font-mono">
                <Hash className="w-3 h-3" />
                {run.fingerprint}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="w-3 h-3" />
                {formatRelativeTime(run.created_at)}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {run.run_type === 'SIMULATION' && run.status === 'SUCCESS' && (
            <Link to={`/runs/${id}/simulation`} className="btn btn-primary btn-sm">
              <BarChart2 className="w-4 h-4" />
              View Results
            </Link>
          )}
          {canCancel && (
            <button
              onClick={() => setShowCancelModal(true)}
              className="btn btn-danger btn-sm"
            >
              <XCircle className="w-4 h-4" />
              Cancel Run
            </button>
          )}
        </div>
      </div>

      {/* Error banner */}
      {run.error && (
        <div className="bg-red-900/20 border border-red-700/50 rounded-lg p-4 flex items-start gap-3">
          <AlertCircle className="w-5 h-5 text-red-400 flex-shrink-0 mt-0.5" />
          <div>
            <p className="text-sm font-medium text-red-200">Run failed</p>
            <p className="text-sm text-red-300 mt-1">{run.error}</p>
          </div>
        </div>
      )}

      {/* Tabs */}
      <Tabs
        tabs={[
          { value: 'overview', label: 'Overview' },
          { value: 'trades', label: 'Trades' },
          { value: 'events', label: 'Events' },
        ]}
        activeTab={activeTab}
        onChange={setActiveTab}
      />

      {/* Tab content */}
      {activeTab === 'overview' && <OverviewTab run={run} />}
      {activeTab === 'trades' && <TradesTab runId={id} />}
      {activeTab === 'events' && <EventsTab runId={id} />}

      {/* Cancel modal */}
      <Modal
        isOpen={showCancelModal}
        onClose={() => setShowCancelModal(false)}
        title="Cancel Run"
        size="sm"
      >
        <div className="space-y-4">
          <p className="text-terminal-200">
            Are you sure you want to cancel this run? This action cannot be undone.
          </p>
          <div className="flex gap-3 justify-end">
            <button
              onClick={() => setShowCancelModal(false)}
              className="btn btn-secondary"
            >
              Keep Running
            </button>
            <button
              onClick={handleCancel}
              disabled={canceling}
              className="btn btn-danger"
            >
              {canceling ? 'Canceling...' : 'Cancel Run'}
            </button>
          </div>
        </div>
      </Modal>
    </div>
  )
}

// P2-15: Formatted stat card
function StatCard({ label, value, sub, color }) {
  return (
    <div className="bg-terminal-800/50 rounded-lg p-3 border border-terminal-700/50">
      <div className="text-xs text-terminal-500 mb-1">{label}</div>
      <div className={`text-lg font-mono font-bold ${color || 'text-terminal-100'}`}>
        {value}
      </div>
      {sub && <div className="text-xs text-terminal-500 mt-0.5">{sub}</div>}
    </div>
  )
}

function OverviewTab({ run }) {
  const s = run.summary || {}
  const isBacktest = run.run_type === 'BACKTEST'
  const isSimulation = run.run_type === 'SIMULATION'
  const hasStats = isBacktest || isSimulation

  return (
    <div className="space-y-6">
      {/* P2-15: Formatted backtest/simulation stats */}
      {hasStats && s.total_trades > 0 && (
        <>
          {/* Key metrics row */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            <StatCard
              label="Net PnL"
              value={formatPnL(s.net_pnl)}
              color={s.net_pnl >= 0 ? 'text-signal-long' : 'text-signal-short'}
            />
            <StatCard
              label="Return"
              value={formatPercent(s.return_pct)}
              color={s.return_pct >= 0 ? 'text-signal-long' : 'text-signal-short'}
            />
            <StatCard
              label="Win Rate"
              value={formatPercent(s.win_rate * 100)}
              sub={`${s.winning_trades}W / ${s.losing_trades}L`}
            />
            <StatCard
              label="Profit Factor"
              value={formatNumber(s.profit_factor)}
              color={s.profit_factor >= 1 ? 'text-signal-long' : 'text-signal-short'}
            />
            <StatCard
              label="Max Drawdown"
              value={formatPercent(s.max_drawdown_pct)}
              color="text-signal-short"
            />
            <StatCard
              label="Total Trades"
              value={s.total_trades}
              sub={`${s.signals_generated} signals`}
            />
          </div>

          {/* R-multiple and risk metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            <StatCard label="Avg R" value={formatR(s.avg_r)} color={s.avg_r >= 0 ? 'text-signal-long' : 'text-signal-short'} />
            <StatCard label="Total R" value={formatR(s.total_r)} />
            <StatCard label="Best R" value={formatR(s.max_r)} color="text-signal-long" />
            <StatCard label="Worst R" value={formatR(s.min_r)} color="text-signal-short" />
            <StatCard label="Sharpe" value={formatNumber(s.sharpe_ratio, 3)} />
            <StatCard label="Sortino" value={formatNumber(s.sortino_ratio, 3)} />
          </div>

          {/* Extended metrics */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            <StatCard label="Calmar" value={formatNumber(s.calmar_ratio, 3)} />
            <StatCard label="Payoff Ratio" value={formatNumber(s.payoff_ratio, 2)} />
            <StatCard label="Avg Win" value={formatR(s.avg_win_r)} color="text-signal-long" />
            <StatCard label="Avg Loss" value={formatR(s.avg_loss_r)} color="text-signal-short" />
            <StatCard label="Max Con. Wins" value={s.max_consecutive_wins} />
            <StatCard label="Max Con. Losses" value={s.max_consecutive_losses} />
          </div>

          {/* Capital and costs */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Starting Capital" value={formatDollar(s.starting_capital)} />
            <StatCard label="Final Equity" value={formatDollar(s.final_equity)} color={s.final_equity >= s.starting_capital ? 'text-signal-long' : 'text-signal-short'} />
            <StatCard label="Total Fees" value={formatDollar(s.total_fees)} color="text-amber-400" />
            <StatCard label="Total Slippage" value={formatDollar(s.total_slippage)} color="text-amber-400" />
          </div>

          {/* P2-14: Per-setup breakdown */}
          {s.per_setup && Object.keys(s.per_setup).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Per-Setup Breakdown</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-terminal-500 border-b border-terminal-700">
                        <th className="text-left py-2 pr-4">Setup</th>
                        <th className="text-right py-2 px-4">Trades</th>
                        <th className="text-right py-2 px-4">Win Rate</th>
                        <th className="text-right py-2 px-4">Net PnL</th>
                        <th className="text-right py-2 px-4">Total R</th>
                        <th className="text-right py-2 pl-4">Avg R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(s.per_setup).map(([sid, data]) => (
                        <tr key={sid} className="border-b border-terminal-800">
                          <td className="py-2 pr-4 font-mono font-bold text-terminal-100">{sid}</td>
                          <td className="text-right py-2 px-4">{data.trades}</td>
                          <td className="text-right py-2 px-4">{formatPercent(data.win_rate * 100)}</td>
                          <td className={`text-right py-2 px-4 font-mono ${data.pnl_net >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>{formatPnL(data.pnl_net)}</td>
                          <td className="text-right py-2 px-4 font-mono">{formatR(data.total_r)}</td>
                          <td className="text-right py-2 pl-4 font-mono">{formatR(data.avg_r)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}

          {/* P2-14: Per-symbol breakdown */}
          {s.per_symbol && Object.keys(s.per_symbol).length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Per-Symbol Breakdown (Top 20)</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="text-terminal-500 border-b border-terminal-700">
                        <th className="text-left py-2 pr-4">Symbol</th>
                        <th className="text-right py-2 px-4">Trades</th>
                        <th className="text-right py-2 px-4">Win Rate</th>
                        <th className="text-right py-2 px-4">Net PnL</th>
                        <th className="text-right py-2 px-4">Total R</th>
                        <th className="text-right py-2 pl-4">Avg R</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(s.per_symbol)
                        .sort(([, a], [, b]) => Math.abs(b.pnl_net) - Math.abs(a.pnl_net))
                        .map(([sym, data]) => (
                          <tr key={sym} className="border-b border-terminal-800">
                            <td className="py-2 pr-4 font-mono font-bold text-terminal-100">{sym}</td>
                            <td className="text-right py-2 px-4">{data.trades}</td>
                            <td className="text-right py-2 px-4">{formatPercent(data.win_rate * 100)}</td>
                            <td className={`text-right py-2 px-4 font-mono ${data.pnl_net >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>{formatPnL(data.pnl_net)}</td>
                            <td className="text-right py-2 px-4 font-mono">{formatR(data.total_r)}</td>
                            <td className="text-right py-2 pl-4 font-mono">{formatR(data.avg_r)}</td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </div>
              </CardContent>
            </Card>
          )}

          {/* Execution info */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            <StatCard label="Avg Holding" value={`${formatNumber(s.avg_holding_bars, 1)} bars`} />
            <StatCard label="Bars Processed" value={s.bars_processed?.toLocaleString()} />
            <StatCard label="Assets Processed" value={s.assets_processed} sub={s.assets_skipped ? `${s.assets_skipped} skipped` : undefined} />
            <StatCard label="Execution Time" value={`${formatNumber(s.execution_time_seconds, 1)}s`} />
          </div>
        </>
      )}

      {/* Non-backtest/simulation or empty summary: show as before */}
      {(!hasStats || !s.total_trades) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Timeline */}
          <Card>
            <CardHeader>
              <CardTitle>Timeline</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <DataRow label="Created" value={formatDateTime(run.created_at)} />
              <DataRow label="Started" value={formatDateTime(run.started_at)} />
              <DataRow label="Finished" value={formatDateTime(run.finished_at)} />
              <DataRow label="Engine Version" value={run.engine_version || '\u2014'} />
              {run.parent_run_id && (
                <DataRow label="Parent Run" value={
                  <Link to={`/runs/${run.parent_run_id}`} className="text-amber-400 hover:underline">
                    {run.parent_run_id.slice(0, 8)}...
                  </Link>
                } />
              )}
            </CardContent>
          </Card>

          {/* Summary */}
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

      {/* Specs (collapsed for backtests/simulations with stats) */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {hasStats && s.total_trades > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Timeline</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <DataRow label="Created" value={formatDateTime(run.created_at)} />
              <DataRow label="Started" value={formatDateTime(run.started_at)} />
              <DataRow label="Finished" value={formatDateTime(run.finished_at)} />
              <DataRow label="Engine Version" value={run.engine_version || '\u2014'} />
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

// P5-29: Sortable, filterable trades tab
function TradesTab({ runId }) {
  const { data, loading, error } = useRunTrades(runId, { limit: 500 })
  const trades = data?.trades || []

  const [sortField, setSortField] = useState('entry_ts')
  const [sortDir, setSortDir] = useState('desc')
  const [filterSetup, setFilterSetup] = useState('')
  const [filterSide, setFilterSide] = useState('')
  const [filterExit, setFilterExit] = useState('')

  const toggleSort = (field) => {
    if (sortField === field) {
      setSortDir(d => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDir('desc')
    }
  }

  const SortHeader = ({ field, children }) => (
    <th
      className="text-right py-2 px-2 cursor-pointer hover:text-terminal-200 select-none"
      onClick={() => toggleSort(field)}
    >
      <span className="inline-flex items-center gap-1">
        {children}
        {sortField === field ? (
          sortDir === 'asc' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />
        ) : (
          <ArrowUpDown className="w-3 h-3 opacity-30" />
        )}
      </span>
    </th>
  )

  // Get unique values for filters
  const setups = useMemo(() => [...new Set(trades.map(t => t.setup_id).filter(Boolean))], [trades])
  const exits = useMemo(() => [...new Set(trades.map(t => t.exit_reason).filter(Boolean))], [trades])

  // Filter and sort
  const filtered = useMemo(() => {
    let result = [...trades]
    if (filterSetup) result = result.filter(t => t.setup_id === filterSetup)
    if (filterSide) result = result.filter(t => t.side === filterSide)
    if (filterExit) result = result.filter(t => t.exit_reason === filterExit)

    result.sort((a, b) => {
      let va = a[sortField] ?? 0
      let vb = b[sortField] ?? 0
      if (typeof va === 'string') va = va.toLowerCase()
      if (typeof vb === 'string') vb = vb.toLowerCase()
      if (va < vb) return sortDir === 'asc' ? -1 : 1
      if (va > vb) return sortDir === 'asc' ? 1 : -1
      return 0
    })
    return result
  }, [trades, filterSetup, filterSide, filterExit, sortField, sortDir])

  // P5-30: Export functions
  const exportCSV = () => {
    const headers = ['asset', 'timeframe', 'setup_id', 'side', 'entry_ts', 'entry_price', 'stop_price', 'tp1_price', 'exit_ts', 'exit_price', 'exit_reason', 'pnl_net', 'r_multiple', 'fees', 'slippage', 'setup_score']
    const rows = filtered.map(t => headers.map(h => t[h] ?? ''))
    const csv = [headers.join(','), ...rows.map(r => r.join(','))].join('\n')
    const blob = new Blob([csv], { type: 'text/csv' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `trades_${runId.slice(0, 8)}.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  const exportJSON = () => {
    const blob = new Blob([JSON.stringify(filtered, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `trades_${runId.slice(0, 8)}.json`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3, 4, 5].map(i => (
          <Card key={i}>
            <CardContent>
              <Skeleton />
            </CardContent>
          </Card>
        ))}
      </div>
    )
  }

  if (trades.length === 0) {
    return (
      <EmptyState
        icon={Activity}
        title="No trades"
        description="This run hasn't generated any trades yet"
      />
    )
  }

  return (
    <div className="space-y-4">
      {/* Filters and export */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={filterSetup}
          onChange={(e) => setFilterSetup(e.target.value)}
          className="bg-terminal-800 border border-terminal-600 rounded px-3 py-1.5 text-sm"
        >
          <option value="">All Setups</option>
          {setups.map(s => <option key={s} value={s}>{s}</option>)}
        </select>
        <select
          value={filterSide}
          onChange={(e) => setFilterSide(e.target.value)}
          className="bg-terminal-800 border border-terminal-600 rounded px-3 py-1.5 text-sm"
        >
          <option value="">All Sides</option>
          <option value="long">Long</option>
          <option value="short">Short</option>
        </select>
        <select
          value={filterExit}
          onChange={(e) => setFilterExit(e.target.value)}
          className="bg-terminal-800 border border-terminal-600 rounded px-3 py-1.5 text-sm"
        >
          <option value="">All Exit Types</option>
          {exits.map(e => <option key={e} value={e}>{e}</option>)}
        </select>

        <span className="text-sm text-terminal-500 ml-auto">
          {filtered.length} of {trades.length} trades
        </span>

        <button onClick={exportCSV} className="btn btn-ghost btn-sm flex items-center gap-1">
          <Download className="w-3 h-3" /> CSV
        </button>
        <button onClick={exportJSON} className="btn btn-ghost btn-sm flex items-center gap-1">
          <Download className="w-3 h-3" /> JSON
        </button>
      </div>

      {/* Table */}
      <Card>
        <CardContent className="p-0 overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-terminal-500 border-b border-terminal-700 text-xs">
                <th className="text-left py-2 px-3">Asset</th>
                <th className="text-left py-2 px-2">Setup</th>
                <th className="text-left py-2 px-2">Side</th>
                <SortHeader field="entry_price">Entry</SortHeader>
                <SortHeader field="stop_price">Stop</SortHeader>
                <SortHeader field="exit_price">Exit</SortHeader>
                <th className="text-left py-2 px-2">Reason</th>
                <SortHeader field="pnl_net">PnL</SortHeader>
                <SortHeader field="r_multiple">R</SortHeader>
                <SortHeader field="setup_score">Score</SortHeader>
              </tr>
            </thead>
            <tbody>
              {filtered.map((trade) => (
                <tr key={trade.id} className="border-b border-terminal-800 hover:bg-terminal-800/50">
                  <td className="py-2 px-3 font-mono font-bold text-terminal-100">
                    {trade.asset}
                    <span className="text-terminal-500 text-xs ml-1">{trade.timeframe}</span>
                  </td>
                  <td className="py-2 px-2">
                    <Badge variant="info" className="text-xs">{trade.setup_id}</Badge>
                  </td>
                  <td className="py-2 px-2">
                    <DirectionBadge direction={trade.side} />
                  </td>
                  <td className="text-right py-2 px-2 font-mono text-terminal-200">{formatPrice(trade.entry_price)}</td>
                  <td className="text-right py-2 px-2 font-mono text-signal-short">{formatPrice(trade.stop_price)}</td>
                  <td className="text-right py-2 px-2 font-mono text-terminal-200">{formatPrice(trade.exit_price)}</td>
                  <td className="py-2 px-2">
                    <Badge variant={
                      trade.exit_reason === 'STOP_LOSS' || trade.exit_reason === 'FAIL_FAST' ? 'error' :
                      trade.exit_reason === 'TP1' || trade.exit_reason === 'TP2' ? 'success' :
                      trade.exit_reason === 'TRAIL_STOP' ? 'warning' :
                      'default'
                    } className="text-xs">
                      {trade.exit_reason}
                    </Badge>
                  </td>
                  <td className={`text-right py-2 px-2 font-mono ${(trade.pnl_net || 0) >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                    {formatPnL(trade.pnl_net)}
                  </td>
                  <td className={`text-right py-2 px-2 font-mono ${(trade.r_multiple || 0) >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                    {formatR(trade.r_multiple)}
                  </td>
                  <td className="text-right py-2 px-2 font-mono text-amber-400">
                    {trade.setup_score ? formatNumber(trade.setup_score, 1) : '\u2014'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  )
}

function EventsTab({ runId }) {
  const [eventType, setEventType] = useState('')
  const { data, loading, error } = useRunEvents(runId, { event_type: eventType, limit: 100 })
  const events = data?.events || []

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <span className="text-sm text-terminal-400">Filter by type:</span>
        <select
          value={eventType}
          onChange={(e) => setEventType(e.target.value)}
          className="bg-terminal-800 border border-terminal-500 rounded px-3 py-1.5 text-sm"
        >
          <option value="">All</option>
          <option value="INFO">Info</option>
          <option value="SIGNAL">Signal</option>
          <option value="TRADE">Trade</option>
          <option value="ERROR">Error</option>
        </select>
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map(i => (
            <Card key={i}>
              <CardContent>
                <Skeleton />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No events"
          description="This run hasn't logged any events yet"
        />
      ) : (
        <div className="space-y-2">
          {events.map((event) => (
            <Card key={event.id}>
              <CardContent className="py-3">
                <div className="flex items-start gap-3">
                  <Badge
                    variant={
                      event.event_type === 'ERROR' ? 'error' :
                      event.event_type === 'SIGNAL' ? 'warning' :
                      event.event_type === 'TRADE' ? 'success' :
                      'default'
                    }
                    className="mt-0.5"
                  >
                    {event.event_type}
                  </Badge>
                  <div className="flex-1 min-w-0">
                    <p className="text-terminal-200">{event.message}</p>
                    <p className="text-xs text-terminal-500 mt-1">
                      {formatDateTime(event.ts)}
                    </p>
                    {event.payload && Object.keys(event.payload).length > 0 && (
                      <div className="mt-2">
                        <JsonView data={event.payload} />
                      </div>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
