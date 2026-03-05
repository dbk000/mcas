import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  ChevronDown,
  ChevronUp,
  Shuffle,
  XCircle,
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Select,
  Toggle,
} from '../components/ui'
import { formatPrice } from '../utils/formatters'
import OpportunityCurveChart from '../components/opportunities/OpportunityCurveChart'
import { getSurveyRowChartProps } from '../components/opportunities/chartPresets'
import { getEventColor } from '../components/opportunities/chartEventPalette'

const REVEAL_STEP_MIN = 5
const ENTRY_REVEAL_OFFSET_MIN = 5
// Macro context window for pattern training: 6h pre-entry history.
const PRE_S = 21600
const POST_S = 21600

const modeOptions = [
  { value: 'selected', label: 'Selected Opps (Mode A)' },
  { value: 'winners', label: 'All Winners (Mode B)' },
  { value: 'losers', label: 'All Losers (Mode C)' },
  { value: 'mixed', label: 'Mixed Hidden Labels (Mode D)' },
]

function clamp(v, min, max) {
  return Math.min(max, Math.max(min, v))
}

function roundToStep(value, step = REVEAL_STEP_MIN) {
  return Math.round(value / step) * step
}

function parseIds(raw) {
  if (!raw) return []
  const ids = raw
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => Number(part))
    .filter((n) => Number.isFinite(n))
  return Array.from(new Set(ids))
}

function getRowRevealTs(windowData, revealOffsetMin) {
  const entryTs = windowData?.markers?.entry_ts
  const tStart = windowData?.t_start
  const tEnd = windowData?.t_end
  if (!entryTs || !tStart || !tEnd) return null
  const revealTs = entryTs + (revealOffsetMin * 60 * 1000)
  return clamp(revealTs, tStart, tEnd)
}

function getBoundsForWindow(windowData) {
  const entryTs = windowData?.markers?.entry_ts
  const exitTs = windowData?.markers?.exit_ts
  const tStart = windowData?.t_start
  const tEnd = windowData?.t_end
  if (!entryTs || !tStart || !tEnd) return null

  const minOffset = roundToStep((tStart - entryTs) / 60000)
  const maxOffset = roundToStep((tEnd - entryTs) / 60000)
  const exitOffset = exitTs ? roundToStep((exitTs - entryTs) / 60000) : 0
  return { minOffset, maxOffset, exitOffset }
}

function pickColumns(presentationMode) {
  if (!presentationMode) return 1
  return 2
}

function isValidEventTs(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0
}

function isValidEventPrice(value) {
  const n = Number(value)
  return Number.isFinite(n) && n > 0
}

function formatEventTs(ts, includeDate = false) {
  if (!isValidEventTs(ts)) return '—'
  const d = new Date(Number(ts))
  const hh = String(d.getUTCHours()).padStart(2, '0')
  const mm = String(d.getUTCMinutes()).padStart(2, '0')
  if (!includeDate) return `${hh}:${mm}`
  const dd = String(d.getUTCDate()).padStart(2, '0')
  const mo = String(d.getUTCMonth() + 1).padStart(2, '0')
  const yy = String(d.getUTCFullYear()).slice(-2)
  return `${dd}/${mo}/${yy} ${hh}:${mm}`
}

