import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ArrowLeft,
  BarChart3,
  CheckCircle2,
  Pause,
  Play,
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
  DataRow,
  EmptyState,
  Select,
  Toggle,
} from '../components/ui'
import { formatDateTime } from '../utils/formatters'
import OpportunityCurveChart from '../components/opportunities/OpportunityCurveChart'

const PREDICTIONS_KEY = 'mcas_visual_predictions_v1'
const REVEAL_STEP_MIN = 5
const PRE_S = 3600
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

function hasExitTimestamp(opp) {
  const v = opp?.exit_ts ?? opp?.payload?.exit_ts
  if (v == null) return false
  if (typeof v === 'string') return v.trim().length > 0
  return true
}

function readPredictionStore() {
  try {
    const raw = localStorage.getItem(PREDICTIONS_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' ? parsed : {}
  } catch {
    return {}
  }
}

function writePredictionStore(store) {
  localStorage.setItem(PREDICTIONS_KEY, JSON.stringify(store))
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

function pickColumns(presentationMode, width) {
  if (!presentationMode) return 1
  if (width >= 1800) return 3
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
  const [revealOffsetMin, setRevealOffsetMin] = useState(0) // Assumption: default reveal at entry.
  const [autoplayMs, setAutoplayMs] = useState(500)
  const [autoplayOn, setAutoplayOn] = useState(false)
  const [oppsLoading, setOppsLoading] = useState(false)
  const [oppsError, setOppsError] = useState('')
  const [skippedMissingExitCount, setSkippedMissingExitCount] = useState(0)
  const [candidates, setCandidates] = useState([])
  const [windowsById, setWindowsById] = useState({})
  const [windowErrorsById, setWindowErrorsById] = useState({})
  const [loadingProgress, setLoadingProgress] = useState({ done: 0, total: 0 })
  const [confidenceDraft, setConfidenceDraft] = useState({})
  const [predictions, setPredictions] = useState(() => readPredictionStore())

  const scrollRef = useRef(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewport, setViewport] = useState({ width: 0, height: 700 })
  const requestVersionRef = useRef(0)

  const fetchCandidates = useCallback(async () => {
    setOppsLoading(true)
    setOppsError('')
    setSkippedMissingExitCount(0)
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
      const chartableRows = rows.filter(hasExitTimestamp)
      setSkippedMissingExitCount(Math.max(0, rows.length - chartableRows.length))
      setCandidates(chartableRows)
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
    writePredictionStore(predictions)
  }, [predictions])

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

  const committedForBatch = useMemo(
    () => activeBatch.length > 0 && activeBatch.every((opp) => !!predictions[opp.id]),
    [activeBatch, predictions]
  )

  const maxRevealAllowed = committedForBatch ? bounds.max : 0

  useEffect(() => {
    setRevealOffsetMin((prev) => clamp(prev, bounds.min, maxRevealAllowed))
  }, [bounds.min, maxRevealAllowed])

  useEffect(() => {
    if (!autoplayOn) return undefined
    if (!committedForBatch) return undefined

    const interval = setInterval(() => {
      setRevealOffsetMin((prev) => {
        const next = prev + REVEAL_STEP_MIN
        if (next >= bounds.max) {
          setAutoplayOn(false)
          return bounds.max
        }
        return next
      })
    }, autoplayMs)

    return () => clearInterval(interval)
  }, [autoplayOn, autoplayMs, bounds.max, committedForBatch])

  const commitPrediction = (oppId, predictedClass) => {
    const confidence = Number(confidenceDraft[oppId] || 3)
    setPredictions((prev) => ({
      ...prev,
      [oppId]: {
        predicted_class: predictedClass,
        confidence,
        reveal_offset_at_commit: revealOffsetMin,
        created_at: new Date().toISOString(),
      },
    }))
  }

  const rowHeight = presentationMode ? 320 : 250
  const columns = pickColumns(presentationMode, viewport.width)
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
          <Badge variant={committedForBatch ? 'success' : 'warning'}>
            {committedForBatch ? 'Predictions Committed' : 'Commit Required Before Forward Reveal'}
          </Badge>
        </div>
      </div>

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

      {skippedMissingExitCount > 0 && (
        <div className="px-3 py-2 rounded border border-amber-700/60 bg-amber-500/10 text-amber-300 text-xs">
          Skipped {skippedMissingExitCount} expired opportunit{skippedMissingExitCount === 1 ? 'y' : 'ies'} missing
          {' '}`exit_ts` (non-chartable by design).
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Reveal Controls</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <button className="btn btn-ghost btn-sm" onClick={() => setRevealOffsetMin((v) => clamp(v - 30, bounds.min, maxRevealAllowed))}>-30m</button>
            <button className="btn btn-ghost btn-sm" onClick={() => setRevealOffsetMin((v) => clamp(v - 5, bounds.min, maxRevealAllowed))}>-5m</button>
            <button
              className="btn btn-secondary btn-sm"
              disabled={!committedForBatch}
              onClick={() => setRevealOffsetMin((v) => clamp(v + 5, bounds.min, maxRevealAllowed))}
            >
              +5m
            </button>
            <button
              className="btn btn-secondary btn-sm"
              disabled={!committedForBatch}
              onClick={() => setRevealOffsetMin((v) => clamp(v + 30, bounds.min, maxRevealAllowed))}
            >
              +30m
            </button>
            <button className="btn btn-ghost btn-sm" onClick={() => setRevealOffsetMin(0)}>Jump Entry</button>
            <button
              className="btn btn-ghost btn-sm"
              disabled={!committedForBatch}
              onClick={() => setRevealOffsetMin(clamp(bounds.revealExit, bounds.min, maxRevealAllowed))}
            >
              Reveal Exit
            </button>
            <button
              className="btn btn-primary btn-sm"
              disabled={!committedForBatch}
              onClick={() => setRevealOffsetMin(maxRevealAllowed)}
            >
              Reveal All
            </button>

            <button
              className="btn btn-ghost btn-sm"
              disabled={!committedForBatch}
              onClick={() => setAutoplayOn((v) => !v)}
            >
              {autoplayOn ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              {autoplayOn ? 'Stop' : 'Autoplay'}
            </button>

            <Select
              value={autoplayMs}
              onChange={(e) => setAutoplayMs(Number(e.target.value))}
              options={[
                { value: 250, label: '250ms' },
                { value: 500, label: '500ms' },
                { value: 1000, label: '1000ms' },
              ]}
              className="w-24"
            />
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between text-sm text-terminal-400">
              <span>Offset from entry (minutes)</span>
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
              className="h-[70vh] overflow-auto border border-terminal-600 rounded"
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
                            prediction={predictions[opp.id]}
                            onCommit={commitPrediction}
                            confidence={Number(confidenceDraft[opp.id] || predictions[opp.id]?.confidence || 3)}
                            onConfidenceChange={(v) => setConfidenceDraft((prev) => ({ ...prev, [opp.id]: Number(v) }))}
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
  prediction,
  onCommit,
  confidence,
  onConfidenceChange,
  hideLevelsUntilReveal,
}) {
  const revealTs = windowData ? getRowRevealTs(windowData, revealOffsetMin) : null
  const outcomeClass = windowData?.outcome?.class || opp.outcome_class || 'unknown'
  const hideOutcome = mode === 'mixed' && !prediction

  return (
    <div className="border border-terminal-600 rounded bg-terminal-900/40 p-3 h-[calc(100%-0px)]">
      <div className="grid grid-cols-[170px_1fr_220px] gap-3 h-full">
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
          {prediction && !hideOutcome && outcomeClass !== 'unknown' && (
            prediction.predicted_class === outcomeClass ? (
              <Badge variant="success"><CheckCircle2 className="w-3 h-3 mr-1" />Correct</Badge>
            ) : (
              <Badge variant="error"><XCircle className="w-3 h-3 mr-1" />Miss</Badge>
            )
          )}
        </div>

        <div className="h-full">
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
              markers={windowData.markers || {}}
              revealTs={revealTs}
              hideExitUntilReveal
              hideLevels={hideLevelsUntilReveal && revealOffsetMin <= 0}
              compact
              height={190}
            />
          )}
        </div>

        <div className="text-xs space-y-2 border-l border-terminal-700 pl-3">
          <p className="text-terminal-400 uppercase tracking-wide">Commit</p>
          <div>
            <label className="text-terminal-500 block mb-1">Confidence</label>
            <Select
              value={confidence}
              onChange={(e) => onConfidenceChange(e.target.value)}
              options={[1, 2, 3, 4, 5].map((v) => ({ value: v, label: `${v}` }))}
            />
          </div>
          <div className="flex gap-2">
            <button className="btn btn-secondary btn-sm flex-1" onClick={() => onCommit(opp.id, 'winner')}>
              Predict Winner
            </button>
            <button className="btn btn-secondary btn-sm flex-1" onClick={() => onCommit(opp.id, 'loser')}>
              Predict Loser
            </button>
          </div>
          {prediction ? (
            <div className="text-terminal-300 bg-terminal-800 rounded px-2 py-1">
              <DataRow label="Pred" value={prediction.predicted_class} />
              <DataRow label="Conf" value={prediction.confidence} />
            </div>
          ) : (
            <p className="text-amber-300">Commit to unlock forward reveal.</p>
          )}
        </div>
      </div>
    </div>
  )
}
