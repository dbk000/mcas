import { useParams, Link } from 'react-router-dom'
import { ArrowLeft, Copy, Check, BarChart3, Clock } from 'lucide-react'
import { useState, useEffect } from 'react'
import { useOpportunity, useLiveOpportunity } from '../hooks/useData'
import { useToast } from '../context/ToastContext'
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  Badge,
  DirectionBadge,
  StatusBadge,
  Skeleton,
  JsonView,
  DataRow
} from '../components/ui'
import {
  formatPrice,
  formatDollar,
  formatDateTime,
  formatRelativeTime,
  formatTradePlan,
  copyToClipboard
} from '../utils/formatters'

function parseCompositeId(id) {
  if (!id || !id.includes(':')) return null
  const parts = id.split(':')
  // Format: setup_id:symbol:tf (e.g. "S2:BTCUSDT:1m")
  if (parts.length >= 3) {
    return { setup_id: parts[0], symbol: parts[1], tf: parts.slice(2).join(':') }
  }
  return null
}

function ExpiryCountdown({ expiresAt }) {
  const [remaining, setRemaining] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!expiresAt) return

    function update() {
      const now = Date.now() / 1000
      const diff = expiresAt - now
      if (diff <= 0) {
        setRemaining('Expired')
        setIsUrgent(true)
        return
      }
      const mins = Math.floor(diff / 60)
      const secs = Math.floor(diff % 60)
      setRemaining(`${mins}m ${secs}s`)
      setIsUrgent(diff < 600) // < 10 minutes
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [expiresAt])

  if (!expiresAt) return null

  return (
    <span className={`font-mono ${isUrgent ? 'text-red-400' : 'text-terminal-300'}`}>
      {remaining}
    </span>
  )
}

function ActionByCountdown({ actionByTs }) {
  const [remaining, setRemaining] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!actionByTs) return

    function update() {
      const targetMs = typeof actionByTs === 'number'
        ? (actionByTs < 1e12 ? actionByTs * 1000 : actionByTs)
        : new Date(actionByTs).getTime()
      if (isNaN(targetMs)) { setRemaining(''); return }

      const diffMs = targetMs - Date.now()
      if (diffMs <= 0) {
        setRemaining('Deadline passed')
        setIsUrgent(true)
        return
      }
      const totalMins = Math.floor(diffMs / 60000)
      const hours = Math.floor(totalMins / 60)
      const mins = totalMins % 60
      const secs = Math.floor((diffMs % 60000) / 1000)
      if (hours > 0) {
        setRemaining(`${hours}h ${mins}m ${secs}s`)
      } else {
        setRemaining(`${mins}m ${secs}s`)
      }
      setIsUrgent(diffMs < 600000) // < 10 minutes
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [actionByTs])

  if (!actionByTs || !remaining) return null

  return (
    <span className={`font-mono ${isUrgent ? 'text-red-400' : 'text-terminal-300'}`}>
      {remaining}
    </span>
  )
}

