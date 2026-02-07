import { useState, useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { Target, Filter, Copy, Check, Clock, RefreshCw, PlayCircle } from 'lucide-react'
import { useOpportunities } from '../hooks/useData'
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
  Select
} from '../components/ui'
import {
  formatPrice,
  formatDollar,
  formatRelativeTime,
  formatDateTime,
  formatTradePlan,
  copyToClipboard
} from '../utils/formatters'

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

export default function Opportunities() {
  const { addToast } = useToast()
  const { apiCall } = useApi()
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
    position_size_usd: 1000,
    limit: 500,
  })

  // Build API params — omit setup_id if empty
  const apiParams = { status: filters.status, min_score: filters.min_score, limit: filters.limit }
  if (filters.setup_id) apiParams.setup_id = filters.setup_id

  const { data, loading, lastUpdated, refetch, meta } = useOpportunities(apiParams)
  const rawOpportunities = data?.opportunities || []
  const opportunities = sortOpportunities(rawOpportunities, sortBy)

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

  const handleSimulateExpired = async () => {
    setSimulating(true)
    try {
      const response = await apiCall('/control/runs/simulation', {
        method: 'POST',
        body: JSON.stringify({
          position_size_usd: simConfig.position_size_usd,
          limit: simConfig.limit,
          setup_ids: filters.setup_id ? [filters.setup_id] : null,
          min_score: filters.min_score,
        }),
      })

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
            options={[
              { value: '', label: 'All Setups' },
              { value: 'S1', label: 'S1' },
              { value: 'S2', label: 'S2' },
              { value: 'U1', label: 'U1' },
            ]}
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
                    ]}
                  />
                </div>

                <div className="text-xs text-terminal-500">
                  {filters.setup_id && `Setup: ${filters.setup_id}`}
                  {filters.min_score > 0 && ` | Min Score: ${filters.min_score}`}
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
            />
          ))}
        </div>
      )}
    </div>
  )
}

function OpportunityCard({ opportunity, onCopy }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async (e) => {
    e.preventDefault()
    e.stopPropagation()
    await onCopy()
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const payload = opportunity.payload || {}
  const linkId = opportunity.id || `${opportunity.setup_id}:${opportunity.symbol}:${opportunity.timeframe}`
  const sinceTs = opportunity.since_ts || payload.since_ts || null
  const actionByTs = opportunity.action_by_ts || payload.action_by_ts || null

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
            <DirectionBadge direction={opportunity.direction} />
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

          {/* Price levels */}
          {payload.entry_price && (
            <div className="grid grid-cols-2 gap-2 text-sm border-t border-terminal-600 pt-3">
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

          {/* Footer */}
          <div className="flex items-center justify-between pt-2 border-t border-terminal-600">
            <span className="text-xs text-terminal-500">
              {formatRelativeTime(opportunity.updated_ts)}
            </span>
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
        </CardContent>
      </Link>
    </Card>
  )
}
