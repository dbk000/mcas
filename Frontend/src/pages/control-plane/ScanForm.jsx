import { useState } from 'react'
import { Loader2, Zap } from 'lucide-react'
import { FeedbackOnset } from '../../components/FeedbackOnset'
import { Input } from '../../components/ui'
import FormField from './FormField'

export default function ScanForm({ onSubmit, loading, strategies }) {
  const [name, setName] = useState('')
  const [setupIds, setSetupIds] = useState(['S9', 'U1', 'S10'])
  const [topN, setTopN] = useState(10)
  const [priority, setPriority] = useState(0)

  const handleSubmit = (event) => {
    event.preventDefault()
    onSubmit({
      name: name || undefined,
      setup_ids: setupIds,
      top_n: topN,
      priority,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormField label="Run Name (optional)" hint="Leave blank for auto-generated name">
        <Input
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="scan_manual_20240115"
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

      <div className="grid grid-cols-2 gap-4">
        <FormField label="Top N Signals" hint="Max signals to return">
          <Input
            type="number"
            min={1}
            max={100}
            value={topN}
            onChange={(event) => setTopN(parseInt(event.target.value, 10) || 10)}
          />
        </FormField>

        <FormField label="Priority" hint="0=lowest, 10=highest">
          <Input
            type="number"
            min={0}
            max={10}
            value={priority}
            onChange={(event) => setPriority(parseInt(event.target.value, 10) || 0)}
          />
        </FormField>
      </div>

      <FeedbackOnset latencyLabel="RunStart">
        <button
          type="submit"
          disabled={loading || setupIds.length === 0}
          className="flex w-full items-center justify-center gap-2 rounded-lg bg-amber-500 px-4 py-2.5 font-medium text-terminal-900 transition-colors hover:bg-amber-400 disabled:bg-terminal-700"
        >
          {loading ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              Creating...
            </>
          ) : (
            <>
              <Zap className="size-4" />
              Run Scan
            </>
          )}
        </button>
      </FeedbackOnset>
    </form>
  )
}
