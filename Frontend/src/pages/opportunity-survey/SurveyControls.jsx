import { ChevronDown, ChevronUp, Shuffle } from 'lucide-react'
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Select,
  Toggle,
} from '../../components/ui'
import {
  modeOptions,
  REVEAL_MODE_ENTRY,
  REVEAL_MODE_EXIT,
  REVEAL_MODE_OFFSET,
} from './surveyUtils'

export default function SurveyControls({
  applyRevealEntry,
  applyRevealExit,
  applyRevealExitPlusHours,
  batchOffset,
  batchSize,
  bounds,
  currentPage,
  dateFrom,
  dateTo,
  directionFilter,
  directionOptions,
  exitPlusHoursApplied,
  fetchCandidates,
  filteredCount,
  maxRevealAllowed,
  mode,
  postExitHours,
  presentationMode,
  randomSample,
  revealMode,
  revealOffsetMin,
  strategyFilter,
  strategyOptions,
  topPanelsCollapsed,
  totalPages,
  setBatchOffset,
  setBatchSize,
  setDateFrom,
  setDateTo,
  setDirectionFilter,
  setMode,
  setPostExitHours,
  setPresentationMode,
  setRandomSample,
  setRevealMode,
  setRevealOffsetMin,
  setStrategyFilter,
  setTopPanelsCollapsed,
}) {
  return (
    <>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-2xl font-bold text-terminal-100">Expired Opportunity Survey</h1>
            <p className="text-sm text-terminal-400">Forward-blind stack review for visual pattern training</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setTopPanelsCollapsed((value) => !value)}
            title={topPanelsCollapsed ? 'Show controls above stack view' : 'Collapse controls above stack view'}
          >
            {topPanelsCollapsed ? <ChevronDown className="size-4" /> : <ChevronUp className="size-4" />}
            {topPanelsCollapsed ? 'Show Top Panels' : 'Collapse Top Panels'}
          </button>
        </div>
      </div>

      {!topPanelsCollapsed && (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center gap-3">
              <Select value={mode} onChange={(event) => setMode(event.target.value)} options={modeOptions} className="w-64" />
              <Select
                value={batchSize}
                onChange={(event) => setBatchSize(Number(event.target.value))}
                options={[25, 50, 100].map((value) => ({ value, label: `${value}` }))}
                className="w-24"
              />
              <Toggle enabled={randomSample} onChange={setRandomSample} label="Random Sample" />
              <Toggle enabled={presentationMode} onChange={setPresentationMode} label="Presentation Mode (Landscape)" />
              <Select
                value={strategyFilter}
                onChange={(event) => setStrategyFilter(event.target.value)}
                options={strategyOptions}
                className="w-48"
              />
              <Select
                value={directionFilter}
                onChange={(event) => setDirectionFilter(event.target.value)}
                options={directionOptions}
                className="w-44"
              />
              <div className="flex items-center gap-2">
                <label className="text-xs text-terminal-400" htmlFor="survey-date-from">From</label>
                <input
                  id="survey-date-from"
                  type="date"
                  value={dateFrom}
                  onChange={(event) => setDateFrom(event.target.value)}
                  className="rounded border border-terminal-600 bg-terminal-900 px-2 py-1 text-sm text-terminal-100"
                />
                <label className="text-xs text-terminal-400" htmlFor="survey-date-to">To</label>
                <input
                  id="survey-date-to"
                  type="date"
                  value={dateTo}
                  onChange={(event) => setDateTo(event.target.value)}
                  className="rounded border border-terminal-600 bg-terminal-900 px-2 py-1 text-sm text-terminal-100"
                />
              </div>
              <button className="btn btn-secondary btn-sm" onClick={fetchCandidates}>
                <Shuffle className="size-4" />
                Reload Batch
              </button>
              <div className="ml-auto flex items-center gap-2">
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={batchOffset === 0}
                  onClick={() => setBatchOffset((value) => Math.max(0, value - batchSize))}
                >
                  Prev
                </button>
                <span className="text-sm text-terminal-400">
                  Page {totalPages > 0 ? currentPage : 0}/{Math.max(totalPages, 1)}
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={batchOffset + batchSize >= filteredCount}
                  onClick={() => setBatchOffset((value) => value + batchSize)}
                >
                  Next
                </button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Reveal Controls</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <button className="btn btn-secondary btn-sm" onClick={applyRevealEntry}>Up To Entry</button>
                <button className="btn btn-secondary btn-sm" onClick={applyRevealExit}>Up To Exit</button>
                <div className="flex items-center gap-2">
                  <span className="text-xs text-terminal-400">N hours post-exit</span>
                  <input
                    type="number"
                    min="0"
                    step="0.5"
                    value={postExitHours}
                    onChange={(event) => setPostExitHours(event.target.value)}
                    className="w-20 rounded border border-terminal-600 bg-terminal-900 px-2 py-1 text-sm text-terminal-100"
                  />
                  <button className="btn btn-primary btn-sm" onClick={applyRevealExitPlusHours}>
                    Apply Exit + N
                  </button>
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm text-terminal-400">
                  <span>Global offset from entry (minutes)</span>
                  <span className="font-mono text-terminal-200">
                    {revealMode === REVEAL_MODE_OFFSET
                      ? `${revealOffsetMin}m`
                      : revealMode === REVEAL_MODE_ENTRY
                        ? 'entry +5m'
                        : revealMode === REVEAL_MODE_EXIT
                          ? 'per-row exit'
                          : `per-row exit +${exitPlusHoursApplied}h`}
                  </span>
                </div>
                <input
                  type="range"
                  min={bounds.min}
                  max={maxRevealAllowed}
                  step={5}
                  value={revealOffsetMin}
                  onChange={(event) => {
                    setRevealMode(REVEAL_MODE_OFFSET)
                    setRevealOffsetMin(Number(event.target.value))
                  }}
                  className="w-full"
                />
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </>
  )
}
