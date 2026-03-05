import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  BarChart3,
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
import { formatDateTime, formatPrice } from '../utils/formatters'
import OpportunityCurveChart from '../components/opportunities/OpportunityCurveChart'
import { getSurveyRowChartProps } from '../components/opportunities/chartPresets'

const REVEAL_STEP_MIN = 5
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
  const [revealOffsetMin, setRevealOffsetMin] = useState(0)
  const [postExitHours, setPostExitHours] = useState(2)
  const [topPanelsCollapsed, setTopPanelsCollapsed] = useState(false)
  const [oppsLoading, setOppsLoading] = useState(false)
  const [oppsError, setOppsError] = useState('')
  const [candidates, setCandidates] = useState([])
  const [windowsById, setWindowsById] = useState({})
  const [windowErrorsById, setWindowErrorsById] = useState({})
  const [loadingProgress, setLoadingProgress] = useState({ done: 0, total: 0 })

  const scrollRef = useRef(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState({ width: 0, height: 700 })
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
    if (need.length === 0) {
      setLoadingProgress({ done: 0, total: 0 })
      return
    }

    let cancelled = false
    const total = need.length
    let done = 0
    setLoadingProgress({ done, total })

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
          } finally {
            done += 1
            if (!cancelled) {
              setLoadingProgress({ done, total })
            }
          }
        }
      })

      await Promise.all(workers)
      if (!cancelled) {
        setLoadingProgress({ done: 0, total: 0 })
      }
    }

    run()
    return () => {
      cancelled = true
    }
  }, [activeBatch, windowsById, windowErrorsById, get])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return undefined
    const observer = new ResizeObserver(() => {
      setViewport({
        width: el.clientWidth,
        height: el.clientHeight,
      })
    })
    observer.observe(el)
    setViewport({ width: el.clientWidth, height: el.clientHeight })
    return () => observer.disconnect()
  }, [])

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
    setRevealOffsetMin(clamp(0, bounds.min, maxRevealAllowed))
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

  const rowHeight = presentationMode ? 320 : 250
  const columns = pickColumns(presentationMode)
  const rowCount = Math.ceil(activeBatch.length / columns)
  const overscan = 2
  const firstRow = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan)
  const lastRow = Math.min(
    rowCount,
    Math.ceil((scrollTop + viewport.height) / rowHeight) + overscan
  )

  const visibleRows = []
  for (let rowIdx = firstRow; rowIdx < lastRow; rowIdx += 1) {
    visibleRows.push(rowIdx)
  }

  useEffect(() => {
    if (!scrollRef.current) return
    const maxScrollTop = Math.max(0, (rowCount * rowHeight) - viewport.height)
    if (scrollTop > maxScrollTop) {
      const clamped = Math.max(0, maxScrollTop)
      scrollRef.current.scrollTop = clamped
      setScrollTop(clamped)
    }
  }, [rowCount, rowHeight, viewport.height, scrollTop])

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
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-amber-400" />
            Stack View
          </CardTitle>
          <div className="text-sm text-terminal-400">
            {activeBatch.length} curves in batch
            {loadingProgress.total > 0 && ` • loading ${loadingProgress.done}/${loadingProgress.total}`}
          </div>
        </CardHeader>
        <CardContent>
          {oppsLoading ? (
            <div className="text-terminal-400">Loading opportunities…</div>
          ) : activeBatch.length === 0 ? (
            <EmptyState icon={XCircle} title="No opportunities in this mode" description="Change mode or reload batch." />
          ) : (
              <div
                ref={scrollRef}
                onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
                className={`${topPanelsCollapsed ? 'h-[84vh]' : 'h-[70vh]'} overflow-auto`}
              >
              <div style={{ height: rowCount * rowHeight, position: 'relative' }}>
                {visibleRows.map((rowIdx) => {
                  const start = rowIdx * columns
                  const rowItems = activeBatch.slice(start, start + columns)
                  return (
                    <div
                      key={rowIdx}
                      style={{
                        position: 'absolute',
                        top: rowIdx * rowHeight,
                        left: 0,
                        right: 0,
                        height: rowHeight,
                        padding: 8,
                      }}
                    >
                      <div
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
                    </div>
                  )
                })}
              </div>
            </div>
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

  return (
    <div className="border border-terminal-600 rounded bg-terminal-900/40 p-3 h-[calc(100%-0px)] overflow-hidden">
      <div className={`grid ${presentationMode ? 'grid-cols-[150px_minmax(0,1fr)]' : 'grid-cols-[170px_1fr]'} gap-3 h-full`}>
        <div className="text-xs space-y-1">
          <p className="font-mono text-terminal-100 text-sm">{opp.symbol}</p>
          <p className="text-terminal-400">{(opp.direction || opp.decision || 'n/a').toUpperCase()}</p>
          <p className="text-terminal-500">Entry: {formatDateTime(opp.entry_ts || opp.created_ts)}</p>
          <p className="text-terminal-500">Setup: {opp.setup_id}</p>
          {!hideOutcome ? (
            <Badge variant={outcomeClass === 'winner' ? 'success' : outcomeClass === 'loser' ? 'error' : 'default'}>
              Outcome: {outcomeClass}
            </Badge>
          ) : (
            <Badge variant="warning">Outcome hidden</Badge>
          )}
          {!hideOutcome && (
            <p className="text-terminal-500">
              Final Exit: <span className="font-mono text-sky-300">{finalExitPrice != null ? formatPrice(finalExitPrice) : '—'}</span>
            </p>
          )}
        </div>

        <div className="h-full min-w-0">
          {windowError ? (
            <div className="h-full border border-terminal-600 rounded flex items-center justify-center text-xs text-red-300">
              {windowError}
            </div>
          ) : !windowData ? (
            <div className="h-full border border-terminal-600 rounded flex items-center justify-center text-xs text-terminal-400">
              Loading curve…
            </div>
          ) : (
            <OpportunityCurveChart
              candles={windowData.candles || []}
              markers={markers}
              revealTs={revealTs}
              hideExitUntilReveal
              hideLevels={hideLevelsUntilReveal && revealOffsetMin <= 0}
              {...chartPreset}
            />
          )}
        </div>

      </div>
    </div>
  )
}
