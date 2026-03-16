import { useState } from 'react'
import { Database, Loader2 } from 'lucide-react'
import { Input } from '../../components/ui'
import FormField from './FormField'
import SymbolMultiSelect from './SymbolMultiSelect'
import DataAvailabilityInfo from './DataAvailabilityInfo'

export default function BacktestForm({ onSubmit, loading, markets, strategies }) {
  const [name, setName] = useState('')
  const [symbols, setSymbols] = useState(['BTCUSDT', 'ETHUSDT'])
  const [timeframeList, setTimeframeList] = useState('1h,4h')
  const [startDate, setStartDate] = useState('2024-01-01')
  const [endDate, setEndDate] = useState(() => {
    const date = new Date()
    date.setDate(date.getDate() - 1)
    return date.toISOString().slice(0, 10)
  })
  const [setupIds, setSetupIds] = useState(['S9', 'U1', 'S10'])
  const [slippageBps, setSlippageBps] = useState(5)
  const [feeBps, setFeeBps] = useState(10)

  const parsedTimeframes = timeframeList.split(',').map((item) => item.trim()).filter(Boolean)

  const handleSubmit = (event) => {
    event.preventDefault()
    onSubmit({
      name: name || undefined,
      symbols,
      timeframes: parsedTimeframes,
      start_date: startDate,
      end_date: endDate,
      setup_ids: setupIds,
      slippage_bps: slippageBps,
      fee_bps: feeBps,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormField label="Run Name (optional)">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="backtest_btc_2024"
        />
      </FormField>

      <FormField label="Symbols" hint="Search and select from available markets">
        <SymbolMultiSelect
          selected={symbols}
          onChange={setSymbols}
          markets={markets}
        />
      </FormField>

      <FormField label="Timeframes" hint="Comma-separated list">
        <Input
          value={timeframeList}
          onChange={(event) => setTimeframeList(event.target.value)}
          placeholder="1h,4h"
        />
      </FormField>

      <div className="grid grid-cols-2 gap-4">
        <FormField label="Start Date">
          <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
        </FormField>
        <FormField label="End Date">
          <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        </FormField>
      </div>

      <DataAvailabilityInfo
        symbols={symbols}
        timeframes={parsedTimeframes}
        startDate={startDate}
        endDate={endDate}
      />

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

      <div className="grid grid-cols-2 gap-4">
        <FormField label="Slippage (bps)">
          <Input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={slippageBps}
            onChange={(event) => setSlippageBps(parseFloat(event.target.value) || 5)}
          />
        </FormField>
        <FormField label="Fees (bps)">
          <Input
            type="number"
            min={0}
            max={100}
            step={0.5}
            value={feeBps}
            onChange={(event) => setFeeBps(parseFloat(event.target.value) || 10)}
          />
        </FormField>
      </div>

      <button
        type="submit"
        disabled={loading || setupIds.length === 0 || symbols.length === 0}
        className="flex w-full items-center justify-center gap-2 rounded-lg bg-amber-500 px-4 py-2.5 font-medium text-terminal-900 transition-colors hover:bg-amber-400 disabled:bg-terminal-700"
      >
        {loading ? (
          <>
            <Loader2 className="size-4 animate-spin" />
            Creating...
          </>
        ) : (
          <>
            <Database className="size-4" />
            Run Backtest
          </>
        )}
      </button>
    </form>
  )
}
