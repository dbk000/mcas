import { useState, useCallback, useEffect, useMemo } from 'react'
import { Link } from 'react-router-dom'
import {
  ArrowUpDown,
  ChevronUp,
  ChevronDown,
  TrendingUp,
  DollarSign,
  Users,
  Activity,
  BarChart3,
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { useToast } from '../context/ToastContext'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  Skeleton,
  EmptyState,
  RefreshControl,
} from '../components/ui'

// -- Formatting helpers --

function formatFunding(rate) {
  if (rate == null) return '--'
  const bps = rate * 10000
  return `${bps >= 0 ? '+' : ''}${bps.toFixed(2)} bps`
}

function formatOI(value) {
  if (value == null) return '--'
  if (value >= 1e9) return `$${(value / 1e9).toFixed(2)}B`
  if (value >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
  if (value >= 1e3) return `$${(value / 1e3).toFixed(0)}K`
  return `$${value.toFixed(0)}`
}

function formatPct(value) {
  if (value == null) return '--'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function fundingColor(rate) {
  if (rate == null) return 'text-terminal-400'
  if (Math.abs(rate) < 0.00005) return 'text-terminal-400'
  return rate > 0 ? 'text-red-400' : 'text-green-400'
}

function zScoreColor(z) {
  if (z == null) return 'text-terminal-400'
  if (z > 2) return 'text-red-400'
  if (z < -2) return 'text-green-400'
  if (Math.abs(z) > 1) return 'text-amber-400'
  return 'text-terminal-400'
}

function pctColor(value) {
  if (value == null) return 'text-terminal-400'
  return value >= 0 ? 'text-green-400' : 'text-red-400'
}

function minutesAgo(isoString) {
  if (!isoString) return null
  const diff = Date.now() - new Date(isoString).getTime()
  return Math.max(0, Math.round(diff / 60000))
}

// -- Sort comparator --

function getSortValue(row, col) {
  switch (col) {
    case 'symbol': return row.symbol || ''
    case 'funding_rate': return Math.abs(row.funding_rate ?? 0)
    case 'funding_rate_zscore': return Math.abs(row.funding_rate_zscore ?? 0)
    case 'oi_change_pct_24h': return row.oi_change_pct_24h ?? 0
    case 'ls_ratio': return row.ls_ratio ?? 0
    case 'taker_buy_ratio': return row.taker_buy_ratio ?? 0
    case 'oi_value': return row.oi_value ?? 0
    default: return 0
  }
}

// -- Column definitions --

const COLUMNS = [
  { key: 'symbol', label: 'Symbol', sortable: true },
  { key: 'funding_rate', label: 'Funding Rate', sortable: true },
  { key: 'funding_rate_zscore', label: 'FR Z-Score', sortable: true },
  { key: 'oi_value', label: 'Open Interest', sortable: true },
  { key: 'oi_change_pct_24h', label: 'OI Change 24h', sortable: true },
  { key: 'ls_ratio', label: 'L/S Ratio', sortable: true },
  { key: 'taker_buy_ratio', label: 'Taker Buy Ratio', sortable: true },
]

// -- Sub-components --

function SortIcon({ column, sortCol, sortDir }) {
  if (column !== sortCol) {
    return <ArrowUpDown className="ml-1 inline size-3 text-terminal-500" />
  }
  return sortDir === 'asc'
    ? <ChevronUp className="ml-1 inline size-3 text-amber-400" />
    : <ChevronDown className="ml-1 inline size-3 text-amber-400" />
}

function RatioBar({ value, max = 2, color = 'bg-amber-400' }) {
  if (value == null) return <span className="text-terminal-500">--</span>
  const pct = Math.min(100, Math.max(0, (value / max) * 100))
  return (
    <div className="flex items-center gap-2">
      <div className="h-2 w-20 overflow-hidden rounded-full bg-terminal-700">
        <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="font-mono text-xs text-terminal-200">{value.toFixed(2)}</span>
    </div>
  )
}

function SummaryCard({ label, value, icon: Icon, valueColor }) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between">
        <div>
          <p className="mb-1 text-sm text-terminal-400">{label}</p>
          <p className={`font-mono text-2xl font-bold ${valueColor || 'text-terminal-100'}`}>
            {value}
          </p>
        </div>
        {Icon && (
          <div className="flex size-10 items-center justify-center rounded-lg bg-terminal-700">
            <Icon className="size-5 text-terminal-400" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// -- Main component --

export default function DerivativesDashboard() {
  const { get, isConnected } = useApi()
  const { addToast } = useToast()
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [lastFetched, setLastFetched] = useState(null)
  const [sortCol, setSortCol] = useState('funding_rate')
  const [sortDir, setSortDir] = useState('desc')

  const loadData = useCallback(async () => {
    if (!isConnected) return
    try {
      setLoading(true)
      const res = await get('/v1/derivatives/dashboard')
      setData(res?.data)
      setLastFetched(new Date())
    } catch (err) {
      addToast(err.message || 'Failed to load derivatives data', 'error')
    } finally {
      setLoading(false)
    }
  }, [get, isConnected, addToast])

  useEffect(() => { loadData() }, [loadData])

  const symbols = data?.symbols || []

  // Compute summary metrics
  const summary = useMemo(() => {
    if (symbols.length === 0) return null
    const withFunding = symbols.filter((s) => s.funding_rate != null)
    const avgFunding = withFunding.length > 0
      ? withFunding.reduce((sum, s) => sum + s.funding_rate, 0) / withFunding.length
      : 0
    const totalOI = symbols.reduce((sum, s) => sum + (s.oi_value || 0), 0)
    const withLS = symbols.filter((s) => s.ls_ratio != null)
    const avgLS = withLS.length > 0
      ? withLS.reduce((sum, s) => sum + s.ls_ratio, 0) / withLS.length
      : 0
    const withTaker = symbols.filter((s) => s.taker_imbalance != null)
    const netTakerFlow = withTaker.length > 0
      ? withTaker.reduce((sum, s) => sum + s.taker_imbalance, 0) / withTaker.length
      : 0
    return { avgFunding, totalOI, avgLS, netTakerFlow }
  }, [symbols])

  // Sort symbols
  const sortedSymbols = useMemo(() => {
    if (symbols.length === 0) return []
    return [...symbols].sort((a, b) => {
      const va = getSortValue(a, sortCol)
      const vb = getSortValue(b, sortCol)
      if (sortCol === 'symbol') {
        return sortDir === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va)
      }
      return sortDir === 'asc' ? va - vb : vb - va
    })
  }, [symbols, sortCol, sortDir])

  const handleSort = (col) => {
    if (sortCol === col) {
      setSortDir((prev) => (prev === 'asc' ? 'desc' : 'asc'))
    } else {
      setSortCol(col)
      setSortDir('desc')
    }
  }

  const dataAgeMinutes = minutesAgo(data?.data_as_of_ts)

  // -- Render --

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Derivatives Dashboard</h1>
          <p className="mt-1 text-sm text-terminal-400">
            Funding rates, open interest, and positioning data
          </p>
        </div>
        <div className="flex items-center gap-3">
          {dataAgeMinutes != null && (
            <Badge variant={dataAgeMinutes > 10 ? 'warning' : 'default'}>
              Last updated: {dataAgeMinutes}m ago
            </Badge>
          )}
          {data && (
            <Badge variant="info">
              Data for {data.total_with_data || symbols.length} symbols
            </Badge>
          )}
          <RefreshControl
            lastUpdated={lastFetched}
            onRefresh={loadData}
            loading={loading}
            serverAsOf={data?.data_as_of_ts}
          />
        </div>
      </div>

      {/* Summary cards */}
      {loading && !data ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {Array(4).fill(0).map((_, i) => (
            <Card key={i}>
              <CardContent>
                <Skeleton className="mb-2 h-4 w-24" />
                <Skeleton className="h-8 w-32" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : summary ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <SummaryCard
            label="Avg Funding Rate"
            value={formatFunding(summary.avgFunding)}
            icon={TrendingUp}
            valueColor={fundingColor(summary.avgFunding)}
          />
          <SummaryCard
            label="Total Open Interest"
            value={formatOI(summary.totalOI)}
            icon={DollarSign}
          />
          <SummaryCard
            label="Avg L/S Ratio"
            value={summary.avgLS.toFixed(3)}
            icon={Users}
          />
          <SummaryCard
            label="Net Taker Flow"
            value={summary.netTakerFlow >= 0
              ? `+${summary.netTakerFlow.toFixed(3)}`
              : summary.netTakerFlow.toFixed(3)}
            icon={Activity}
            valueColor={summary.netTakerFlow >= 0 ? 'text-green-400' : 'text-red-400'}
          />
        </div>
      ) : null}

      {/* Main table */}
      <Card>
        <CardHeader>
          <CardTitle>Derivatives by Symbol</CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto p-0">
          {loading && !data ? (
            <div className="space-y-3 p-4">
              {Array(8).fill(0).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : sortedSymbols.length === 0 ? (
            <div className="p-4">
              <EmptyState
                icon={BarChart3}
                title="No derivatives data"
                description="Derivatives data is not yet available. Check back later."
              />
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-terminal-700 text-left">
                  {COLUMNS.map((col) => (
                    <th
                      key={col.key}
                      className={`whitespace-nowrap px-4 py-3 text-xs font-medium uppercase tracking-wider text-terminal-400 ${
                        col.sortable ? 'cursor-pointer select-none hover:text-terminal-200' : ''
                      }`}
                      onClick={col.sortable ? () => handleSort(col.key) : undefined}
                    >
                      {col.label}
                      {col.sortable && (
                        <SortIcon column={col.key} sortCol={sortCol} sortDir={sortDir} />
                      )}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-terminal-700/50">
                {sortedSymbols.map((row) => (
                  <tr
                    key={row.symbol}
                    className="transition-colors hover:bg-terminal-700/30"
                  >
                    {/* Symbol */}
                    <td className="whitespace-nowrap px-4 py-3">
                      <Link
                        to={`/app/markets/${row.symbol}`}
                        className="font-mono font-medium text-amber-400 hover:text-amber-300"
                      >
                        {row.symbol}
                      </Link>
                    </td>

                    {/* Funding Rate */}
                    <td className={`whitespace-nowrap px-4 py-3 font-mono ${fundingColor(row.funding_rate)}`}>
                      {formatFunding(row.funding_rate)}
                    </td>

                    {/* Funding Rate Z-Score */}
                    <td className={`whitespace-nowrap px-4 py-3 font-mono ${zScoreColor(row.funding_rate_zscore)}`}>
                      {row.funding_rate_zscore != null
                        ? row.funding_rate_zscore.toFixed(2)
                        : '--'}
                    </td>

                    {/* Open Interest */}
                    <td className="whitespace-nowrap px-4 py-3 font-mono text-terminal-200">
                      {formatOI(row.oi_value)}
                    </td>

                    {/* OI Change 24h */}
                    <td className={`whitespace-nowrap px-4 py-3 font-mono ${pctColor(row.oi_change_pct_24h)}`}>
                      {formatPct(row.oi_change_pct_24h)}
                    </td>

                    {/* L/S Ratio */}
                    <td className="px-4 py-3">
                      <RatioBar
                        value={row.ls_ratio}
                        max={2}
                        color={row.ls_ratio > 1 ? 'bg-green-500' : 'bg-red-500'}
                      />
                    </td>

                    {/* Taker Buy Ratio */}
                    <td className="px-4 py-3">
                      <RatioBar
                        value={row.taker_buy_ratio}
                        max={1}
                        color={row.taker_buy_ratio > 0.5 ? 'bg-green-500' : 'bg-red-500'}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
