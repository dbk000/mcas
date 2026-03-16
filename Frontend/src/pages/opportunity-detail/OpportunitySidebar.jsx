import { BarChart3, Clock, Copy } from 'lucide-react'
import { Link } from 'react-router-dom'
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  DataRow,
  StatusBadge,
} from '../../components/ui'
import GlassBoxBadge from '../../components/opportunities/GlassBoxBadge'
import {
  formatDateTime,
  formatRelativeTime,
} from '../../utils/formatters'
import {
  ExpiryCountdown,
  ActionByCountdown,
} from './countdowns'
import {
  formatWalkthroughTimestamp,
  getWalkthroughToneClasses,
} from './utils'

export default function OpportunitySidebar({
  actionByTs,
  handleCopy,
  isLive,
  liveWalkthrough,
  opportunity,
  payload,
  previewCases,
  sinceTs,
  walkthroughSourceLabel,
  walkthroughSteps,
  walkthroughSubheadline,
}) {
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <DataRow label="Status" value={isLive ? <Badge variant="success">Live</Badge> : <StatusBadge status={opportunity.status} />} />
          {isLive && opportunity.expires_at && (
            <DataRow label="Expires In" value={
              <div className="flex items-center gap-1">
                <Clock className="size-3 text-terminal-400" />
                <ExpiryCountdown expiresAt={opportunity.expires_at} />
              </div>
            } />
          )}
          {!isLive && <DataRow label="Decision" value={opportunity.decision || '—'} />}
          {!isLive && <DataRow label="Signal ID" value={opportunity.signal_id || '—'} />}
          <DataRow label="Source" value={opportunity.source === 'redis' ? 'Redis (Live)' : 'Database'} />
          {payload.early_exit_flagged && (
            <DataRow label="Early Exit" value={
              <span className="inline-flex items-center rounded border border-amber-700/50 bg-amber-900/50 px-2 py-0.5 text-xs font-medium text-amber-300">
                {payload.early_exit_reason || 'review'}
                {payload.early_exit_unrealized_pct != null && (
                  <span className="ml-1">({payload.early_exit_unrealized_pct}%)</span>
                )}
              </span>
            } />
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle>Glass Box</CardTitle>
          <Badge variant={liveWalkthrough.length > 0 ? 'success' : 'warning'}>
            {walkthroughSourceLabel}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-start justify-between gap-3">
            <div className="space-y-1">
              <p className="text-sm text-terminal-200">
                {liveWalkthrough.length > 0
                  ? 'Timing lineage is attached to this opportunity, so the proof strip below reflects the live payload.'
                  : walkthroughSubheadline}
              </p>
              <p className="text-xs text-terminal-500">
                Decision timing stays separate from strategy performance. Missing lineage keeps the badge honest instead of guessing.
              </p>
            </div>
            <GlassBoxBadge opportunity={opportunity} className="shrink-0" />
          </div>

          {walkthroughSteps.length > 0 && (
            <div className="space-y-2">
              {walkthroughSteps.map((step, index) => {
                const tone = getWalkthroughToneClasses(step.state)
                const stepTs = formatWalkthroughTimestamp(step.ts)
                return (
                  <div
                    key={`${step.phase || 'phase'}-${index}`}
                    className={`rounded-lg border px-3 py-2 ${tone.border} ${tone.bg}`}
                  >
                    <div className="flex items-start gap-3">
                      <span className={`mt-1 size-2.5 shrink-0 rounded-full ${tone.dot}`} />
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <p className={`text-sm font-medium capitalize ${tone.title}`}>
                            {step.label || String(step.phase || 'Phase').replace(/_/g, ' ')}
                          </p>
                          {stepTs && (
                            <span className="text-[11px] uppercase tracking-wide text-terminal-500">
                              {stepTs}
                            </span>
                          )}
                        </div>
                        {step.detail && (
                          <p className="mt-1 text-sm text-terminal-300">{step.detail}</p>
                        )}
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {liveWalkthrough.length === 0 && previewCases.length > 0 && (
            <div className="border-t border-terminal-600 pt-4">
              <p className="text-xs uppercase tracking-wider text-terminal-500">Authored preview states</p>
              <div className="mt-3 flex flex-wrap gap-2">
                {previewCases.map((preview) => (
                  <GlassBoxBadge
                    key={preview.case_id}
                    opportunity={preview.opportunity}
                  />
                ))}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

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
                  <span className="ml-2 text-xs text-terminal-500">
                    ({formatRelativeTime(sinceTs)})
                  </span>
                </div>
              } />
            )}
            {actionByTs && (
              <DataRow label="Act By" value={
                <div className="flex items-center gap-2">
                  <Clock className="size-3 text-terminal-400" />
                  <div>
                    <span className="text-terminal-200">{formatDateTime(actionByTs)}</span>
                    <span className="ml-2 text-xs">
                      <ActionByCountdown actionByTs={actionByTs} />
                    </span>
                  </div>
                </div>
              } />
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Timestamps</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {(opportunity.candle_ts || opportunity.bar_start_ts || payload.candle_ts || payload.bar_start_ts) && (
            <DataRow label="Candle (bar_start_ts)" value={formatDateTime(opportunity.candle_ts || opportunity.bar_start_ts || payload.candle_ts || payload.bar_start_ts)} />
          )}
          <DataRow label="Updated" value={formatDateTime(opportunity.updated_ts)} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Quick Actions</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <Link
            to={`/app/markets/${opportunity.symbol}`}
            className="btn btn-secondary w-full"
          >
            <BarChart3 className="size-4" />
            Open Chart
          </Link>
          <button onClick={handleCopy} className="btn btn-secondary w-full">
            <Copy className="size-4" />
            Copy Trade Plan
          </button>
        </CardContent>
      </Card>
    </div>
  )
}
