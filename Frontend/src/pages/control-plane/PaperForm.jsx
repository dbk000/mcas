import { useState } from 'react'
import { Loader2, Play, Square } from 'lucide-react'
import { Input, Toggle } from '../../components/ui'
import { formatDateTime } from '../../utils/formatters'
import FormField from './FormField'

export default function PaperForm({ onSubmit, onStop, loading, paperStatus, strategies }) {
  const [name, setName] = useState('')
  const [setupIds, setSetupIds] = useState(['S9', 'U1', 'S10'])
  const [initialCapital, setInitialCapital] = useState(10000)
  const [riskPerTrade, setRiskPerTrade] = useState(1.0)
  const [maxPositions, setMaxPositions] = useState(3)
  const [autoTrade, setAutoTrade] = useState(true)
  const [autoTradeTopN, setAutoTradeTopN] = useState(3)
  const [minScore, setMinScore] = useState(0)

  const isActive = paperStatus?.active

  const handleSubmit = (event) => {
    event.preventDefault()
    onSubmit({
      name: name || undefined,
      setup_ids: setupIds,
      initial_capital: initialCapital,
      risk_per_trade_pct: riskPerTrade,
      max_positions: maxPositions,
      auto_trade: autoTrade,
      auto_trade_top_n: autoTradeTopN,
      min_score: minScore,
    })
  }

  if (isActive) {
    return (
      <div className="space-y-4">
        <div className="rounded-lg border border-green-500/30 bg-green-500/10 p-4">
          <div className="mb-2 flex items-center gap-2 text-green-400">
            <div className="size-2 animate-pulse rounded-full bg-green-400" />
            Paper Trading Active
          </div>
          <div className="text-sm text-terminal-300">
            <div>Run: <span className="font-mono text-terminal-100">{paperStatus.run_name}</span></div>
            <div>Started: {formatDateTime(paperStatus.started_at)}</div>
          </div>
        </div>

        <button
          onClick={() => onStop()}
          disabled={loading}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-red-500 px-4 py-2.5 font-medium text-white transition-colors hover:bg-red-400 disabled:bg-terminal-700"
        >
          {loading ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Stopping...
            </>
          ) : (
            <>
              <Square className="size-4" />
              Stop Paper Trading
            </>
          )}
        </button>
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormField label="Run Name (optional)">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="paper_session_001"
        />
      </FormField>

      <FormField label="Setups">
        <div className="flex flex-wrap gap-3">
          {(strategies || []).map((strategy) => (
            <label key={strategy.id} className="flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={setupIds.includes(strategy.id)}
                onChange={(event) => {
                  if (event.target.checked) setSetupIds([...setupIds, strategy.id])
                  else setSetupIds(setupIds.filter((item) => item !== strategy.id))
                }}
                className="rounded border-terminal-600 bg-terminal-800 text-amber-500 focus:ring-amber-500"
              />
              <span className="text-sm">{strategy.label}</span>
            </label>
          ))}
        </div>
      </FormField>

      <div className="grid grid-cols-3 gap-4">
        <FormField label="Initial Capital">
          <Input
            type="number"
            min={100}
            value={initialCapital}
            onChange={(event) => setInitialCapital(parseFloat(event.target.value) || 10000)}
          />
        </FormField>
        <FormField label="Risk % / Trade">
          <Input
            type="number"
            min={0.1}
            max={5}
            step={0.1}
            value={riskPerTrade}
            onChange={(event) => setRiskPerTrade(parseFloat(event.target.value) || 1.0)}
          />
        </FormField>
        <FormField label="Max Positions">
          <Input
            type="number"
            min={1}
            max={10}
            value={maxPositions}
            onChange={(event) => setMaxPositions(parseInt(event.target.value, 10) || 3)}
          />
        </FormField>
      </div>

      <div className="space-y-3 rounded-lg bg-terminal-800/50 p-3">
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium">Auto-Trade from Signals</span>
          <Toggle enabled={autoTrade} onChange={setAutoTrade} />
        </div>

        {autoTrade && (
          <div className="grid grid-cols-2 gap-4 border-t border-terminal-700 pt-2">
            <FormField label="Take Top N Signals">
              <Input
                type="number"
                min={1}
                max={10}
                value={autoTradeTopN}
                onChange={(event) => setAutoTradeTopN(parseInt(event.target.value, 10) || 3)}
              />
            </FormField>
            <FormField label="Minimum Score">
              <Input
                type="number"
                min={0}
                max={100}
                step={5}
                value={minScore}
                onChange={(event) => setMinScore(parseFloat(event.target.value) || 0)}
              />
            </FormField>
          </div>
        )}
      </div>

      <button
        type="submit"
        disabled={loading || setupIds.length === 0}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-green-500 px-4 py-2.5 font-medium text-white transition-colors hover:bg-green-400 disabled:bg-terminal-700"
      >
        {loading ? (
          <>
            <Loader2 className="size-4 animate-spin" />
            Starting...
          </>
        ) : (
          <>
            <Play className="size-4" />
            Start Paper Trading
          </>
        )}
      </button>
    </form>
  )
}
