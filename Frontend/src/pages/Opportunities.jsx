import { useState, useEffect, useMemo } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Target, Filter, Copy, Check, Clock, RefreshCw, PlayCircle, ChevronDown, ChevronRight, X, Plus, Download, LineChart, Square, CheckSquare, BarChart3 } from 'lucide-react'
import { useOpportunities, useStrategies, useOpportunityPriceWindow } from '../hooks/useData'
import { useToast } from '../context/ToastContext'
import { useApi } from '../context/ApiContext'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  DirectionBadge,
  Skeleton,
  EmptyState,
  RefreshControl,
  Input,
  Select,
  MultiSelect,
  Modal,
  DataRow
} from '../components/ui'
import {
  formatPrice,
  formatDollar,
  formatRelativeTime,
  formatDateTime,
  formatTradePlan,
  copyToClipboard,
  isEquitiesStrategy
} from '../utils/formatters'
import OpportunityCurveChart from '../components/opportunities/OpportunityCurveChart'

function ExpiryBadge({ expiresAt }) {
  const [label, setLabel] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!expiresAt) return

    function update() {
      const now = Date.now() / 1000
      const diff = expiresAt - now
      if (diff <= 0) {
        setLabel('Expired')
        setIsUrgent(true)
        return
      }
      const mins = Math.floor(diff / 60)
      if (mins < 1) {
        setLabel('<1m')
      } else {
        setLabel(`${mins}m`)
      }
      setIsUrgent(diff < 600) // < 10 minutes
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [expiresAt])

  if (!expiresAt) return null

  return (
    <span className={`inline-flex items-center gap-1 text-xs font-mono ${
      isUrgent ? 'text-red-400' : 'text-terminal-400'
    }`}>
      <Clock className="w-3 h-3" />
      {label}
    </span>
  )
}

function ActionByLabel({ actionByTs }) {
  const [label, setLabel] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!actionByTs) return

    function update() {
      const targetMs = typeof actionByTs === 'number'
        ? (actionByTs < 1e12 ? actionByTs * 1000 : actionByTs)
        : new Date(actionByTs).getTime()
      if (isNaN(targetMs)) { setLabel(''); return }

      const diffMs = targetMs - Date.now()
      if (diffMs <= 0) {
        setLabel('Deadline passed')
        setIsUrgent(true)
        return
      }
      const mins = Math.floor(diffMs / 60000)
      const hours = Math.floor(mins / 60)
      const remainMins = mins % 60
      if (hours > 0) {
        setLabel(`Act by: ${hours}h ${remainMins}m`)
      } else {
        setLabel(`Act by: ${mins}m`)
      }
      setIsUrgent(diffMs < 600000) // < 10 minutes
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [actionByTs])

  if (!actionByTs || !label) return null

  return (
    <span className={`inline-flex items-center gap-1 font-mono ${
      isUrgent ? 'text-red-400' : 'text-terminal-500'
    }`}>
      <Clock className="w-3 h-3" />
      {label}
    </span>
  )
}

function sortOpportunities(opps, sortBy) {
  // Canon §1.5D: Primary ranking is server-side. Backend returns
  // opportunities pre-ranked by score. Secondary sort options below
  // are display-only reordering for user convenience.
  if (sortBy === 'score' || !sortBy) {
    // Default: preserve server-provided ranking order (already sorted by score)
    return opps
  }
  const sorted = [...opps]
  switch (sortBy) {
    case 'newest':
      sorted.sort((a, b) => {
        const tsA = a.as_of_ts || 0
        const tsB = b.as_of_ts || 0
        return tsB - tsA
      })
      break
    case 'expiring':
      sorted.sort((a, b) => {
        const expA = a.expires_at || Infinity
        const expB = b.expires_at || Infinity
        return expA - expB
      })
      break
    default:
      break
  }
  return sorted
}