function withAlpha(hexColor, alphaHex = 'cc') {
  if (typeof hexColor !== 'string') return '#94a3b8'
  if (/^#[0-9a-fA-F]{6}$/.test(hexColor)) return `${hexColor}${alphaHex}`
  return hexColor
}

function extractTsFromExitPoints(exitPoints, token) {
  if (!Array.isArray(exitPoints)) return null
  const tokenLower = String(token || '').toLowerCase()
  for (const point of exitPoints) {
    const label = String(point?.label || '').toLowerCase()
    const ts = Number(point?.ts)
    if (!isValidEventTs(ts)) continue
    if (label.includes(tokenLower)) return ts
  }
  return null
}

function buildTradeEventRows(markers) {
  const exitPoints = Array.isArray(markers?.exit_points) ? markers.exit_points : []
  const tp = Array.isArray(markers?.tp_px) ? markers.tp_px : []
  const tp1Ts = extractTsFromExitPoints(exitPoints, 'tp1')
  const tp2Ts = extractTsFromExitPoints(exitPoints, 'tp2')
  const stopTs = extractTsFromExitPoints(exitPoints, 'stop') ?? extractTsFromExitPoints(exitPoints, 'sl')
  const finalExitTs = isValidEventTs(markers?.exit_ts) ? Number(markers.exit_ts) : null

  const rows = [
    {
      key: 'entry',
      label: 'ENTRY',
      price: isValidEventPrice(markers?.entry_px) ? Number(markers.entry_px) : null,
      ts: isValidEventTs(markers?.entry_ts) ? Number(markers.entry_ts) : null,
      color: getEventColor('ENTRY'),
      includeDate: true,
    },
    {
      key: 'tp1',
      label: 'TP1',
      price: isValidEventPrice(tp[0]) ? Number(tp[0]) : null,
      ts: tp1Ts,
      color: getEventColor('TP1'),
      includeDate: false,
    },
    {
      key: 'tp2',
      label: 'TP2',
      price: isValidEventPrice(tp[1]) ? Number(tp[1]) : null,
      ts: tp2Ts,
      color: getEventColor('TP2'),
      includeDate: false,
    },
    {
      key: 'stop',
      label: 'STOP',
      price: isValidEventPrice(markers?.stop_px) ? Number(markers.stop_px) : null,
      ts: stopTs,
      color: getEventColor('STOP'),
      includeDate: false,
    },
    {
      key: 'final',
      label: 'FINAL EXIT',
      price: isValidEventPrice(markers?.exit_px) ? Number(markers.exit_px) : null,
      ts: finalExitTs,
      color: getEventColor('FINAL EXIT', { isFinal: true }),
      includeDate: true,
    },
  ]

  return rows
}

export default function OpportunitySurvey() {
  const { get } = useApi()
  const [searchParams] = useSearchParams()

  const initialMode = modeOptions.some((m) => m.value === searchParams.get('mode'))
    ? searchParams.get('mode')
    : 'selected'
  const querySelectedIds = useMemo(() => parseIds(searchParams.get('ids')), [searchParams])

  const [mode, setMode] = useState(initialMode)
  const [batchSize, setBatchSize] = useState(50)
  const [batchOffset, setBatchOffset] = useState(0)
  const [randomSample, setRandomSample] = useState(true)
  const [presentationMode, setPresentationMode] = useState(false)
  const [hideLevelsUntilReveal, setHideLevelsUntilReveal] = useState(false)
  const [revealOffsetMin, setRevealOffsetMin] = useState(ENTRY_REVEAL_OFFSET_MIN)
  const [postExitHours, setPostExitHours] = useState(2)
  const [topPanelsCollapsed, setTopPanelsCollapsed] = useState(false)
  const [oppsLoading, setOppsLoading] = useState(false)
  const [oppsError, setOppsError] = useState('')
  const [candidates, setCandidates] = useState([])
  const [windowsById, setWindowsById] = useState({})
  const [windowErrorsById, setWindowErrorsById] = useState({})
  const requestVersionRef = useRef(0)

  const fetchCandidates = useCallback(async () => {
    setOppsLoading(true)
    setOppsError('')
    const reqId = ++requestVersionRef.current

    try {
      let rows = []
      if (mode === 'selected') {
        const params = {
          status: 'expired',
          dedup: false,
          include_outcome: true,
          limit: Math.max(batchSize, querySelectedIds.length || batchSize),
        }
        if (querySelectedIds.length > 0) {
          params.ids = querySelectedIds.join(',')
        }
        const result = await get('/v1/opportunities', params)
        rows = result?.opportunities || []
        if (querySelectedIds.length > 0) {
          const ordering = new Map(querySelectedIds.map((id, idx) => [id, idx]))
          rows.sort((a, b) => (ordering.get(a.id) ?? 1e9) - (ordering.get(b.id) ?? 1e9))
        }
      } else if (mode === 'winners' || mode === 'losers') {
        const result = await get('/v1/opportunities', {
          status: 'expired',
          dedup: false,
          include_outcome: true,
          outcome_class: mode === 'winners' ? 'winner' : 'loser',
          random_sample: randomSample,
          sample_seed: Date.now(),
          limit: Math.max(batchSize * 3, batchSize),
        })
        rows = result?.opportunities || []
      } else {
        const winnerTarget = Math.ceil(batchSize / 2)
        const loserTarget = Math.floor(batchSize / 2)
        const [winnerResult, loserResult] = await Promise.all([
          get('/v1/opportunities', {
            status: 'expired',
            dedup: false,
            include_outcome: true,
            outcome_class: 'winner',
            random_sample: true,
            sample_seed: Date.now(),
            limit: winnerTarget,
          }),
          get('/v1/opportunities', {
            status: 'expired',
            dedup: false,
            include_outcome: true,
            outcome_class: 'loser',
            random_sample: true,
            sample_seed: Date.now() + 137,
            limit: loserTarget,
          }),
        ])

        const merged = [
          ...(winnerResult?.opportunities || []),
          ...(loserResult?.opportunities || []),
        ]
        rows = merged.sort(() => (Math.random() - 0.5))
      }

      if (requestVersionRef.current !== reqId) return
      setCandidates(rows)
      setBatchOffset(0)
    } catch (err) {
      if (requestVersionRef.current !== reqId) return
      setOppsError(err.message || 'Failed to load survey opportunities')
      setCandidates([])
    } finally {
      if (requestVersionRef.current === reqId) {
        setOppsLoading(false)
      }
    }
  }, [mode, batchSize, querySelectedIds, randomSample, get])

  useEffect(() => {
    fetchCandidates()
  }, [fetchCandidates])

  const activeBatch = useMemo(
    () => candidates.slice(batchOffset, batchOffset + batchSize),
    [candidates, batchOffset, batchSize]
  )

  useEffect(() => {
    const need = activeBatch.filter((opp) => windowsById[opp.id] == null && windowErrorsById[opp.id] == null)
    if (need.length === 0) return

    let cancelled = false

    const run = async () => {
      const queue = [...need]
      const workers = Array.from({ length: Math.min(8, queue.length) }).map(async () => {
        while (!cancelled && queue.length > 0) {
          const opp = queue.shift()
          if (!opp) break
          try {
            const windowData = await get(`/v1/opportunities/${opp.id}/price-window`, {
              pre_s: PRE_S,
              post_s: POST_S,
            })
            if (cancelled) return
            setWindowsById((prev) => ({ ...prev, [opp.id]: windowData }))
          } catch (err) {
            if (cancelled) return
            setWindowErrorsById((prev) => ({ ...prev, [opp.id]: err.message || 'Failed to load window' }))
          }
        }
      })

      await Promise.all(workers)
    }

    run()
    return () => {
      cancelled = true
    }
  }, [activeBatch, windowsById, windowErrorsById, get])

  const bounds = useMemo(() => {
    const available = activeBatch
      .map((opp) => getBoundsForWindow(windowsById[opp.id]))
      .filter(Boolean)

    if (available.length === 0) {
      return { min: -60, max: 360, revealExit: 0 }
    }

    return {
      min: Math.min(...available.map((b) => b.minOffset), -60),
      max: Math.max(...available.map((b) => b.maxOffset), 360),
      revealExit: Math.max(...available.map((b) => b.exitOffset), 0),
    }
  }, [activeBatch, windowsById])

  const maxRevealAllowed = bounds.max

  useEffect(() => {
    setRevealOffsetMin((prev) => clamp(prev, bounds.min, maxRevealAllowed))
  }, [bounds.min, maxRevealAllowed])

  const applyRevealEntry = useCallback(() => {
    setRevealOffsetMin(clamp(ENTRY_REVEAL_OFFSET_MIN, bounds.min, maxRevealAllowed))
  }, [bounds.min, maxRevealAllowed])

  const applyRevealExit = useCallback(() => {
    setRevealOffsetMin(clamp(bounds.revealExit, bounds.min, maxRevealAllowed))
  }, [bounds.revealExit, bounds.min, maxRevealAllowed])

  const applyRevealExitPlusHours = useCallback(() => {
    const hours = Number(postExitHours)
    const safeHours = Number.isFinite(hours) ? Math.max(0, hours) : 0
    const plusOffsetMin = roundToStep(safeHours * 60)
    setRevealOffsetMin(clamp(bounds.revealExit + plusOffsetMin, bounds.min, maxRevealAllowed))
  }, [postExitHours, bounds.revealExit, bounds.min, maxRevealAllowed])

  const columns = pickColumns(presentationMode)

  const totalPages = Math.ceil(candidates.length / batchSize)
  const currentPage = Math.floor(batchOffset / batchSize) + 1

  if (oppsError) {
    return (
      <div className="space-y-4">
        <Link to="/opportunities" className="btn btn-ghost btn-sm">
          <ArrowLeft className="w-4 h-4" />
          Back to Opportunities
        </Link>
        <EmptyState icon={XCircle} title="Failed to load survey" description={oppsError} />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Link to="/opportunities" className="btn btn-ghost btn-sm">
            <ArrowLeft className="w-4 h-4" />
            Back
          </Link>
          <div>
            <h1 className="text-2xl font-bold text-terminal-100">Expired Opportunity Survey</h1>
            <p className="text-sm text-terminal-400">Forward-blind stack review for visual pattern training</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => setTopPanelsCollapsed((v) => !v)}
            title={topPanelsCollapsed ? 'Show controls above stack view' : 'Collapse controls above stack view'}
          >
            {topPanelsCollapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
            {topPanelsCollapsed ? 'Show Top Panels' : 'Collapse Top Panels'}
          </button>
        </div>
      </div>

      {!topPanelsCollapsed && (
        <>
          <Card>
            <CardContent className="flex flex-wrap items-center gap-3">
              <Select value={mode} onChange={(e) => setMode(e.target.value)} options={modeOptions} className="w-64" />
              <Select
                value={batchSize}
                onChange={(e) => setBatchSize(Number(e.target.value))}
                options={[25, 50, 100].map((n) => ({ value: n, label: `${n}` }))}
                className="w-24"
              />
              <Toggle enabled={randomSample} onChange={setRandomSample} label="Random Sample" />
              <Toggle enabled={presentationMode} onChange={setPresentationMode} label="Presentation Mode (Landscape)" />
              <Toggle
                enabled={hideLevelsUntilReveal}
                onChange={setHideLevelsUntilReveal}
                label="Hide SL/TP Until Reveal"
              />
              <button className="btn btn-secondary btn-sm" onClick={fetchCandidates}>
                <Shuffle className="w-4 h-4" />
                Reload Batch
              </button>
              <div className="ml-auto flex items-center gap-2">
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={batchOffset === 0}
                  onClick={() => setBatchOffset((v) => Math.max(0, v - batchSize))}
                >
                  Prev
                </button>
                <span className="text-sm text-terminal-400">
                  Page {totalPages > 0 ? currentPage : 0}/{Math.max(totalPages, 1)}
                </span>
                <button
                  className="btn btn-ghost btn-sm"
                  disabled={batchOffset + batchSize >= candidates.length}
                  onClick={() => setBatchOffset((v) => v + batchSize)}
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
                    onChange={(e) => setPostExitHours(e.target.value)}
                    className="w-20 px-2 py-1 rounded border border-terminal-600 bg-terminal-900 text-terminal-100 text-sm"
                  />
                  <button className="btn btn-primary btn-sm" onClick={applyRevealExitPlusHours}>
                    Apply Exit + N
                  </button>
                </div>
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between text-sm text-terminal-400">
                  <span>Global offset from entry (minutes)</span>
                  <span className="font-mono text-terminal-200">{revealOffsetMin}m</span>
                </div>
                <input
                  type="range"
                  min={bounds.min}
                  max={maxRevealAllowed}
                  step={REVEAL_STEP_MIN}
                  value={revealOffsetMin}
                  onChange={(e) => setRevealOffsetMin(Number(e.target.value))}
                  className="w-full"
                />
              </div>
            </CardContent>
          </Card>
        </>
      )}

      <Card>
        <CardContent className="space-y-3">
          {oppsLoading ? (
            <div className="text-terminal-400">Loading opportunities…</div>
          ) : activeBatch.length === 0 ? (
            <EmptyState icon={XCircle} title="No opportunities in this mode" description="Change mode or reload batch." />
          ) : (
            (() => {
              const rows = []
              for (let idx = 0; idx < activeBatch.length; idx += columns) {
                rows.push(activeBatch.slice(idx, idx + columns))
              }
              return rows.map((rowItems, rowIdx) => (
                <div
                  key={rowIdx}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))`,
                    gap: 10,
                  }}
                >
                  {rowItems.map((opp) => (
                    <SurveyRow
                      key={opp.id}
                      opp={opp}
                      windowData={windowsById[opp.id]}
                      windowError={windowErrorsById[opp.id]}
                      revealOffsetMin={revealOffsetMin}
                      mode={mode}
                      presentationMode={presentationMode}
                      hideLevelsUntilReveal={hideLevelsUntilReveal}
                    />
                  ))}
                </div>
              ))
            })()
          )}
        </CardContent>
      </Card>
    </div>
  )
}

function SurveyRow({
  opp,
  windowData,
  windowError,
  revealOffsetMin,
  mode,
  presentationMode,
  hideLevelsUntilReveal,
}) {
  const revealTs = windowData ? getRowRevealTs(windowData, revealOffsetMin) : null
  const chartPreset = getSurveyRowChartProps(presentationMode)
  const outcomeClass = windowData?.outcome?.class || opp.outcome_class || 'unknown'
  const hideOutcome = mode === 'mixed'
  const finalExitPriceRaw = windowData?.markers?.exit_px ?? opp?.exit_price ?? opp?.payload?.exit_price
  const finalExitPrice = Number.isFinite(Number(finalExitPriceRaw)) ? Number(finalExitPriceRaw) : null
  const markers = useMemo(() => {
    if (!windowData?.markers) return {}
    return {
      ...windowData.markers,
      exit_px: windowData.markers.exit_px ?? finalExitPrice,
    }
  }, [windowData, finalExitPrice])
  const tradeEventRows = useMemo(
    () => buildTradeEventRows(markers),
    [markers]
  )

  return (
    <div className="border border-terminal-600 rounded bg-terminal-900/40 p-3">
      <div className={`grid ${presentationMode ? 'grid-cols-[185px_minmax(0,1fr)]' : 'grid-cols-[210px_1fr]'} gap-3`}>
        <div className="text-xs space-y-1">
          <div className="flex items-baseline gap-2">
            <p className="font-mono text-terminal-100 text-sm">{opp.symbol}</p>
            <p className="font-mono text-terminal-500 text-[11px]">{opp.setup_id || '—'}</p>
            <p className="ml-auto font-mono text-terminal-400 text-[11px]">{(opp.direction || opp.decision || 'n/a').toUpperCase()}</p>
          </div>
          {!hideOutcome ? (
            <Badge variant={outcomeClass === 'winner' ? 'success' : outcomeClass === 'loser' ? 'error' : 'default'}>
              Outcome: {outcomeClass}
            </Badge>
          ) : (
            <Badge variant="warning">Outcome hidden</Badge>
          )}
          <div className="mt-2 rounded border border-terminal-700/70 bg-terminal-950/40 overflow-hidden">
            {tradeEventRows.map((event) => (
              <div
                key={event.key}
                className="grid grid-cols-[50px_92px_minmax(0,1fr)] text-[10px] font-mono"
                style={{ borderLeft: `2px solid ${event.color}` }}
              >
                <div
                  className="px-1 py-0.5 border-t border-terminal-800/80 font-semibold"
                  style={{ color: event.color }}
                >
                  {event.label}
                </div>
                <div
                  className="px-1 py-0.5 border-t border-l border-terminal-800/80"
                  style={{ color: withAlpha(event.color) }}
                >
                  {formatEventTs(event.ts, event.includeDate)}
                </div>
                <div
                  className="px-1 pr-1 py-0.5 border-t border-l border-terminal-800/80 font-semibold text-right"
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
            <div className="h-full min-h-[180px] border border-terminal-600 rounded flex items-center justify-center text-xs text-red-300">
              {windowError}
            </div>
          ) : !windowData ? (
            <div className="h-full min-h-[180px] border border-terminal-600 rounded flex items-center justify-center text-xs text-terminal-400">
              Loading curve…
            </div>
          ) : (
            <OpportunityCurveChart
              candles={windowData.candles || []}
              markers={markers}
              revealTs={revealTs}
              hideExitUntilReveal
              hideLevels={hideLevelsUntilReveal && revealOffsetMin <= 0}
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
