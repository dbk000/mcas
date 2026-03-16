import { Link } from 'react-router-dom'
import { ArrowRight, Radar, ShieldCheck, AlertTriangle } from 'lucide-react'
import { useStrategies, useOpportunities } from '../hooks/useData'
import { useSubscription } from '../context/SubscriptionContext'
import { FRONTEND_ANALYTICS_EVENTS, emitAnalyticsEvent } from '../utils/analytics'
import { usePublicJson } from '../utils/publicJson'
import { auditIntegrity, IntegrityStatus } from '../utils/integrity'
import { StaleDataGhost } from '../components/StaleDataGhost'

function sourceLabel(sourceStatus) {
  if (sourceStatus === 'live') return 'Live feed'
  if (sourceStatus === 'degraded') return 'Live feed temporarily unavailable, showing authored preview set'
  return 'Authored preview set'
}

function integrityWarningLabel(status) {
  if (status === IntegrityStatus.WARN_SHAPE) return 'Data Schema Mismatch'
  if (status === IntegrityStatus.WARN_ORDER) return 'Timestamp Ordering Anomaly'
  if (status === IntegrityStatus.WARN_INCOMPLETE) return 'Incomplete Glass Box Records'
  return 'Data Inconsistency Detected'
}

function formatAge(minutes) {
  if (minutes == null) return '—'
  if (minutes < 60) return `${minutes}m`
  return `${(minutes / 60).toFixed(1)}h`
}

function causalityLabel(status) {
  if (status === 'VERIFIED') return 'Glass Box: Verified'
  if (status === 'VIOLATION') return 'Glass Box: Violation'
  if (status === 'UNKNOWN') return 'Glass Box: Unknown'
  return 'Glass Box: N/A'
}

function deriveSourceStatus({ demoData, liveStrategies, liveOpps, error }) {
  if (liveStrategies.length > 0 || liveOpps.length > 0) return 'live'
  if (error && demoData) return 'degraded'
  if (demoData) return 'placeholder'
  return 'degraded'
}

function buildLiveLeaderboard(strategies, opportunities) {
  const strategyMap = new Map((strategies || []).map((strategy) => [strategy.id, strategy]))
  const grouped = new Map()

  for (const opportunity of opportunities || []) {
    const key = opportunity.setup_id || 'UNKNOWN'
    if (!grouped.has(key)) {
      grouped.set(key, [])
    }
    grouped.get(key).push(opportunity)
  }

  return Array.from(grouped.entries())
    .map(([setupId, opps]) => {
      const sortedOpps = [...opps].sort((a, b) => (b.score || 0) - (a.score || 0))
      const topOpps = sortedOpps.slice(0, 5)
      const verifiedCount = opps.filter((opp) => opp?.sim_trace?.kline_lineage?.causality_verified === true).length
      const freshestSignalAgeMinutes = Math.min(...topOpps.map((opp) => {
        const ts = opp.created_ts ? Date.parse(opp.created_ts) : null
        if (!ts) return 9999
        return Math.max(0, Math.round((Date.now() - ts) / 60000))
      }))

      return {
        setup_id: setupId,
        label: strategyMap.get(setupId)?.label || 'Unknown strategy',
        status: strategyMap.get(setupId)?.status || 'active',
        rank: 0,
        live_count: opps.length,
        verified_count: verifiedCount,
        avg_score: opps.reduce((sum, opp) => sum + (opp.score || 0), 0) / Math.max(opps.length, 1),
        freshest_signal_age_minutes: freshestSignalAgeMinutes,
        urgency_band: freshestSignalAgeMinutes <= 5 ? 'very_high' : freshestSignalAgeMinutes <= 15 ? 'high' : freshestSignalAgeMinutes <= 45 ? 'medium' : 'low',
        dominant_regime: topOpps[0]?.payload?.regime || topOpps[0]?.regime || 'Live feed',
        top_opportunities: topOpps.map((opp, index) => ({
          id: opp.id || `${opp.setup_id}:${opp.symbol}:${opp.timeframe}`,
          symbol: opp.symbol,
          timeframe: opp.timeframe,
          direction: opp.direction,
          score: opp.score,
          rank: index + 1,
          age_minutes: freshestSignalAgeMinutes,
          action_window_minutes: opp.action_by_ts ? Math.max(0, Math.round((Date.parse(opp.action_by_ts) - Date.now()) / 60000)) : null,
          causality_status: opp?.sim_trace?.kline_lineage?.causality_verified === true
            ? 'VERIFIED'
            : opp?.sim_trace?.kline_lineage?.causality_verified === false
              ? 'VIOLATION'
              : opp?.sim_trace?.kline_lineage
                ? 'UNKNOWN'
                : 'N_A',
          thesis: opp.reason || opp.payload?.thesis || 'Live opportunity summary unavailable',
        })),
      }
    })
    .sort((a, b) => b.live_count - a.live_count || b.avg_score - a.avg_score)
    .map((item, index) => ({ ...item, rank: index + 1 }))
}

