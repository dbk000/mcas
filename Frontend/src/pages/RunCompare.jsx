import { useState, useMemo } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { ArrowLeft, GitCompare, Search } from 'lucide-react'
import { useRuns, useRun } from '../hooks/useData'
import {
  Card, CardHeader, CardTitle, CardContent,
  Skeleton, EmptyState, Input
} from '../components/ui'
import { getPaginationLimit } from '../config/governed'
import {
  formatPnL, formatPercent, formatNumber, formatR,
  formatDollar, formatDateTime
} from '../utils/formatters'

function CompareStatRow({ label, valueA, valueB, format, higherIsBetter = true }) {
  const fmtA = format ? format(valueA) : String(valueA ?? '\u2014')
  const fmtB = format ? format(valueB) : String(valueB ?? '\u2014')

  let colorA = 'text-terminal-100'
  let colorB = 'text-terminal-100'

  if (valueA != null && valueB != null && valueA !== valueB) {
    const aWins = higherIsBetter ? valueA > valueB : valueA < valueB
    colorA = aWins ? 'text-signal-long' : 'text-signal-short'
    colorB = aWins ? 'text-signal-short' : 'text-signal-long'
  }

  return (
    <tr className="border-b border-terminal-800">
      <td className="px-3 py-2 text-sm text-terminal-400">{label}</td>
      <td className={`px-3 py-2 text-right font-mono text-sm ${colorA}`}>{fmtA}</td>
      <td className={`px-3 py-2 text-right font-mono text-sm ${colorB}`}>{fmtB}</td>
    </tr>
  )
}

function RunSelector({ label, selectedId, onSelect, runs }) {
  const [query, setQuery] = useState('')

  const backtestRuns = useMemo(() =>
    (runs || []).filter(r =>
      r.run_type === 'BACKTEST' && r.status === 'SUCCESS'
    ),
    [runs]
  )

  const filtered = query
    ? backtestRuns.filter(r =>
        r.name?.toLowerCase().includes(query.toLowerCase()) ||
        r.fingerprint?.includes(query)
      )
    : backtestRuns

  return (
    <div className="space-y-2">
      <label className="block text-sm font-medium text-terminal-300">{label}</label>
      <div className="relative">
        <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-terminal-400" />
        <Input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by name or fingerprint..."
          className="w-full pl-9"
        />
      </div>
      <div className="max-h-48 overflow-y-auto rounded-lg border border-terminal-600">
        {filtered.length === 0 ? (
          <div className="p-3 text-sm text-terminal-400">No completed backtests found.</div>
        ) : (
          filtered.slice(0, 20).map(r => (
            <button
              key={r.id}
              onClick={() => onSelect(r.id)}
              className={`w-full border-b border-terminal-800 px-3 py-2 text-left text-sm last:border-b-0 hover:bg-terminal-700 ${
                selectedId === r.id ? 'bg-amber-500/10 text-amber-400' : 'text-terminal-200'
              }`}
            >
              <div className="truncate font-medium">{r.name}</div>
              <div className="font-mono text-xs text-terminal-500">
                {r.fingerprint?.slice(0, 8)} &middot; {formatDateTime(r.created_at)}
              </div>
            </button>
          ))
        )}
      </div>
    </div>
  )
}