export default function OpportunityDetail() {
  const { id } = useParams()
  // The route param may contain encoded colons from the URL
  const decodedId = decodeURIComponent(id)
  const { addToast } = useToast()
  const [copied, setCopied] = useState(false)

  const composite = parseCompositeId(decodedId)
  const isLive = !!composite

  // Both hooks are always called (React hooks rule), but only one fires a request
  const { data: liveOpp, loading: liveLoading, error: liveError } = useLiveOpportunity(
    composite?.setup_id, composite?.symbol, composite?.tf
  )
  const { data: pgOpp, loading: pgLoading, error: pgError } = useOpportunity(
    isLive ? null : decodedId
  )

  const opp = isLive ? liveOpp : pgOpp
  const loading = isLive ? liveLoading : pgLoading
  const error = isLive ? liveError : pgError

  const handleCopy = async () => {
    if (!opp) return

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
    setCopied(true)
    addToast('Trade plan copied to clipboard', 'success')
    setTimeout(() => setCopied(false), 2000)
  }

  if (loading) {
    return (
      <div className="space-y-6">
        <Skeleton variant="title" className="w-48" />
        <Card>
          <CardContent className="space-y-4">
            <Skeleton />
            <Skeleton />
            <Skeleton className="w-2/3" />
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error || !opp) {
    return (
      <div className="text-center py-12">
        <p className="text-terminal-400">Opportunity not found</p>
        <Link to="/opportunities" className="btn btn-secondary mt-4">
          <ArrowLeft className="w-4 h-4" />
          Back to Opportunities
        </Link>
      </div>
    )
  }

  const payload = opp.payload || {}
  const sinceTs = opp.since_ts || payload.since_ts || null
  const actionByTs = opp.action_by_ts || payload.action_by_ts || null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/opportunities" className="btn btn-ghost btn-sm">
            <ArrowLeft className="w-4 h-4" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-terminal-100 font-mono">{opp.symbol}</h1>
              <Badge>{opp.timeframe}</Badge>
              <Badge variant="info">{opp.setup_id}</Badge>
              <DirectionBadge direction={opp.direction} />
            </div>
            <p className="text-sm text-terminal-400 mt-1">
              {isLive ? 'Live Signal' : `Opportunity #${opp.id}`}
            </p>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <Link
            to={`/markets/${opp.symbol}`}
            className="btn btn-secondary btn-sm"
          >
            <BarChart3 className="w-4 h-4" />
            View Chart
          </Link>
          <button onClick={handleCopy} className="btn btn-primary btn-sm">
            {copied ? (
              <Check className="w-4 h-4" />
            ) : (
              <Copy className="w-4 h-4" />
            )}
            Copy Trade Plan
          </button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Main info */}
        <div className="lg:col-span-2 space-y-6">
          {/* Trade levels */}
          <Card>
            <CardHeader>
              <CardTitle>Trade Levels</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <LevelBox
                  label="Entry"
                  value={formatPrice(payload.entry_price)}
                  color="text-terminal-100"
                />
                <LevelBox
                  label="Stop Loss"
                  value={formatPrice(payload.stop_price)}
                  color="text-signal-short"
                />
                <LevelBox
                  label="Take Profit 1"
                  value={formatPrice(payload.tp1_price)}
                  color="text-signal-long"
                />
                <LevelBox
                  label="Take Profit 2"
                  value={formatPrice(payload.tp2_price)}
                  color="text-signal-long"
                />
              </div>

              <div className="mt-4 pt-4 border-t border-terminal-600 grid grid-cols-2 gap-4">
                <DataRow label="R-Value" value={formatDollar(payload.r_value)} />
                <DataRow label="Score" value={opp.score?.toFixed(2)} />
              </div>
            </CardContent>
          </Card>

          {/* Reason */}
          {opp.reason && (
            <Card>
              <CardHeader>
                <CardTitle>Signal Reason</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-terminal-200">{opp.reason}</p>
              </CardContent>
            </Card>
          )}

          {/* Full payload */}
          <Card>
            <CardHeader>
              <CardTitle>Full Payload</CardTitle>
            </CardHeader>
            <CardContent>
              <JsonView data={payload} expanded />
            </CardContent>
          </Card>
        </div>

        {/* Sidebar */}
        <div className="space-y-6">
          {/* Status */}
          <Card>
            <CardHeader>
              <CardTitle>Status</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <DataRow label="Status" value={
                isLive ? <Badge variant="success">Live</Badge> : <StatusBadge status={opp.status} />
              } />
              {isLive && opp.expires_at && (
                <DataRow label="Expires In" value={
                  <div className="flex items-center gap-1">
                    <Clock className="w-3 h-3 text-terminal-400" />
                    <ExpiryCountdown expiresAt={opp.expires_at} />
                  </div>
                } />
              )}
              {!isLive && <DataRow label="Decision" value={opp.decision || '\u2014'} />}
              {!isLive && <DataRow label="Signal ID" value={opp.signal_id || '\u2014'} />}
              <DataRow label="Source" value={opp.source === 'redis' ? 'Redis (Live)' : 'Database'} />
            </CardContent>
          </Card>

          {/* Timing: since_ts / action_by_ts (Canon §1.5) */}
          {(sinceTs || actionByTs) && (
            <Card>
              <CardHeader>
                <CardTitle>Timing</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {sinceTs && (
                  <DataRow label="Signal Active Since" value={
                    <div>
                      <span className="text-terminal-200">{formatDateTime(sinceTs)}</span>
                      <span className="text-terminal-500 text-xs ml-2">
                        ({formatRelativeTime(sinceTs)})
                      </span>
                    </div>
                  } />
                )}
                {actionByTs && (
                  <DataRow label="Act By" value={
                    <div className="flex items-center gap-2">
                      <Clock className="w-3 h-3 text-terminal-400" />
                      <div>
                        <span className="text-terminal-200">{formatDateTime(actionByTs)}</span>
                        <span className="text-xs ml-2">
                          <ActionByCountdown actionByTs={actionByTs} />
                        </span>
                      </div>
                    </div>
                  } />
                )}
              </CardContent>
            </Card>
          )}

          {/* Timestamps (Canon §1.5: candle_ts + as_of_ts) */}
          <Card>
            <CardHeader>
              <CardTitle>Timestamps</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {(opp.candle_ts || opp.bar_start_ts || payload.candle_ts || payload.bar_start_ts) && (
                <DataRow label="Candle (bar_start_ts)" value={formatDateTime(opp.candle_ts || opp.bar_start_ts || payload.candle_ts || payload.bar_start_ts)} />
              )}
              <DataRow label="Updated" value={formatDateTime(opp.updated_ts)} />
            </CardContent>
          </Card>

          {/* Quick actions */}
          <Card>
            <CardHeader>
              <CardTitle>Quick Actions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <Link
                to={`/markets/${opp.symbol}`}
                className="btn btn-secondary w-full"
              >
                <BarChart3 className="w-4 h-4" />
                Open Chart
              </Link>
              <button onClick={handleCopy} className="btn btn-secondary w-full">
                <Copy className="w-4 h-4" />
                Copy Trade Plan
              </button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  )
}

function LevelBox({ label, value, color }) {
  return (
    <div className="p-4 bg-terminal-900 rounded-lg border border-terminal-600">
      <p className="text-xs text-terminal-500 uppercase tracking-wider mb-1">{label}</p>
      <p className={`text-lg font-mono font-bold ${color}`}>{value}</p>
    </div>
  )
}
