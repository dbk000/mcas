import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Check, CheckSquare, Copy, LineChart, Square } from 'lucide-react'
import { Badge, Card, CardContent, DirectionBadge } from '../../components/ui'
import {
  formatPrice,
  formatDollar,
  formatRelativeTime,
  formatDateTime,
  isEquitiesStrategy,
} from '../../utils/formatters'
import ExpiryBadge from './ExpiryBadge'
import ActionByLabel from './ActionByLabel'

export default function OpportunityCard({
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

  const handleCopy = async (event) => {
    event.preventDefault()
    event.stopPropagation()
    await onCopy()
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const handleCurve = (event) => {
    event.preventDefault()
    event.stopPropagation()
    onCurve?.()
  }

  const handleToggleSelect = (event) => {
    event.preventDefault()
    event.stopPropagation()
    onToggleSelect?.()
  }

  const payload = opportunity.payload || {}
  const linkId = opportunity.id || `${opportunity.setup_id}:${opportunity.symbol}:${opportunity.timeframe}`
  const sinceTs = opportunity.since_ts || payload.since_ts || null
  const actionByTs = opportunity.action_by_ts || payload.action_by_ts || null
  const entryPx = Number(payload.entry_price ?? opportunity.entry_price)
  const exitPx = Number(payload.exit_price ?? opportunity.exit_price)
  const pnlNet = opportunity.outcome_pnl_net != null ? Number(opportunity.outcome_pnl_net) : null
  const rMultiple = opportunity.outcome_r_multiple != null ? Number(opportunity.outcome_r_multiple) : null
  const direction = String(opportunity.direction || opportunity.decision || payload.direction || '').toLowerCase()
  const canDerivePct = Number.isFinite(entryPx) && entryPx > 0 && Number.isFinite(exitPx)
  const derivedPnlPct = canDerivePct
    ? ((direction === 'short' ? (entryPx - exitPx) : (exitPx - entryPx)) / entryPx) * 100
    : null
  const pnlValue = pnlNet != null ? pnlNet : derivedPnlPct
  const pnlClass = pnlValue == null
    ? 'text-terminal-400'
    : (pnlValue >= 0 ? 'text-signal-long' : 'text-signal-short')

  return (
    <Card hover className="op-row relative" data-operator-surface>
      <Link to={`/app/opportunities/${encodeURIComponent(linkId)}`} className="block">
        <CardContent className="space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="font-mono text-lg font-bold text-terminal-100">{opportunity.symbol}</span>
              <Badge>{opportunity.timeframe}</Badge>
            </div>
            <div className="flex items-center gap-2">
              {isSelectable && (
                <button
                  onClick={handleToggleSelect}
                  className={`btn btn-ghost btn-xs ${isSelected ? 'text-amber-300' : 'text-terminal-400'}`}
                  title={isSelected ? 'Deselect from survey batch' : 'Select for survey batch'}
                >
                  {isSelected ? <CheckSquare className="size-4" /> : <Square className="size-4" />}
                </button>
              )}
              <DirectionBadge direction={opportunity.direction || opportunity.decision} />
            </div>
          </div>

          {payload.early_exit_flagged && (
            <span className="inline-flex items-center rounded border border-amber-700/50 bg-amber-900/50 px-2 py-0.5 text-xs font-medium text-amber-300">
              Early Exit: {payload.early_exit_reason || 'review'}
            </span>
          )}

          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Badge variant="info">{opportunity.setup_id}</Badge>
              {opportunity.expires_at && <ExpiryBadge expiresAt={opportunity.expires_at} />}
            </div>
            <div className="text-right">
              <span className="text-xs text-terminal-500">Score</span>
              <p className="font-mono text-lg font-bold text-amber-400">
                {opportunity.score?.toFixed(1)}
              </p>
            </div>
          </div>

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
                    <ul className="mt-1 space-y-0.5">
                      {payload.explain.map((item, index) => (
                        <li key={index} className="text-xs text-terminal-400">{item}</li>
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

          {opportunity.reason && (
            <p className="line-clamp-2 border-t border-terminal-600 pt-3 text-sm text-terminal-400">
              {opportunity.reason}
            </p>
          )}

          {(sinceTs || actionByTs) && (
            <div className="flex items-center gap-3 border-t border-terminal-600 pt-3 text-xs text-terminal-500">
              {sinceTs && (
                <span title={formatDateTime(sinceTs)}>
                  Active {formatRelativeTime(sinceTs)}
                </span>
              )}
              {actionByTs && <ActionByLabel actionByTs={actionByTs} />}
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
                    {rMultiple != null ? rMultiple.toFixed(2) : '—'}
                  </p>
                </div>
              </div>
            </div>
          )}

          <div className="flex items-center justify-between border-t border-terminal-600 pt-2">
            <span className="text-xs text-terminal-500">{formatRelativeTime(opportunity.updated_ts)}</span>
            <div className="flex items-center gap-1">
              {showCurve && (
                <button onClick={handleCurve} className="btn btn-ghost btn-xs" title="Open opportunity curve">
                  <LineChart className="size-4" />
                  Curve
                </button>
              )}
              <button onClick={handleCopy} className="btn btn-ghost btn-xs" title="Copy trade plan">
                {copied ? <Check className="size-4 text-green-400" /> : <Copy className="size-4" />}
              </button>
            </div>
          </div>
        </CardContent>
      </Link>
    </Card>
  )
}
