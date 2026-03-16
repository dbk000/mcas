import { Filter, Search } from 'lucide-react'
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Select,
  Skeleton,
  Spinner,
} from '../../components/ui'
import { formatRelativeTime } from '../../utils/formatters'
import { MIN_TRADE_OPTIONS } from './useCorrelationAnalysis'

function RunOption({ isSelected, onToggle, run }) {
  return (
    <label
      className={[
        'flex cursor-pointer items-center gap-3 rounded-lg p-3 transition-colors',
        isSelected
          ? 'border border-amber-600/50 bg-amber-600/20'
          : 'border border-transparent bg-terminal-900 hover:bg-terminal-700',
      ].join(' ')}
    >
      <input
        type="checkbox"
        checked={isSelected}
        onChange={() => onToggle(run.id)}
        className="size-4 rounded border-terminal-500 bg-terminal-800 text-amber-500 focus:ring-amber-500"
      />
      <Badge
        variant={run.run_type === 'BACKTEST' ? 'warning' : 'info'}
        className="text-xs"
      >
        {run.run_type === 'BACKTEST' ? 'BT' : 'SIM'}
      </Badge>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-terminal-100">{run.name}</p>
        <p className="text-xs text-terminal-400">
          {run.summary?.total_trades || run.summary?.simulated_count || 0} trades | {formatRelativeTime(run.created_at)}
        </p>
      </div>
      {run.summary && (
        <div className="text-right text-xs">
          <p className={run.summary.win_rate >= 0.5 ? 'text-signal-long' : 'text-signal-short'}>
            {(run.summary.win_rate * 100).toFixed(1)}% WR
          </p>
        </div>
      )}
    </label>
  )
}

export default function RunSelectionPanel({
  availableRuns,
  handleAnalyze,
  isConnected,
  loading,
  minTrades,
  polling,
  runsLoading,
  selectedRunCount,
  selectedRunIds,
  setMinTrades,
  setSetupFilter,
  setupFilter,
  strategyOptions,
  toggleRun,
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Filter className="size-4" />
          Select Backtest Runs
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {runsLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : availableRuns.length === 0 ? (
          <p className="text-sm text-terminal-400">No backtest runs available. Run a backtest first.</p>
        ) : (
          <div className="max-h-64 space-y-2 overflow-y-auto">
            {availableRuns.map((run) => (
              <RunOption
                key={run.id}
                isSelected={selectedRunIds.has(run.id)}
                onToggle={toggleRun}
                run={run}
              />
            ))}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-4 border-t border-terminal-600 pt-4">
          <div className="flex items-center gap-2">
            <span className="text-sm text-terminal-400">Setup:</span>
            <Select
              value={setupFilter}
              onChange={(event) => setSetupFilter(event.target.value)}
              options={strategyOptions}
              className="w-40"
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-terminal-400">Min Trades:</span>
            <Select
              value={minTrades}
              onChange={(event) => setMinTrades(Number.parseInt(event.target.value, 10))}
              options={MIN_TRADE_OPTIONS}
              className="w-24"
            />
          </div>
        </div>

        <div className="flex items-center justify-between pt-4">
          <p className="text-sm text-terminal-400">
            {selectedRunCount} run{selectedRunCount !== 1 ? 's' : ''} selected
          </p>
          <button
            onClick={handleAnalyze}
            disabled={!isConnected || loading || polling || selectedRunCount === 0}
            className="btn btn-primary"
          >
            {loading || polling ? (
              <>
                <Spinner size="sm" className="mr-2" />
                {polling ? 'Analyzing...' : 'Starting...'}
              </>
            ) : (
              <>
                <Search className="mr-2 size-4" />
                Analyze Correlations
              </>
            )}
          </button>
        </div>
      </CardContent>
    </Card>
  )
}