export default function StrategyDashboard() {
  const { tier } = useSubscription()
  const { data: strategiesData, error: strategyError } = useStrategies()
  const { data: oppData, error: oppError } = useOpportunities({ status: 'active', min_score: 0, limit: 200 })
  const { data: demoData } = usePublicJson('/curation/strategy-dashboard-demo.v1.json')

  const liveStrategies = strategiesData?.strategies || []
  const liveOpps = oppData?.opportunities || []
  
  // Integrity Audit (Workstream 2)
  const strategyIntegrity = auditIntegrity(liveStrategies, 'strategy')
  const oppIntegrity = auditIntegrity(liveOpps, 'opportunity')
  
  // FIXED: Logic bug - independent checks for each dataset
  const hasIntegrityIssue = (strategyIntegrity !== IntegrityStatus.OK && liveStrategies.length > 0) || 
                           (oppIntegrity !== IntegrityStatus.OK && liveOpps.length > 0);
  
  const worstIntegrity = [strategyIntegrity, oppIntegrity].sort((a, b) => {
    const priorities = { [IntegrityStatus.WARN_SHAPE]: 3, [IntegrityStatus.WARN_ORDER]: 2, [IntegrityStatus.WARN_INCOMPLETE]: 1, [IntegrityStatus.OK]: 0 }
    return (priorities[b] || 0) - (priorities[a] || 0)
  })[0]

  const liveLeaderboard = buildLiveLeaderboard(liveStrategies, liveOpps)
  const sourceStatus = deriveSourceStatus({
    demoData,
    liveStrategies,
    liveOpps,
    error: strategyError || oppError,
  })

  const displayData = liveLeaderboard.length > 0
    ? {
        leaderboard: liveLeaderboard,
        alerts_tape: liveOpps.slice(0, 3).map((opp) => `${opp.setup_id} ${opp.symbol} ${opp.timeframe} score ${opp.score?.toFixed(1) || '—'}`),
        market_state: {
          regime: 'Live opportunity feed',
          breadth_score: liveOpps.length,
          volatility_state: 'live',
          cross_asset_note: 'Grouped directly from the live opportunities endpoint.',
        },
        as_of_ts: new Date().toISOString(),
      }
    : demoData

  const leaderboard = displayData?.leaderboard || []

  return (
    <div className="space-y-6" data-operator-surface>
      <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/70 p-6 shadow-2xl shadow-black/20 sm:p-8">
        <div className="flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <div className="flex flex-wrap gap-2">
              <div className="inline-flex items-center gap-2 rounded-full border border-terminal-700 bg-terminal-900/80 px-3 py-1 text-xs uppercase tracking-[0.22em] text-terminal-400">
                <Radar className="size-3.5 text-amber-300" />
                {sourceLabel(sourceStatus)}
              </div>
              
              {hasIntegrityIssue && sourceStatus === 'live' && (
                <div className="inline-flex items-center gap-2 rounded-full border border-red-500/30 bg-red-500/10 px-3 py-1 text-xs uppercase tracking-[0.22em] text-red-400">
                  <AlertTriangle className="size-3.5" />
                  {integrityWarningLabel(worstIntegrity)}
                </div>
              )}
            </div>
            <h1 className="mt-5 text-4xl font-semibold tracking-tight text-terminal-100">Ranked strategy visibility.</h1>
            <p className="mt-4 max-w-3xl text-base leading-7 text-terminal-300">
              Prioritize the active setup mix by urgency, freshness, and Glass Box continuity instead of jumping across anonymous opportunity rows.
            </p>
          </div>
          <div className="rounded-3xl border border-terminal-700 bg-terminal-900/80 px-4 py-3 text-sm text-terminal-400">
            Updated {displayData?.as_of_ts || '—'}
          </div>
        </div>

        <div className="mt-6 grid gap-4 lg:grid-cols-[0.65fr_1.35fr]">
          <div className="rounded-3xl border border-terminal-700 bg-terminal-900/80 p-5">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Market State</div>
            <div className="mt-3 text-sm text-terminal-300">
              <div className="text-lg font-semibold text-terminal-100">{displayData?.market_state?.regime || '—'}</div>
              <div className="mt-2">Breadth score: {displayData?.market_state?.breadth_score ?? '—'}</div>
              <div className="mt-1">Volatility: {displayData?.market_state?.volatility_state || '—'}</div>
              <p className="mt-3 leading-6 text-terminal-400">{displayData?.market_state?.cross_asset_note || '—'}</p>
            </div>
          </div>

          <div className="rounded-3xl border border-terminal-700 bg-terminal-900/80 p-5">
            <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Alerts Tape</div>
            <div className="mt-3 grid gap-3 md:grid-cols-3">
              {(displayData?.alerts_tape || []).map((item) => (
                <div key={item} className="rounded-xl border border-terminal-700 bg-terminal-800/70 p-3 text-sm text-terminal-300">
                  {item}
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <StaleDataGhost lastUpdateTs={displayData?.as_of_ts ? Date.parse(displayData.as_of_ts) : null}>
        {leaderboard.length === 0 ? (
          <section className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-8 text-center">
            <h2 className="text-2xl font-semibold text-terminal-100">No strategy cards are available yet.</h2>
            <p className="mt-3 text-sm text-terminal-400">
              Once live opportunities arrive, this page groups them by strategy. Until then, continue into pricing or public winners.
            </p>
            <div className="mt-6 flex flex-col justify-center gap-3 sm:flex-row">
              <Link to="/pricing" className="btn btn-secondary btn-sm">Pricing</Link>
              <Link to="/winners" className="btn btn-primary btn-sm">
                Winners
                <ArrowRight className="size-4" />
              </Link>
            </div>
          </section>
        ) : (
          <section className="grid gap-4 xl:grid-cols-2">
            {leaderboard.map((strategy) => {
              const href = `/app/opportunities?setup_id=${encodeURIComponent(strategy.setup_id)}`
              return (
                <article key={strategy.setup_id} className="rounded-[2rem] border border-terminal-700 bg-terminal-800/60 p-6">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.22em] text-terminal-500">Rank #{strategy.rank}</div>
                      <h2 className="mt-2 text-2xl font-semibold text-terminal-100">{strategy.label}</h2>
                      <div className="mt-3 flex flex-wrap gap-2 text-xs uppercase tracking-[0.18em] text-terminal-400">
                        <span className="rounded-full border border-terminal-700 bg-terminal-900/70 px-3 py-1">{strategy.setup_id}</span>
                        <span className="rounded-full border border-terminal-700 bg-terminal-900/70 px-3 py-1">{strategy.urgency_band}</span>
                        <span className="rounded-full border border-terminal-700 bg-terminal-900/70 px-3 py-1">{strategy.dominant_regime}</span>
                      </div>
                    </div>
                    <Link
                      to={href}
                      className="btn btn-secondary btn-sm"
                      onClick={() => emitAnalyticsEvent(FRONTEND_ANALYTICS_EVENTS.strategyCardClicked, {
                        surface: 'strategy_dashboard',
                        placement: 'card_cta',
                        source_status: sourceStatus,
                        setup_id: strategy.setup_id,
                        strategy_label: strategy.label,
                        rank: strategy.rank,
                        live_count: strategy.live_count,
                        urgency_band: strategy.urgency_band,
                        tier,
                      })}
                    >
                      Open Strategy
                      <ArrowRight className="size-4" />
                    </Link>
                  </div>

                  <div className="mt-5 grid gap-3 sm:grid-cols-4">
                    <div className="rounded-xl border border-terminal-700 bg-terminal-900/80 p-3">
                      <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Live Count</div>
                      <div className="mt-1 text-xl font-semibold text-terminal-100">{strategy.live_count}</div>
                    </div>
                    <div className="rounded-xl border border-terminal-700 bg-terminal-900/80 p-3">
                      <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Verified</div>
                      <div className="mt-1 text-xl font-semibold text-terminal-100">{strategy.verified_count}</div>
                    </div>
                    <div className="rounded-xl border border-terminal-700 bg-terminal-900/80 p-3">
                      <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Avg Score</div>
                      <div className="mt-1 text-xl font-semibold text-terminal-100">{strategy.avg_score?.toFixed(1) || '—'}</div>
                    </div>
                    <div className="rounded-xl border border-terminal-700 bg-terminal-900/80 p-3">
                      <div className="text-xs uppercase tracking-[0.18em] text-terminal-500">Freshest</div>
                      <div className="mt-1 text-xl font-semibold text-terminal-100">{formatAge(strategy.freshest_signal_age_minutes)}</div>
                    </div>
                  </div>

                  <div className="mt-5 space-y-3">
                    {strategy.top_opportunities.map((opp) => (
                      <Link
                        key={opp.id}
                        to={`/app/opportunities/${encodeURIComponent(opp.id)}`}
                        className="op-row block rounded-[1.25rem] border border-terminal-700 bg-terminal-900/80 p-4 transition-colors hover:border-terminal-500 hover:bg-terminal-800/80"
                        onClick={() => emitAnalyticsEvent(FRONTEND_ANALYTICS_EVENTS.strategyCardClicked, {
                          surface: 'strategy_dashboard',
                          placement: 'opp_row',
                          source_status: sourceStatus,
                          setup_id: strategy.setup_id,
                          strategy_label: strategy.label,
                          rank: strategy.rank,
                          live_count: strategy.live_count,
                          urgency_band: strategy.urgency_band,
                          tier,
                        })}
                      >
                        <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                          <div>
                            <div className="text-lg font-semibold text-terminal-100">{opp.symbol} <span className="text-sm font-normal text-terminal-500">{opp.timeframe}</span></div>
                            <div className="mt-1 text-sm text-terminal-400">{opp.thesis}</div>
                          </div>
                          <div className="text-right">
                            <div className="text-base font-semibold text-terminal-100">{opp.score?.toFixed(1) || '—'}</div>
                            <div className="mt-1 text-xs uppercase tracking-[0.18em] text-terminal-500">{String(opp.direction).toUpperCase()} • {formatAge(opp.age_minutes)}</div>
                          </div>
                        </div>
                        <div className="mt-3 flex flex-wrap gap-2 text-xs uppercase tracking-[0.16em] text-terminal-400">
                          <span className="rounded-full border border-terminal-700 bg-terminal-800/80 px-3 py-1">{causalityLabel(opp.causality_status)}</span>
                          {opp.action_window_minutes != null && (
                            <span className="rounded-full border border-terminal-700 bg-terminal-800/80 px-3 py-1">Action window {opp.action_window_minutes}m</span>
                          )}
                        </div>
                      </Link>
                    ))}
                  </div>

                  {sourceStatus !== 'live' && (
                    <div className="mt-5 rounded-2xl border border-amber-500/25 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
                      This dashboard is currently rendering from the authored preview set so the surface stays demonstrable even without a live feed.
                    </div>
                  )}
                </article>
              )
            })}
          </section>
        )}
      </StaleDataGhost>

      {sourceStatus !== 'live' && (
        <section className="rounded-[2rem] border border-terminal-700 bg-gradient-to-r from-terminal-800 via-terminal-800 to-amber-900/20 p-6 sm:p-8">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <div>
              <div className="inline-flex items-center gap-2 rounded-full border border-terminal-700 bg-terminal-900/80 px-3 py-1 text-xs uppercase tracking-[0.22em] text-terminal-400">
                <ShieldCheck className="size-3.5 text-emerald-300" />
                Demo continuity
              </div>
              <h2 className="mt-3 text-2xl font-semibold text-terminal-100">Preview mode stays dense so the page contract is already real.</h2>
            </div>
            <div className="flex flex-col gap-3 sm:flex-row">
              <Link to="/pricing" className="btn btn-secondary btn-sm">Pricing</Link>
              <Link to="/winners" className="btn btn-primary btn-sm">
                Winners
                <ArrowRight className="size-4" />
              </Link>
            </div>
          </div>
        </section>
      )}
    </div>
  )
}