function ComparisonTable({ runA, runB }) {
  const sA = runA?.summary || {}
  const sB = runB?.summary || {}

  const metrics = [
    { label: 'Net PnL', key: 'net_pnl', format: formatPnL, higher: true },
    { label: 'Return %', key: 'return_pct', format: v => formatPercent(v), higher: true },
    { label: 'Win Rate', key: 'win_rate', format: v => formatPercent(v * 100), higher: true },
    { label: 'Profit Factor', key: 'profit_factor', format: v => formatNumber(v, 2), higher: true },
    { label: 'Total Trades', key: 'total_trades', format: v => String(v), higher: null },
    { label: 'Avg R', key: 'avg_r', format: formatR, higher: true },
    { label: 'Total R', key: 'total_r', format: formatR, higher: true },
    { label: 'Sharpe', key: 'sharpe_ratio', format: v => formatNumber(v, 3), higher: true },
    { label: 'Sortino', key: 'sortino_ratio', format: v => formatNumber(v, 3), higher: true },
    { label: 'Calmar', key: 'calmar_ratio', format: v => formatNumber(v, 3), higher: true },
    { label: 'Max Drawdown %', key: 'max_drawdown_pct', format: v => formatPercent(v), higher: false },
    { label: 'Payoff Ratio', key: 'payoff_ratio', format: v => formatNumber(v, 2), higher: true },
    { label: 'Avg Win R', key: 'avg_win_r', format: formatR, higher: true },
    { label: 'Avg Loss R', key: 'avg_loss_r', format: formatR, higher: false },
    { label: 'Max Con. Wins', key: 'max_consecutive_wins', format: v => String(v), higher: true },
    { label: 'Max Con. Losses', key: 'max_consecutive_losses', format: v => String(v), higher: false },
    { label: 'Avg Holding (bars)', key: 'avg_holding_bars', format: v => formatNumber(v, 1), higher: null },
    { label: 'Starting Capital', key: 'starting_capital', format: formatDollar, higher: null },
    { label: 'Final Equity', key: 'final_equity', format: formatDollar, higher: true },
    { label: 'Total Fees', key: 'total_fees', format: formatDollar, higher: false },
    { label: 'Total Slippage', key: 'total_slippage', format: formatDollar, higher: false },
  ]

  return (
    <Card>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-terminal-700 text-terminal-500">
              <th className="px-3 py-2 text-left">Metric</th>
              <th className="px-3 py-2 text-right">
                <span className="text-amber-400">{runA.name}</span>
              </th>
              <th className="px-3 py-2 text-right">
                <span className="text-blue-400">{runB.name}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {metrics.map(m => (
              <CompareStatRow
                key={m.key}
                label={m.label}
                valueA={sA[m.key]}
                valueB={sB[m.key]}
                format={m.format}
                higherIsBetter={m.higher === null ? undefined : m.higher}
              />
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}

function PerSetupComparison({ runA, runB }) {
  const setupsA = runA?.summary?.per_setup || {}
  const setupsB = runB?.summary?.per_setup || {}
  const allSetups = [...new Set([...Object.keys(setupsA), ...Object.keys(setupsB)])]

  if (allSetups.length === 0) return null

  return (
    <Card>
      <CardHeader>
        <CardTitle>Per-Setup Comparison</CardTitle>
      </CardHeader>
      <CardContent className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-terminal-700 text-terminal-500">
              <th className="px-3 py-2 text-left">Setup</th>
              <th className="px-3 py-2 text-right" colSpan={3}>
                <span className="text-amber-400">{runA.name}</span>
              </th>
              <th className="px-3 py-2 text-right" colSpan={3}>
                <span className="text-blue-400">{runB.name}</span>
              </th>
            </tr>
            <tr className="border-b border-terminal-700 text-xs text-terminal-500">
              <th></th>
              <th className="px-2 py-1 text-right">Trades</th>
              <th className="px-2 py-1 text-right">Win%</th>
              <th className="px-2 py-1 text-right">PnL</th>
              <th className="px-2 py-1 text-right">Trades</th>
              <th className="px-2 py-1 text-right">Win%</th>
              <th className="px-2 py-1 text-right">PnL</th>
            </tr>
          </thead>
          <tbody>
            {allSetups.map(sid => {
              const a = setupsA[sid] || {}
              const b = setupsB[sid] || {}
              return (
                <tr key={sid} className="border-b border-terminal-800">
                  <td className="px-3 py-2 font-mono font-bold text-terminal-100">{sid}</td>
                  <td className="p-2 text-right">{a.trades ?? '\u2014'}</td>
                  <td className="p-2 text-right">{a.win_rate != null ? formatPercent(a.win_rate * 100) : '\u2014'}</td>
                  <td className={`p-2 text-right font-mono ${(a.pnl_net || 0) >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                    {a.pnl_net != null ? formatPnL(a.pnl_net) : '\u2014'}
                  </td>
                  <td className="p-2 text-right">{b.trades ?? '\u2014'}</td>
                  <td className="p-2 text-right">{b.win_rate != null ? formatPercent(b.win_rate * 100) : '\u2014'}</td>
                  <td className={`p-2 text-right font-mono ${(b.pnl_net || 0) >= 0 ? 'text-signal-long' : 'text-signal-short'}`}>
                    {b.pnl_net != null ? formatPnL(b.pnl_net) : '\u2014'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}

export default function RunCompare() {
  const [searchParams, setSearchParams] = useSearchParams()
  const idA = searchParams.get('a') || ''
  const idB = searchParams.get('b') || ''

  const { data: runsData, loading: runsLoading } = useRuns({ run_type: 'BACKTEST', status: 'SUCCESS', limit: getPaginationLimit('runs_max') })
  const { data: runA, loading: loadingA } = useRun(idA)
  const { data: runB, loading: loadingB } = useRun(idB)

  const setIdA = (id) => setSearchParams(prev => { prev.set('a', id); return prev })
  const setIdB = (id) => setSearchParams(prev => { prev.set('b', id); return prev })

  const bothLoaded = runA && runB && !loadingA && !loadingB

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/app/runs" className="btn btn-ghost btn-sm">
          <ArrowLeft className="size-4" />
        </Link>
        <div>
          <h1 className="flex items-center gap-2 text-2xl font-bold text-terminal-100">
            <GitCompare className="size-6 text-amber-500" />
            Compare Backtests
          </h1>
          <p className="text-sm text-terminal-400">Select two completed backtests to compare side-by-side</p>
        </div>
      </div>

      {/* Selectors */}
      <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-amber-400">Run A</CardTitle>
          </CardHeader>
          <CardContent>
            {runsLoading ? (
              <Skeleton />
            ) : (
              <RunSelector
                label="Select first backtest"
                selectedId={idA}
                onSelect={setIdA}
                runs={runsData?.runs}
              />
            )}
            {loadingA && idA && <div className="mt-2"><Skeleton /></div>}
            {runA && (
              <div className="mt-3 rounded bg-terminal-900 p-2 text-sm">
                <span className="font-medium text-amber-400">{runA.name}</span>
                <span className="ml-2 text-terminal-500">&middot; {runA.summary?.total_trades || 0} trades</span>
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-blue-400">Run B</CardTitle>
          </CardHeader>
          <CardContent>
            {runsLoading ? (
              <Skeleton />
            ) : (
              <RunSelector
                label="Select second backtest"
                selectedId={idB}
                onSelect={setIdB}
                runs={runsData?.runs}
              />
            )}
            {loadingB && idB && <div className="mt-2"><Skeleton /></div>}
            {runB && (
              <div className="mt-3 rounded bg-terminal-900 p-2 text-sm">
                <span className="font-medium text-blue-400">{runB.name}</span>
                <span className="ml-2 text-terminal-500">&middot; {runB.summary?.total_trades || 0} trades</span>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Comparison */}
      {bothLoaded ? (
        <div className="space-y-6">
          <ComparisonTable runA={runA} runB={runB} />
          <PerSetupComparison runA={runA} runB={runB} />
        </div>
      ) : (idA && idB) ? (
        <Card>
          <CardContent>
            <div className="py-8 text-center">
              <Skeleton variant="card" />
            </div>
          </CardContent>
        </Card>
      ) : (
        <EmptyState
          icon={GitCompare}
          title="Select two backtests"
          description="Choose a backtest for Run A and Run B above to see a side-by-side comparison"
        />
      )}
    </div>
  )
}
