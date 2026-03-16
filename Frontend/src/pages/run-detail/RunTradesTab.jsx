import { useMemo, useState } from 'react'
import { Activity, ArrowUpDown, ChevronDown, ChevronUp, Download } from 'lucide-react'
import { useRunTrades } from '../../hooks/useData'
import {
  Card,
  CardContent,
  Badge,
  DirectionBadge,
  Skeleton,
  EmptyState,
  Select,
} from '../../components/ui'
import {
  formatPrice,
  formatPnL,
  formatR,
  formatNumber,
} from '../../utils/formatters'
import { getTradeExitReasonVariant } from './runDetailUtils'

function SortHeader({ activeField, direction, field, label, onToggle }) {
  return (
    <th
      className="cursor-pointer select-none p-2 text-right hover:text-terminal-200"
      onClick={() => onToggle(field)}
    >
      <span className="inline-flex items-center gap-1">
        {label}
        {activeField === field ? (
          direction === 'asc' ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />
        ) : (
          <ArrowUpDown className="size-3 opacity-30" />
        )}
      </span>
    </th>
  )
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export default function RunTradesTab({ runId }) {
  const { data, loading } = useRunTrades(runId, { limit: 500 })
  const trades = useMemo(() => data?.trades || [], [data?.trades])
  const [sortField, setSortField] = useState('entry_ts')
  const [sortDirection, setSortDirection] = useState('desc')
  const [filterSetup, setFilterSetup] = useState('')
  const [filterSide, setFilterSide] = useState('')
  const [filterExit, setFilterExit] = useState('')

  const toggleSort = (field) => {
    if (sortField === field) {
      setSortDirection((current) => (current === 'asc' ? 'desc' : 'asc'))
      return
    }
    setSortField(field)
    setSortDirection('desc')
  }

  const setups = useMemo(() => [...new Set(trades.map((trade) => trade.setup_id).filter(Boolean))], [trades])
  const exits = useMemo(() => [...new Set(trades.map((trade) => trade.exit_reason).filter(Boolean))], [trades])

  const filteredTrades = useMemo(() => {
    const result = [...trades]
      .filter((trade) => (filterSetup ? trade.setup_id === filterSetup : true))
      .filter((trade) => (filterSide ? trade.side === filterSide : true))
      .filter((trade) => (filterExit ? trade.exit_reason === filterExit : true))

    result.sort((a, b) => {
      let valueA = a[sortField] ?? 0
      let valueB = b[sortField] ?? 0

      if (typeof valueA === 'string') valueA = valueA.toLowerCase()
      if (typeof valueB === 'string') valueB = valueB.toLowerCase()

      if (valueA < valueB) return sortDirection === 'asc' ? -1 : 1
      if (valueA > valueB) return sortDirection === 'asc' ? 1 : -1
      return 0
    })

    return result
  }, [filterExit, filterSetup, filterSide, sortDirection, sortField, trades])

  const exportCsv = () => {
    const headers = ['asset', 'timeframe', 'setup_id', 'side', 'entry_ts', 'entry_price', 'stop_price', 'tp1_price', 'exit_ts', 'exit_price', 'exit_reason', 'pnl_net', 'r_multiple', 'fees', 'slippage', 'setup_score']
    const rows = filteredTrades.map((trade) => headers.map((header) => trade[header] ?? ''))
    const csv = [headers.join(','), ...rows.map((row) => row.join(','))].join('\n')
    downloadBlob(new Blob([csv], { type: 'text/csv' }), `trades_${runId.slice(0, 8)}.csv`)
  }

  const exportJson = () => {
    downloadBlob(
      new Blob([JSON.stringify(filteredTrades, null, 2)], { type: 'application/json' }),
      `trades_${runId.slice(0, 8)}.json`,
    )
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3, 4, 5].map((index) => (
          <Card key={index}>
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
      <div className="flex flex-wrap items-center gap-3">
        <Select
          value={filterSetup}
          onChange={(event) => setFilterSetup(event.target.value)}
          options={[
            { value: '', label: 'All Setups' },
            ...setups.map((setupId) => ({ value: setupId, label: setupId })),
          ]}
          className="w-40"
        />
        <Select
          value={filterSide}
          onChange={(event) => setFilterSide(event.target.value)}
          options={[
            { value: '', label: 'All Sides' },
            { value: 'long', label: 'Long' },
            { value: 'short', label: 'Short' },
          ]}
          className="w-36"
        />
        <Select
          value={filterExit}
          onChange={(event) => setFilterExit(event.target.value)}
          options={[
            { value: '', label: 'All Exit Types' },
            ...exits.map((exitReason) => ({ value: exitReason, label: exitReason })),
          ]}
          className="w-44"
        />

        <span className="ml-auto text-sm text-terminal-500">
          {filteredTrades.length} of {trades.length} trades
        </span>

        <button onClick={exportCsv} className="btn btn-ghost btn-sm flex items-center gap-1">
          <Download className="size-3" />
          CSV
        </button>
        <button onClick={exportJson} className="btn btn-ghost btn-sm flex items-center gap-1">
          <Download className="size-3" />
          JSON
        </button>
      </div>

      <Card>
        <CardContent className="overflow-x-auto p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-terminal-700 text-xs text-terminal-500">
                <th className="px-3 py-2 text-left">Asset</th>
                <th className="p-2 text-left">Setup</th>
                <th className="p-2 text-left">Side</th>
                <SortHeader activeField={sortField} direction={sortDirection} field="entry_price" label="Entry" onToggle={toggleSort} />
                <SortHeader activeField={sortField} direction={sortDirection} field="stop_price" label="Stop" onToggle={toggleSort} />
                <SortHeader activeField={sortField} direction={sortDirection} field="exit_price" label="Exit" onToggle={toggleSort} />
                <th className="p-2 text-left">Reason</th>
                <SortHeader activeField={sortField} direction={sortDirection} field="pnl_net" label="PnL" onToggle={toggleSort} />
                <SortHeader activeField={sortField} direction={sortDirection} field="r_multiple" label="R" onToggle={toggleSort} />
                <SortHeader activeField={sortField} direction={sortDirection} field="setup_score" label="Score" onToggle={toggleSort} />
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map((trade) => (
                <tr key={trade.id} className="op-row border-b border-terminal-800 hover:bg-terminal-800/50">
                  <td className="px-3 py-2 font-mono font-bold text-terminal-100">
                    {trade.asset}
                    <span className="ml-1 text-xs text-terminal-500">{trade.timeframe}</span>
                  </td>
                  <td className="p-2">
                    <Badge variant="info" className="text-xs">{trade.setup_id}</Badge>
                  </td>
                  <td className="p-2">
                    <DirectionBadge direction={trade.side} />
                  </td>
                  <td className="p-2 text-right font-mono text-terminal-200">{formatPrice(trade.entry_price)}</td>
                  <td className="p-2 text-right font-mono text-signal-short">{formatPrice(trade.stop_price)}</td>
                  <td className="p-2 text-right font-mono text-terminal-200">{formatPrice(trade.exit_price)}</td>
                  <td className="p-2">
                    <Badge variant={getTradeExitReasonVariant(trade.exit_reason)} className="text-xs">
                      {trade.exit_reason}
                    </Badge>
                  </td>
                  <td className={`p-2 text-right font-mono ${(trade.pnl_net || 0) >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                    {formatPnL(trade.pnl_net)}
                  </td>
                  <td className={`p-2 text-right font-mono ${(trade.r_multiple || 0) >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                    {formatR(trade.r_multiple)}
                  </td>
                  <td className="p-2 text-right font-mono text-amber-400">
                    {trade.setup_score ? formatNumber(trade.setup_score, 1) : '—'}
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