function CurveViewerModal({ opportunity, onClose }) {
  const { data, loading, error, refetch } = useOpportunityPriceWindow(
    opportunity?.id,
    { pre_s: 3600, post_s: 21600 },
    !!opportunity?.id
  )

  const markers = data?.markers || {}
  const entryIso = markers.entry_ts ? new Date(markers.entry_ts).toISOString() : null
  const exitIso = markers.exit_ts ? new Date(markers.exit_ts).toISOString() : null
  const hasGaps = data?.data_quality?.is_gappy

  return (
    <Modal
      isOpen={!!opportunity}
      onClose={onClose}
      size="xl"
      title={opportunity ? `Curve • ${opportunity.symbol} #${opportunity.id}` : 'Curve'}
    >
      {!opportunity ? null : loading && !data ? (
        <Skeleton className="h-[420px]" />
      ) : error ? (
        <div className="space-y-3">
          <p className="text-terminal-300">Failed to load curve: {error}</p>
          <button className="btn btn-secondary btn-sm" onClick={refetch}>
            <RefreshCw className="w-4 h-4" />
            Retry
          </button>
        </div>
      ) : (
        <div className="space-y-4">
          <OpportunityCurveChart
            candles={data?.candles || []}
            markers={markers}
            height={420}
          />

          {data?.data_quality?.empty && (
            <p className="text-sm text-terminal-400">No 5m candles found in this window.</p>
          )}
          {hasGaps && (
            <p className="text-sm text-amber-300">Data has gaps. Pattern interpretation may be distorted.</p>
          )}

          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 pt-2 border-t border-terminal-600">
            <DataRow label="Entry Time" value={formatDateTime(entryIso)} />
            <DataRow label="Exit Time" value={formatDateTime(exitIso)} />
            <DataRow label="Entry Px" value={formatPrice(markers.entry_px)} />
            <DataRow label="Stop Px" value={formatPrice(markers.stop_px)} />
          </div>
        </div>
      )}
    </Modal>
  )
}

