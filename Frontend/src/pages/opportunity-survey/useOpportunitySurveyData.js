import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  POST_S,
  PRE_S,
  REVEAL_MODE_ENTRY,
  REVEAL_MODE_EXIT,
  REVEAL_MODE_EXIT_PLUS,
  REVEAL_MODE_OFFSET,
  ENTRY_REVEAL_OFFSET_MIN,
  clamp,
  getBoundsForWindow,
  modeOptions,
  parseIds,
  pickColumns,
  resolveOppDirection,
  resolveOppStrategy,
  resolveOppTsMs,
  roundToStep,
  toDateBoundaryMs,
} from './surveyUtils'

export function useOpportunitySurveyData({ get, searchParams }) {
  const initialMode = modeOptions.some((option) => option.value === searchParams.get('mode'))
    ? searchParams.get('mode')
    : 'selected'
  const querySelectedIds = useMemo(() => parseIds(searchParams.get('ids')), [searchParams])

  const [mode, setMode] = useState(initialMode)
  const [batchSize, setBatchSize] = useState(50)
  const [batchOffset, setBatchOffset] = useState(0)
  const [randomSample, setRandomSample] = useState(true)
  const [presentationMode, setPresentationMode] = useState(false)
  const [strategyFilter, setStrategyFilter] = useState('all')
  const [directionFilter, setDirectionFilter] = useState('all')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [revealOffsetMin, setRevealOffsetMin] = useState(ENTRY_REVEAL_OFFSET_MIN)
  const [revealMode, setRevealMode] = useState(REVEAL_MODE_OFFSET)
  const [exitPlusHoursApplied, setExitPlusHoursApplied] = useState(0)
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
          const ordering = new Map(querySelectedIds.map((id, index) => [id, index]))
          rows.sort((left, right) => (ordering.get(left.id) ?? 1e9) - (ordering.get(right.id) ?? 1e9))
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

        rows = [
          ...(winnerResult?.opportunities || []),
          ...(loserResult?.opportunities || []),
        ].sort(() => (Math.random() - 0.5))
      }

      if (requestVersionRef.current !== reqId) {
        return
      }

      setCandidates(rows)
      setBatchOffset(0)
    } catch (error) {
      if (requestVersionRef.current !== reqId) {
        return
      }
      setOppsError(error.message || 'Failed to load survey opportunities')
      setCandidates([])
    } finally {
      if (requestVersionRef.current === reqId) {
        setOppsLoading(false)
      }
    }
  }, [batchSize, get, mode, querySelectedIds, randomSample])

  useEffect(() => {
    fetchCandidates()
  }, [fetchCandidates])

  const strategyOptions = useMemo(() => {
    const unique = new Set(candidates.map((opportunity) => resolveOppStrategy(opportunity)))
    const sorted = Array.from(unique).sort((left, right) => left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' }))
    return [
      { value: 'all', label: 'All Strategies' },
      ...sorted.map((value) => ({ value, label: value })),
    ]
  }, [candidates])

  const directionOptions = useMemo(() => {
    const available = new Set(candidates.map((opportunity) => resolveOppDirection(opportunity)))
    const options = [{ value: 'all', label: 'All Directions' }]
    if (available.has('long')) {
      options.push({ value: 'long', label: 'Long' })
    }
    if (available.has('short')) {
      options.push({ value: 'short', label: 'Short' })
    }
    if (available.has('unknown')) {
      options.push({ value: 'unknown', label: 'Unknown' })
    }
    return options
  }, [candidates])

  useEffect(() => {
    if (strategyFilter !== 'all' && !strategyOptions.some((option) => option.value === strategyFilter)) {
      setStrategyFilter('all')
    }
  }, [strategyFilter, strategyOptions])

  useEffect(() => {
    if (directionFilter !== 'all' && !directionOptions.some((option) => option.value === directionFilter)) {
      setDirectionFilter('all')
    }
  }, [directionFilter, directionOptions])

  const filteredCandidates = useMemo(() => {
    const fromMs = toDateBoundaryMs(dateFrom, false)
    const toMs = toDateBoundaryMs(dateTo, true)

    return candidates.filter((opportunity) => {
      if (strategyFilter !== 'all' && resolveOppStrategy(opportunity) !== strategyFilter) {
        return false
      }
      if (directionFilter !== 'all' && resolveOppDirection(opportunity) !== directionFilter) {
        return false
      }
      if (fromMs == null && toMs == null) {
        return true
      }

      const opportunityTsMs = resolveOppTsMs(opportunity)
      if (opportunityTsMs == null) {
        return false
      }
      if (fromMs != null && opportunityTsMs < fromMs) {
        return false
      }
      if (toMs != null && opportunityTsMs > toMs) {
        return false
      }
      return true
    })
  }, [candidates, dateFrom, dateTo, directionFilter, strategyFilter])

  useEffect(() => {
    setBatchOffset(0)
  }, [strategyFilter, directionFilter, dateFrom, dateTo])

  const activeBatch = useMemo(
    () => filteredCandidates.slice(batchOffset, batchOffset + batchSize),
    [batchOffset, batchSize, filteredCandidates]
  )

  useEffect(() => {
    const need = activeBatch.filter((opportunity) => windowsById[opportunity.id] == null && windowErrorsById[opportunity.id] == null)
    if (need.length === 0) {
      return undefined
    }

    let cancelled = false

    const run = async () => {
      const queue = [...need]
      const workers = Array.from({ length: Math.min(8, queue.length) }).map(async () => {
        while (!cancelled && queue.length > 0) {
          const opportunity = queue.shift()
          if (!opportunity) {
            break
          }
          try {
            const windowData = await get(`/v1/opportunities/${opportunity.id}/price-window`, {
              pre_s: PRE_S,
              post_s: POST_S,
            })
            if (!cancelled) {
              setWindowsById((previous) => ({ ...previous, [opportunity.id]: windowData }))
            }
          } catch (error) {
            if (!cancelled) {
              setWindowErrorsById((previous) => ({ ...previous, [opportunity.id]: error.message || 'Failed to load window' }))
            }
          }
        }
      })

      await Promise.all(workers)
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [activeBatch, get, windowErrorsById, windowsById])

  const bounds = useMemo(() => {
    const available = activeBatch
      .map((opportunity) => getBoundsForWindow(windowsById[opportunity.id]))
      .filter(Boolean)

    if (available.length === 0) {
      return { min: -60, max: 360, revealExit: 0 }
    }

    return {
      min: Math.min(...available.map((bound) => bound.minOffset), -60),
      max: Math.max(...available.map((bound) => bound.maxOffset), 360),
      revealExit: Math.max(...available.map((bound) => bound.exitOffset), 0),
    }
  }, [activeBatch, windowsById])

  const maxRevealAllowed = bounds.max

  useEffect(() => {
    setRevealOffsetMin((previous) => clamp(previous, bounds.min, maxRevealAllowed))
  }, [bounds.min, maxRevealAllowed])

  const applyRevealEntry = useCallback(() => {
    setRevealMode(REVEAL_MODE_ENTRY)
    setRevealOffsetMin(clamp(ENTRY_REVEAL_OFFSET_MIN, bounds.min, maxRevealAllowed))
  }, [bounds.min, maxRevealAllowed])

  const applyRevealExit = useCallback(() => {
    setRevealMode(REVEAL_MODE_EXIT)
    setRevealOffsetMin(clamp(bounds.revealExit, bounds.min, maxRevealAllowed))
  }, [bounds.min, bounds.revealExit, maxRevealAllowed])

  const applyRevealExitPlusHours = useCallback(() => {
    const hours = Number(postExitHours)
    const safeHours = Number.isFinite(hours) ? Math.max(0, hours) : 0
    const plusOffsetMin = roundToStep(safeHours * 60)
    setRevealMode(REVEAL_MODE_EXIT_PLUS)
    setExitPlusHoursApplied(safeHours)
    setRevealOffsetMin(clamp(bounds.revealExit + plusOffsetMin, bounds.min, maxRevealAllowed))
  }, [bounds.min, bounds.revealExit, maxRevealAllowed, postExitHours])

  const columns = pickColumns(presentationMode)
  const totalPages = Math.ceil(filteredCandidates.length / batchSize)
  const currentPage = Math.floor(batchOffset / batchSize) + 1

  return {
    activeBatch,
    applyRevealEntry,
    applyRevealExit,
    applyRevealExitPlusHours,
    batchOffset,
    batchSize,
    bounds,
    columns,
    currentPage,
    dateFrom,
    dateTo,
    directionFilter,
    directionOptions,
    exitPlusHoursApplied,
    fetchCandidates,
    filteredCount: filteredCandidates.length,
    maxRevealAllowed,
    mode,
    oppsError,
    oppsLoading,
    postExitHours,
    presentationMode,
    randomSample,
    revealMode,
    revealOffsetMin,
    strategyFilter,
    strategyOptions,
    topPanelsCollapsed,
    totalPages,
    windowErrorsById,
    windowsById,
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
  }
}
