import {
  ChevronDown,
  ChevronRight,
  PlayCircle,
  Plus,
  X,
} from 'lucide-react'
import { FeedbackOnset } from '../../components/FeedbackOnset'
import {
  Badge,
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Input,
  Modal,
  Select,
} from '../../components/ui'
import MultiSelect from '../../components/MultiSelect'

const timeframeOptions = [
  { value: '1m', label: '1m' },
  { value: '5m', label: '5m' },
  { value: '15m', label: '15m' },
  { value: '1h', label: '1h' },
  { value: '4h', label: '4h' },
  { value: '1d', label: '1d' },
]

const directionOptions = [
  { value: 'long', label: 'Long' },
  { value: 'short', label: 'Short' },
]

export default function SimulationModal({
  isOpen,
  onClose,
  onSimulate,
  simulating,
  simConfig,
  setSimConfig,
  showDateFilters,
  setShowDateFilters,
  excludeDateInput,
  setExcludeDateInput,
  includeDateInput,
  setIncludeDateInput,
  setupOptions,
}) {
  const addDate = (field, value, clearValue) => {
    if (value && !simConfig[field].includes(value)) {
      setSimConfig({
        ...simConfig,
        [field]: [...simConfig[field], value].sort(),
      })
    }
    clearValue('')
  }

  const removeDate = (field, value) => {
    setSimConfig({
      ...simConfig,
      [field]: simConfig[field].filter((item) => item !== value),
    })
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Simulate Expired Opportunities" size="md">
      <Card className="border-0 bg-transparent shadow-none">
        <CardHeader className="px-0 pt-0">
          <CardTitle className="sr-only">Simulate Expired Opportunities</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 px-0 pb-0">
          <p className="text-sm text-terminal-400">
            Walk through OHLCV data for each expired opportunity to see what would have happened.
          </p>

          <div className="space-y-3">
            <div>
              <label className="mb-1 block text-sm text-terminal-400">Position Size (USD)</label>
              <Input
                type="number"
                value={simConfig.position_size_usd}
                onChange={(event) => setSimConfig({
                  ...simConfig,
                  position_size_usd: parseFloat(event.target.value) || 1000,
                })}
                min={100}
                max={100000}
                step={100}
              />
            </div>

            <div>
              <label className="mb-1 block text-sm text-terminal-400">Max Opportunities to Simulate</label>
              <Select
                value={simConfig.limit}
                onChange={(event) => setSimConfig({
                  ...simConfig,
                  limit: parseInt(event.target.value, 10),
                })}
                options={[
                  { value: 100, label: '100' },
                  { value: 250, label: '250' },
                  { value: 500, label: '500' },
                  { value: 1000, label: '1000' },
                  { value: 2000, label: '2000' },
                  { value: 10000, label: 'All' },
                ]}
              />
            </div>

            <div>
              <label className="mb-1 block text-sm text-terminal-400">Min Score (0 = all)</label>
              <Input
                type="number"
                value={simConfig.min_score}
                onChange={(event) => setSimConfig({
                  ...simConfig,
                  min_score: parseFloat(event.target.value) || 0,
                })}
                min={0}
                max={1}
                step={0.05}
              />
            </div>

            <div>
              <label className="mb-1 block text-sm text-terminal-400">Setup</label>
              <MultiSelect
                value={simConfig.setup_ids}
                onChange={(values) => setSimConfig({ ...simConfig, setup_ids: values })}
                options={setupOptions}
                placeholder="All"
                ariaLabel="Filter setups"
              />
            </div>

            <div>
              <label className="mb-1 block text-sm text-terminal-400">Timeframe</label>
              <MultiSelect
                value={simConfig.timeframes}
                onChange={(values) => setSimConfig({ ...simConfig, timeframes: values })}
                options={timeframeOptions}
                placeholder="All"
                ariaLabel="Filter timeframes"
              />
            </div>

            <div>
              <label className="mb-1 block text-sm text-terminal-400">Direction</label>
              <MultiSelect
                value={simConfig.directions}
                onChange={(values) => setSimConfig({ ...simConfig, directions: values })}
                options={directionOptions}
                placeholder="All"
                ariaLabel="Filter directions"
              />
            </div>

            <div className="border-t border-terminal-600 pt-3">
              <button
                type="button"
                onClick={() => setShowDateFilters(!showDateFilters)}
                className="flex items-center gap-1 text-sm text-terminal-400 transition-colors hover:text-terminal-200"
              >
                {showDateFilters ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                Date Filters
                {(simConfig.from_date || simConfig.to_date || simConfig.include_dates.length > 0 || simConfig.exclude_dates.length > 0) && (
                  <Badge variant="info" className="ml-1">active</Badge>
                )}
              </button>

              {showDateFilters && (
                <div className="mt-3 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="mb-1 block text-sm text-terminal-400">From</label>
                      <Input
                        type="date"
                        value={simConfig.from_date}
                        onChange={(event) => setSimConfig({ ...simConfig, from_date: event.target.value })}
                        disabled={simConfig.include_dates.length > 0}
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-sm text-terminal-400">To</label>
                      <Input
                        type="date"
                        value={simConfig.to_date}
                        onChange={(event) => setSimConfig({ ...simConfig, to_date: event.target.value })}
                        disabled={simConfig.include_dates.length > 0}
                      />
                    </div>
                  </div>

                  {simConfig.include_dates.length > 0 && (
                    <p className="-mt-1 text-xs text-terminal-500">From/To disabled — overridden by Include Dates</p>
                  )}

                  <div>
                    <label className="mb-1 block text-sm text-terminal-400">Exclude Dates</label>
                    <div className="flex gap-2">
                      <Input
                        type="date"
                        value={excludeDateInput}
                        onChange={(event) => setExcludeDateInput(event.target.value)}
                        className="flex-1"
                      />
                      <button
                        type="button"
                        onClick={() => addDate('exclude_dates', excludeDateInput, setExcludeDateInput)}
                        className="btn btn-ghost btn-sm"
                        disabled={!excludeDateInput}
                      >
                        <Plus className="size-4" />
                      </button>
                    </div>
                    {simConfig.exclude_dates.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {simConfig.exclude_dates.map((date) => (
                          <span key={date} className="inline-flex items-center gap-1 rounded bg-terminal-700 px-2 py-0.5 font-mono text-xs text-terminal-300">
                            {date}
                            <button
                              type="button"
                              onClick={() => removeDate('exclude_dates', date)}
                              className="hover:text-red-400"
                            >
                              <X className="size-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>

                  <div>
                    <label className="mb-1 block text-sm text-terminal-400">
                      Include Only
                      <span className="ml-1 text-terminal-500">(overrides From/To)</span>
                    </label>
                    <div className="flex gap-2">
                      <Input
                        type="date"
                        value={includeDateInput}
                        onChange={(event) => setIncludeDateInput(event.target.value)}
                        className="flex-1"
                      />
                      <button
                        type="button"
                        onClick={() => addDate('include_dates', includeDateInput, setIncludeDateInput)}
                        className="btn btn-ghost btn-sm"
                        disabled={!includeDateInput}
                      >
                        <Plus className="size-4" />
                      </button>
                    </div>
                    {simConfig.include_dates.length > 0 && (
                      <div className="mt-2 flex flex-wrap gap-1">
                        {simConfig.include_dates.map((date) => (
                          <span key={date} className="inline-flex items-center gap-1 rounded bg-terminal-700 px-2 py-0.5 font-mono text-xs text-terminal-300">
                            {date}
                            <button
                              type="button"
                              onClick={() => removeDate('include_dates', date)}
                              className="hover:text-red-400"
                            >
                              <X className="size-3" />
                            </button>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>

          <div className="flex gap-3 pt-2">
            <button onClick={onClose} className="btn btn-ghost flex-1" disabled={simulating}>
              Cancel
            </button>
            <FeedbackOnset onClick={onSimulate} latencyLabel="StartSim">
              <button className="btn btn-primary w-full" disabled={simulating}>
                <PlayCircle className="size-4" />
                {simulating ? 'Starting...' : 'Start Simulation'}
              </button>
            </FeedbackOnset>
          </div>
        </CardContent>
      </Card>
    </Modal>
  )
}