export default function Opportunities() {
  const { addToast } = useToast()
  const { post, config } = useApi()
  const navigate = useNavigate()

  // Filters
  const [filters, setFilters] = useState({
    status: 'active',
    setup_id: '',
    min_score: 0,
    limit: 50,
  })
  const [sortBy, setSortBy] = useState('score')

  // Simulation modal state
  const [showSimModal, setShowSimModal] = useState(false)
  const [simulating, setSimulating] = useState(false)
  const [simConfig, setSimConfig] = useState({
    position_size_usd: 100,
    limit: 500,
    min_score: 0,
    setup_ids: [],
    timeframes: [],
    directions: [],
    from_date: '',
    to_date: '',
    exclude_dates: [],
    include_dates: [],
  })
  const [showDateFilters, setShowDateFilters] = useState(false)
  const [excludeDateInput, setExcludeDateInput] = useState('')
  const [includeDateInput, setIncludeDateInput] = useState('')
  const [curveOpp, setCurveOpp] = useState(null)
  const [selectedOppIds, setSelectedOppIds] = useState([])

  // Fetch strategy registry for dropdown options
  const { data: strategyData } = useStrategies()
  const setupOptions = [
    { value: '', label: 'All Setups' },
    ...(strategyData?.strategies || []).map(s => ({ value: s.id, label: s.label })),
  ]

  // Build API params — omit setup_id if empty
  const apiParams = { status: filters.status, min_score: filters.min_score, limit: filters.limit }
  if (filters.setup_id) apiParams.setup_id = filters.setup_id
  if (filters.status === 'expired') apiParams.include_outcome = true

  const { data, loading, lastUpdated, refetch, meta } = useOpportunities(apiParams)
  const rawOpportunities = data?.opportunities || []
  const opportunities = sortOpportunities(rawOpportunities, sortBy)
  const selectedOppSet = useMemo(() => new Set(selectedOppIds), [selectedOppIds])

  useEffect(() => {
    if (filters.status !== 'expired') {
      setSelectedOppIds([])
      return
    }
    const visibleIds = new Set(
      opportunities
        .map((opp) => (opp?.id != null ? String(opp.id) : null))
        .filter(Boolean)
    )
    setSelectedOppIds((prev) => prev.filter((id) => visibleIds.has(id)))
  }, [filters.status, opportunities])

  const handleCopyTradePlan = async (opp) => {
    const plan = formatTradePlan({
      symbol: opp.symbol,
      timeframe: opp.timeframe,
      setup_id: opp.setup_id,
      direction: opp.direction,
      entry_price: opp.payload?.entry_price,
      stop_price: opp.payload?.stop_price,
      tp1_price: opp.payload?.tp1_price,
      tp2_price: opp.payload?.tp2_price,
      r_value: opp.payload?.r_value,
      score: opp.score,
      reason: opp.reason,
    })

    await copyToClipboard(plan)
    addToast('Trade plan copied to clipboard', 'success')
  }

  const handleExportCsv = async () => {
    try {
      const params = new URLSearchParams({
        status: filters.status,
        min_score: String(filters.min_score),
        limit: '10000',
      })
      if (filters.setup_id) params.set('setup_id', filters.setup_id)

      const response = await fetch(`${config.baseUrl}/v1/opportunities/csv?${params}`, {
        headers: { 'X-API-Key': config.apiKey },
      })

      if (!response.ok) {
        const text = await response.text()
        let detail = text
        try { detail = JSON.parse(text).detail } catch {}
        addToast(`CSV export failed: ${detail}`, 'error')
        return
      }

      const blob = await response.blob()
      const disposition = response.headers.get('Content-Disposition')
      const filenameMatch = disposition?.match(/filename=(.+)/)
      const filename = filenameMatch ? filenameMatch[1] : 'opportunities.csv'

      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      addToast('CSV downloaded', 'success')
    } catch (err) {
      addToast(`CSV export failed: ${err.message}`, 'error')
    }
  }

  const handleSimulateExpired = async () => {
    setSimulating(true)
    try {
      const hasIncludeDates = simConfig.include_dates.length > 0
      const payload = {
        position_size_usd: simConfig.position_size_usd,
        limit: simConfig.limit,
        setup_ids: simConfig.setup_ids.length ? simConfig.setup_ids : (filters.setup_id ? [filters.setup_id] : null),
        min_score: simConfig.min_score,
        timeframes: simConfig.timeframes.length ? simConfig.timeframes : null,
        directions: simConfig.directions.length ? simConfig.directions : null,
        // Date filters: include_dates overrides from/to
        from_date: hasIncludeDates ? null : (simConfig.from_date || null),
        to_date: hasIncludeDates ? null : (simConfig.to_date || null),
        include_dates: hasIncludeDates ? simConfig.include_dates : null,
        exclude_dates: simConfig.exclude_dates.length ? simConfig.exclude_dates : null,
      }
      const response = await post('/v1/control/runs/simulation', payload)

      if (response.run_id) {
        addToast(`Simulation started: ${response.message}`, 'success')
        setShowSimModal(false)
        navigate(`/runs/${response.run_id}`)
      }
    } catch (err) {
      addToast(`Failed to start simulation: ${err.message}`, 'error')
    } finally {
      setSimulating(false)
    }
  }

  const toggleOppSelection = (oppId) => {
    if (oppId == null) return
    const key = String(oppId)
    setSelectedOppIds((prev) => (
      prev.includes(key) ? prev.filter((id) => id !== key) : [...prev, key]
    ))
  }

  const openSurveyMode = () => {
    const params = new URLSearchParams({ mode: 'selected' })
    if (selectedOppIds.length > 0) {
      params.set('ids', selectedOppIds.join(','))
    }
    navigate(`/opportunities/survey?${params.toString()}`)
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Opportunities</h1>
          <p className="text-sm text-terminal-400 mt-1">
            Ranked trade opportunities from latest scans
          </p>
        </div>
        <RefreshControl lastUpdated={lastUpdated} onRefresh={refetch} loading={loading} serverAsOf={meta?.as_of_ts} />
      </div>

      {/* Filters */}
      <Card>
        <CardContent className="flex flex-wrap items-center gap-4 py-3">
          <div className="flex items-center gap-2">
            <Filter className="w-4 h-4 text-terminal-400" />
            <span className="text-sm text-terminal-400">Filters:</span>
          </div>

          <Select
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value })}
            options={[
              { value: 'active', label: 'Active' },
              { value: 'expired', label: 'Expired' },
              { value: 'traded', label: 'Traded' },
            ]}
            className="w-32"
          />

          <Select
            value={filters.setup_id}
            onChange={(e) => setFilters({ ...filters, setup_id: e.target.value })}
            options={setupOptions}
            className="w-32"
          />

          <Select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            options={[
              { value: 'score', label: 'Score' },
              { value: 'newest', label: 'Newest' },
              { value: 'expiring', label: 'Expiring Soon' },
            ]}
            className="w-40"
          />

          <div className="flex items-center gap-2">
            <span className="text-sm text-terminal-400">Min Score:</span>
            <Input
              type="number"
              value={filters.min_score}
              onChange={(e) => setFilters({ ...filters, min_score: parseFloat(e.target.value) || 0 })}
              className="w-20"
              min={0}
              step={0.1}
            />
          </div>

          <div className="flex items-center gap-2">
            <span className="text-sm text-terminal-400">Limit:</span>
            <Select
              value={filters.limit}
              onChange={(e) => setFilters({ ...filters, limit: parseInt(e.target.value) })}
              options={[
                { value: 20, label: '20' },
                { value: 50, label: '50' },
                { value: 100, label: '100' },
              ]}
              className="w-20"
            />
          </div>

          <div className="ml-auto flex items-center gap-4">
            <span className="text-sm text-terminal-400">
              {opportunities.length} opportunities
            </span>
            {filters.status === 'expired' && (
              <span className="text-sm text-terminal-400">
                {selectedOppIds.length} selected
              </span>
            )}
            <button
              onClick={handleExportCsv}
              className="btn btn-ghost btn-sm flex items-center gap-2"
              title="Export opportunities as CSV"
            >
              <Download className="w-4 h-4" />
              Export CSV
            </button>
            {filters.status === 'expired' && (
              <button
                onClick={openSurveyMode}
                className="btn btn-secondary btn-sm flex items-center gap-2"
              >
                <BarChart3 className="w-4 h-4" />
                Survey Mode
              </button>
            )}
            {filters.status === 'expired' && (
              <button
                onClick={() => setShowSimModal(true)}
                className="btn btn-primary btn-sm flex items-center gap-2"
              >
                <PlayCircle className="w-4 h-4" />
                Simulate Expired
              </button>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Simulation Modal */}
      {showSimModal && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
          <Card className="w-full max-w-md">
            <CardHeader>
              <CardTitle>Simulate Expired Opportunities</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <p className="text-sm text-terminal-400">
                Walk through OHLCV data for each expired opportunity to see what would have happened.
              </p>

              <div className="space-y-3">
                <div>
                  <label className="text-sm text-terminal-400 block mb-1">
                    Position Size (USD)
                  </label>
                  <Input
                    type="number"
                    value={simConfig.position_size_usd}
                    onChange={(e) => setSimConfig({
                      ...simConfig,
                      position_size_usd: parseFloat(e.target.value) || 1000
                    })}
                    min={100}
                    max={100000}
                    step={100}
                  />
                </div>

                <div>
                  <label className="text-sm text-terminal-400 block mb-1">
                    Max Opportunities to Simulate
                  </label>
                  <Select
                    value={simConfig.limit}
                    onChange={(e) => setSimConfig({
                      ...simConfig,
                      limit: parseInt(e.target.value)
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
                  <label className="text-sm text-terminal-400 block mb-1">
                    Min Score (0 = all)
                  </label>
                  <Input
                    type="number"
                    value={simConfig.min_score}
                    onChange={(e) => setSimConfig({
                      ...simConfig,
                      min_score: parseFloat(e.target.value) || 0
                    })}
                    min={0}
                    max={1}
                    step={0.05}
                  />
                </div>

                <div>
                  <label className="text-sm text-terminal-400 block mb-1">
                    Setup
                  </label>
                  <MultiSelect
                    value={simConfig.setup_ids}
                    onChange={(vals) => setSimConfig({ ...simConfig, setup_ids: vals })}
                    options={(strategyData?.strategies || []).map(s => ({ value: s.id, label: s.id }))}
                    placeholder="All"
                  />
                </div>

                <div>
                  <label className="text-sm text-terminal-400 block mb-1">
                    Timeframe
                  </label>
                  <MultiSelect
                    value={simConfig.timeframes}
                    onChange={(vals) => setSimConfig({ ...simConfig, timeframes: vals })}
                    options={[
                      { value: '1m', label: '1m' },
                      { value: '5m', label: '5m' },
                      { value: '15m', label: '15m' },
                      { value: '1h', label: '1h' },
                      { value: '4h', label: '4h' },
                      { value: '1d', label: '1d' },
                    ]}
                    placeholder="All"
                  />
                </div>

                <div>
                  <label className="text-sm text-terminal-400 block mb-1">
                    Direction
                  </label>
                  <MultiSelect
                    value={simConfig.directions}
                    onChange={(vals) => setSimConfig({ ...simConfig, directions: vals })}
                    options={[
                      { value: 'long', label: 'Long' },
                      { value: 'short', label: 'Short' },
                    ]}
                    placeholder="All"
                  />
                </div>

                {/* Collapsible Date Filters */}
                <div className="border-t border-terminal-600 pt-3">
                  <button
                    type="button"
                    onClick={() => setShowDateFilters(!showDateFilters)}
                    className="flex items-center gap-1 text-sm text-terminal-400 hover:text-terminal-200 transition-colors"
                  >
                    {showDateFilters ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                    Date Filters
                    {(simConfig.from_date || simConfig.to_date || simConfig.include_dates.length > 0 || simConfig.exclude_dates.length > 0) && (
                      <Badge variant="info" className="ml-1">active</Badge>
                    )}
                  </button>

                  {showDateFilters && (
                    <div className="space-y-3 mt-3">
                      {/* From / To */}
                      <div className="grid grid-cols-2 gap-3">
                        <div>
                          <label className="text-sm text-terminal-400 block mb-1">From</label>
                          <Input
                            type="date"
                            value={simConfig.from_date}
                            onChange={(e) => setSimConfig({ ...simConfig, from_date: e.target.value })}
                            disabled={simConfig.include_dates.length > 0}
                          />
                        </div>
                        <div>
                          <label className="text-sm text-terminal-400 block mb-1">To</label>
                          <Input
                            type="date"
                            value={simConfig.to_date}
                            onChange={(e) => setSimConfig({ ...simConfig, to_date: e.target.value })}
                            disabled={simConfig.include_dates.length > 0}
                          />
                        </div>
                      </div>
                      {simConfig.include_dates.length > 0 && (
                        <p className="text-xs text-terminal-500 -mt-1">From/To disabled — overridden by Include Dates</p>
                      )}

                      {/* Exclude Dates */}
                      <div>
                        <label className="text-sm text-terminal-400 block mb-1">Exclude Dates</label>
                        <div className="flex gap-2">
                          <Input
                            type="date"
                            value={excludeDateInput}
                            onChange={(e) => setExcludeDateInput(e.target.value)}
                            className="flex-1"
                          />
                          <button
                            type="button"
                            onClick={() => {
                              if (excludeDateInput && !simConfig.exclude_dates.includes(excludeDateInput)) {
                                setSimConfig({
                                  ...simConfig,
                                  exclude_dates: [...simConfig.exclude_dates, excludeDateInput].sort()
                                })
                              }
                              setExcludeDateInput('')
                            }}
                            className="btn btn-ghost btn-sm"
                            disabled={!excludeDateInput}
                          >
                            <Plus className="w-4 h-4" />
                          </button>
                        </div>
                        {simConfig.exclude_dates.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {simConfig.exclude_dates.map(d => (
                              <span key={d} className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono bg-terminal-700 text-terminal-300">
                                {d}
                                <button
                                  type="button"
                                  onClick={() => setSimConfig({
                                    ...simConfig,
                                    exclude_dates: simConfig.exclude_dates.filter(x => x !== d)
                                  })}
                                  className="hover:text-red-400"
                                >
                                  <X className="w-3 h-3" />
                                </button>
                              </span>
                            ))}
                          </div>
                        )}
                      </div>

                      {/* Include Dates Only */}
                      <div>
                        <label className="text-sm text-terminal-400 block mb-1">
                          Include Only
                          <span className="text-terminal-500 ml-1">(overrides From/To)</span>
                        </label>
                        <div className="flex gap-2">
                          <Input
                            type="date"
                            value={includeDateInput}
                            onChange={(e) => setIncludeDateInput(e.target.value)}
                            className="flex-1"
                          />
                          <button
                            type="button"
                            onClick={() => {
                              if (includeDateInput && !simConfig.include_dates.includes(includeDateInput)) {
                                setSimConfig({
                                  ...simConfig,
                                  include_dates: [...simConfig.include_dates, includeDateInput].sort()
                                })
                              }
                              setIncludeDateInput('')
                            }}
                            className="btn btn-ghost btn-sm"
                            disabled={!includeDateInput}
                          >
                            <Plus className="w-4 h-4" />
                          </button>
                        </div>
                        {simConfig.include_dates.length > 0 && (
                          <div className="flex flex-wrap gap-1 mt-2">
                            {simConfig.include_dates.map(d => (
                              <span key={d} className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-mono bg-terminal-700 text-terminal-300">
                                {d}
                                <button
                                  type="button"
                                  onClick={() => setSimConfig({
                                    ...simConfig,
                                    include_dates: simConfig.include_dates.filter(x => x !== d)
                                  })}
                                  className="hover:text-red-400"
                                >
                                  <X className="w-3 h-3" />
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
                <button
                  onClick={() => setShowSimModal(false)}
                  className="btn btn-ghost flex-1"
                  disabled={simulating}
                >
                  Cancel
                </button>
                <button
                  onClick={handleSimulateExpired}
                  className="btn btn-primary flex-1"
                  disabled={simulating}
                >
                  {simulating ? 'Starting...' : 'Start Simulation'}
                </button>
              </div>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Opportunities list */}
      {loading && !data ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {[1, 2, 3, 4, 5, 6].map(i => (
            <Card key={i}>
              <CardContent className="space-y-3">
                <Skeleton variant="title" />
                <Skeleton />
                <Skeleton className="w-2/3" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : opportunities.length === 0 ? (
        <EmptyState
          icon={Target}
          title="No opportunities found"
          description="Try adjusting your filters or run a new scan"
        />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {opportunities.map((opp) => (
            <OpportunityCard
              key={opp.id || `${opp.setup_id}:${opp.symbol}:${opp.timeframe}`}
              opportunity={opp}
              onCopy={() => handleCopyTradePlan(opp)}
              onCurve={() => setCurveOpp(opp)}
              showCurve={filters.status === 'expired'}
              showOutcome={filters.status === 'expired'}
              isSelectable={filters.status === 'expired'}
              isSelected={selectedOppSet.has(String(opp.id))}
              onToggleSelect={() => toggleOppSelection(opp.id)}
            />
          ))}
        </div>
      )}

      <CurveViewerModal opportunity={curveOpp} onClose={() => setCurveOpp(null)} />
    </div>
  )
}

function OpportunityCard({
  opportunity,
  onCopy,
  onCurve,
  showCurve = false,
  showOutcome = false,
  isSelectable = false,
  isSelected = false,
  onToggleSelect,
}) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async (e) => {
    e.preventDefault()
    e.stopPropagation()
    await onCopy()
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleCurve = (e) => {
    e.preventDefault()
    e.stopPropagation()
    onCurve?.()
  }

  const handleToggleSelect = (e) => {
    e.preventDefault()
    e.stopPropagation()
    onToggleSelect?.()
  }

  const payload = opportunity.payload || {}
  const linkId = opportunity.id || `${opportunity.setup_id}:${opportunity.symbol}:${opportunity.timeframe}`
  const sinceTs = opportunity.since_ts || payload.since_ts || null
  const actionByTs = opportunity.action_by_ts || payload.action_by_ts || null
  const entryPx = Number(payload.entry_price ?? opportunity.entry_price)
  const exitPx = Number(payload.exit_price ?? opportunity.exit_price)
  const pnlNet = opportunity.outcome_pnl_net != null ? Number(opportunity.outcome_pnl_net) : null
  const rMult = opportunity.outcome_r_multiple != null ? Number(opportunity.outcome_r_multiple) : null
  const dir = String(opportunity.direction || opportunity.decision || payload.direction || '').toLowerCase()
  const canDerivePct = Number.isFinite(entryPx) && entryPx > 0 && Number.isFinite(exitPx)
  const derivedPnlPct = canDerivePct
    ? ((dir === 'short' ? (entryPx - exitPx) : (exitPx - entryPx)) / entryPx) * 100
    : null
  const pnlValue = pnlNet != null
    ? pnlNet
    : (derivedPnlPct != null ? derivedPnlPct : null)
  const pnlClass = pnlValue == null
    ? 'text-terminal-400'
    : (pnlValue >= 0 ? 'text-signal-long' : 'text-signal-short')

  return (
    <Card hover className="relative">
      <Link to={`/opportunities/${encodeURIComponent(linkId)}`} className="block">
        <CardContent className="space-y-3">
          {/* Header */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="font-mono font-bold text-lg text-terminal-100">
                {opportunity.symbol}
              </span>
              <Badge>{opportunity.timeframe}</Badge>
            </div>
            <div className="flex items-center gap-2">
              {isSelectable && (
                <button
                  onClick={handleToggleSelect}
                  className={`btn btn-ghost btn-xs ${isSelected ? 'text-amber-300' : 'text-terminal-400'}`}
                  title={isSelected ? 'Deselect from survey batch' : 'Select for survey batch'}
                >
                  {isSelected ? <CheckSquare className="w-4 h-4" /> : <Square className="w-4 h-4" />}
                </button>
              )}
              <DirectionBadge direction={opportunity.direction || opportunity.decision} />
            </div>
          </div>

          {/* Setup and score */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge variant="info">{opportunity.setup_id}</Badge>
              {opportunity.expires_at && (
                <ExpiryBadge expiresAt={opportunity.expires_at} />
              )}
            </div>
            <div className="text-right">
              <span className="text-xs text-terminal-500">Score</span>
              <p className="text-lg font-bold font-mono text-amber-400">
                {opportunity.score?.toFixed(1)}
              </p>
            </div>
          </div>

          {/* Price levels — equities vs crypto */}
          {payload.entry_price && (
            <div className="border-t border-terminal-600 pt-3">
              {isEquitiesStrategy(opportunity.setup_id) ? (
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <DirectionBadge direction={payload.direction} />
                    <Badge>{payload.horizon_days}d horizon</Badge>
                    {payload.exploratory && <Badge variant="warning">EXPLORATORY</Badge>}
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-sm">
                    <div>
                      <span className="text-terminal-500">Entry</span>
                      <p className="font-mono text-terminal-100">{formatPrice(payload.entry_price)}</p>
                    </div>
                    <div>
                      <span className="text-terminal-500">Expected</span>
                      <p className="font-mono text-signal-long">+{payload.expected_return_bps} bps</p>
                    </div>
                    <div>
                      <span className="text-terminal-500">Confidence</span>
                      <p className="font-mono text-terminal-100">{(payload.confidence * 100).toFixed(0)}%</p>
                    </div>
                  </div>
                  {payload.explain && (
                    <ul className="space-y-0.5 mt-1">
                      {payload.explain.map((item, i) => (
                        <li key={i} className="text-xs text-terminal-400">{item}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div>
                    <span className="text-terminal-500">Entry</span>
                    <p className="font-mono text-terminal-100">{formatPrice(payload.entry_price)}</p>
                  </div>
                  <div>
                    <span className="text-terminal-500">Stop</span>
                    <p className="font-mono text-signal-short">{formatPrice(payload.stop_price)}</p>
                  </div>
                  <div>
                    <span className="text-terminal-500">TP1</span>
                    <p className="font-mono text-signal-long">{formatPrice(payload.tp1_price)}</p>
                  </div>
                  <div>
                    <span className="text-terminal-500">R-Value</span>
                    <p className="font-mono text-terminal-100">{formatDollar(payload.r_value)}</p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Reason */}
          {opportunity.reason && (
            <p className="text-sm text-terminal-400 line-clamp-2 border-t border-terminal-600 pt-3">
              {opportunity.reason}
            </p>
          )}

          {/* Timing: since_ts / action_by_ts (Canon §1.5) */}
          {(sinceTs || actionByTs) && (
            <div className="flex items-center gap-3 text-xs text-terminal-500 border-t border-terminal-600 pt-3">
              {sinceTs && (
                <span title={formatDateTime(sinceTs)}>
                  Active {formatRelativeTime(sinceTs)}
                </span>
              )}
              {actionByTs && (
                <ActionByLabel actionByTs={actionByTs} />
              )}
            </div>
          )}

          {showOutcome && (
            <div className="border-t border-terminal-600 pt-3">
              <div className="grid grid-cols-3 gap-2 text-sm">
                <div>
                  <span className="text-terminal-500">Exit</span>
                  <p className="font-mono text-terminal-100">
                    {Number.isFinite(exitPx) ? formatPrice(exitPx) : '—'}
                  </p>
                </div>
                <div>
                  <span className="text-terminal-500">PnL</span>
                  <p className={`font-mono ${pnlClass}`}>
                    {pnlNet != null
                      ? formatDollar(pnlNet)
                      : (derivedPnlPct != null ? `${derivedPnlPct >= 0 ? '+' : ''}${derivedPnlPct.toFixed(2)}%` : '—')}
                  </p>
                </div>
                <div>
                  <span className="text-terminal-500">R-Mult</span>
                  <p className="font-mono text-terminal-100">
                    {rMult != null ? rMult.toFixed(2) : '—'}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Footer */}
          <div className="flex items-center justify-between pt-2 border-t border-terminal-600">
            <span className="text-xs text-terminal-500">
              {formatRelativeTime(opportunity.updated_ts)}
            </span>
            <div className="flex items-center gap-1">
              {showCurve && (
                <button
                  onClick={handleCurve}
                  className="btn btn-ghost btn-xs"
                  title="Open opportunity curve"
                >
                  <LineChart className="w-4 h-4" />
                  Curve
                </button>
              )}
              <button
                onClick={handleCopy}
                className="btn btn-ghost btn-xs"
                title="Copy trade plan"
              >
                {copied ? (
                  <Check className="w-4 h-4 text-green-400" />
                ) : (
                  <Copy className="w-4 h-4" />
                )}
              </button>
            </div>
          </div>
        </CardContent>
      </Link>
    </Card>
  )
}
