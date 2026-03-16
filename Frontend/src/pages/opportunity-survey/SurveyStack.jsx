import { useMemo } from 'react'
import { XCircle } from 'lucide-react'
import { EmptyState } from '../../components/ui'
import OpportunityCurveChart from '../../components/opportunities/OpportunityCurveChart'
import { getSurveyRowChartProps } from '../../components/opportunities/chartPresets'
import { formatPrice } from '../../utils/formatters'
import {
  buildTradeEventRows,
  formatEventTs,
  getRowRevealTs,
  resolveRowRevealOffsetMin,
  withAlpha,
} from './surveyUtils'

function SurveyRow({
  exitPlusHoursApplied,
  mode,
  opportunity,
  presentationMode,
  revealMode,
  revealOffsetMin,
  windowData,
  windowError,
}) {
  const effectiveRevealOffsetMin = windowData
    ? resolveRowRevealOffsetMin(windowData, revealMode, revealOffsetMin, exitPlusHoursApplied)
    : revealOffsetMin
  const revealTs = windowData ? getRowRevealTs(windowData, effectiveRevealOffsetMin) : null
  const chartPreset = getSurveyRowChartProps(presentationMode)
  const outcomeClass = windowData?.outcome?.class || opportunity.outcome_class || 'unknown'
  const hideOutcome = mode === 'mixed'
  const finalExitPriceRaw = windowData?.markers?.exit_px ?? opportunity?.exit_price ?? opportunity?.payload?.exit_price
  const finalExitPrice = Number.isFinite(Number(finalExitPriceRaw)) ? Number(finalExitPriceRaw) : null
  const markers = useMemo(() => {
    if (!windowData?.markers) {
      return {}
    }
    return {
      ...windowData.markers,
      exit_px: windowData.markers.exit_px ?? finalExitPrice,
    }
  }, [finalExitPrice, windowData])
  const tradeEventRows = useMemo(() => buildTradeEventRows(markers), [markers])

  return (
    <div className="rounded border border-terminal-600 bg-terminal-900/40 p-3">
      <div className={`grid ${presentationMode ? 'grid-cols-[185px_minmax(0,1fr)]' : 'grid-cols-[210px_1fr]'} gap-3`}>
        <div className="space-y-1 text-xs">
          <div className="flex items-baseline gap-2">
            <p className="font-mono text-sm text-terminal-100">{opportunity.symbol}</p>
            <p className="font-mono text-[11px] text-terminal-500">{opportunity.setup_id || '—'}</p>
            <p className="ml-auto font-mono text-[11px] text-terminal-400">{(opportunity.direction || opportunity.decision || 'n/a').toUpperCase()}</p>
          </div>
          {!hideOutcome ? (
            <span className={`badge ${outcomeClass === 'winner' ? 'border border-green-700/50 bg-green-900/50 text-green-400' : outcomeClass === 'loser' ? 'border border-red-700/50 bg-red-900/50 text-red-400' : 'bg-terminal-600 text-terminal-200'}`}>
              Outcome: {outcomeClass}
            </span>
          ) : (
            <span className="badge border border-amber-700/50 bg-amber-900/50 text-amber-400">Outcome hidden</span>
          )}
          <div className="bg-terminal-950/40 mt-2 overflow-hidden rounded border border-terminal-700/70">
            {tradeEventRows.map((event) => (
              <div
                key={event.key}
                className="grid grid-cols-[50px_92px_minmax(0,1fr)] font-mono text-[10px]"
                style={{ borderLeft: `2px solid ${event.color}` }}
              >
                <div
                  className="border-t border-terminal-800/80 px-1 py-0.5 font-semibold"
                  style={{ color: event.color }}
                >
                  {event.label}
                </div>
                <div
                  className="border-l border-t border-terminal-800/80 px-1 py-0.5"
                  style={{ color: withAlpha(event.color) }}
                >
                  {formatEventTs(event.ts, event.includeDate)}
                </div>
                <div
                  className="border-l border-t border-terminal-800/80 px-1 py-0.5 text-right font-semibold"
                  style={{ color: event.color }}
                >
                  {event.price != null ? formatPrice(event.price) : '—'}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="min-w-0">
          {windowError ? (
            <div className="flex h-full min-h-[180px] items-center justify-center rounded border border-terminal-600 text-xs text-red-300">
              {windowError}
            </div>
          ) : !windowData ? (
            <div className="flex h-full min-h-[180px] items-center justify-center rounded border border-terminal-600 text-xs text-terminal-400">
              Loading curve…
            </div>
          ) : (
            <OpportunityCurveChart
              candles={windowData.candles || []}
              markers={markers}
              revealTs={revealTs}
              hideExitUntilReveal
              showLevelLineLabels={false}
              showLevelLegend={false}
              {...chartPreset}
            />
          )}
        </div>
      </div>
    </div>
  )
}

export default function SurveyStack({
  activeBatch,
  columns,
  exitPlusHoursApplied,
  mode,
  oppsLoading,
  presentationMode,
  revealMode,
  revealOffsetMin,
  windowErrorsById,
  windowsById,
}) {
  if (oppsLoading) {
    return <div className="text-terminal-400">Loading opportunities…</div>
  }

  if (activeBatch.length === 0) {
    return (
      <EmptyState
        icon={XCircle}
        title="No opportunities match these filters"
        description="Adjust filters or reload batch."
      />
    )
  }

  const rows = []
  for (let index = 0; index < activeBatch.length; index += columns) {
    rows.push(activeBatch.slice(index, index + columns))
  }

  return rows.map((rowItems, rowIndex) => (
    <div
      key={rowIndex}
      style={{
        display: 'grid',
        gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
        gap: 10,
      }}
    >
      {rowItems.map((opportunity) => (
        <SurveyRow
          key={opportunity.id}
          opportunity={opportunity}
          windowData={windowsById[opportunity.id]}
          windowError={windowErrorsById[opportunity.id]}
          revealOffsetMin={revealOffsetMin}
          revealMode={revealMode}
          exitPlusHoursApplied={exitPlusHoursApplied}
          mode={mode}
          presentationMode={presentationMode}
        />
      ))}
    </div>
  ))
}
