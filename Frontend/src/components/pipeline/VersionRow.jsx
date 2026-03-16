import { ChevronDown, ChevronUp } from 'lucide-react'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

const STALENESS_BADGE = {
  potentially_stale: { label: 'Maybe stale', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/20' },
  confirmed_stale: { label: 'Stale', cls: 'bg-red-500/15 text-red-400 border-red-500/20' },
}

const HEALTH_BADGE = {
  degraded: { label: 'Degraded', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/20' },
  unhealthy: { label: 'Unhealthy', cls: 'bg-red-500/15 text-red-400 border-red-500/20' },
}

const STATUS_COLORS = {
  deployed: 'bg-green-500/20 text-green-400 border-green-500/30',
  promising: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  tested: 'bg-terminal-600 text-terminal-300 border-terminal-500',
  baseline: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  retired: 'bg-red-500/20 text-red-400 border-red-500/30',
}

const PARETO_BADGES = {
  active: { label: 'Frontier', cls: 'bg-green-500/15 text-green-400 border-green-500/20' },
  dominated: { label: 'Dominated', cls: 'bg-terminal-700 text-terminal-500 border-terminal-600' },
  historical: { label: 'Historical', cls: 'bg-terminal-800 text-terminal-600 border-terminal-700' },
}

export default function VersionRow({ version: v, expanded, onToggle, isSelectedForBio, onSelectForBio }) {
  const paretoBadge = PARETO_BADGES[v.pareto_status]
  const isDominated = v.pareto_status === 'dominated' || v.pareto_status === 'historical'

  return (
    <div className={`rounded border bg-terminal-900 ${isSelectedForBio ? 'border-amber-500/50' : 'border-terminal-700/50'} ${isDominated ? 'opacity-60' : ''}`}>
      <button
        onClick={onToggle}
        className="flex w-full items-center justify-between px-2.5 py-1.5 text-left transition-colors hover:bg-terminal-700/30"
      >
        <div className="flex min-w-0 items-center gap-2">
          <span className="shrink-0 font-mono text-xs text-terminal-200">v{v.version_number}</span>
          <span className={`shrink-0 rounded border px-1 py-0.5 text-[10px] ${STATUS_COLORS[v.status] || STATUS_COLORS.tested}`}>
            {v.status}
          </span>
          {paretoBadge && (
            <span className={`shrink-0 rounded border px-1 py-0.5 text-[9px] ${paretoBadge.cls}`}>
              {paretoBadge.label}
            </span>
          )}
          {v.agg_pf != null && (
            <span className={`shrink-0 font-mono text-xs ${v.agg_pf >= 1.0 ? 'text-green-400' : 'text-red-400'}`}>
              PF {v.agg_pf.toFixed(2)}
            </span>
          )}
          {PIPELINE_FEATURES.threeStateStaleness && STALENESS_BADGE[v.staleness_state] && (
            <span className={`shrink-0 rounded border px-1 py-0.5 text-[9px] ${STALENESS_BADGE[v.staleness_state].cls}`}>
              {STALENESS_BADGE[v.staleness_state].label}
            </span>
          )}
          {PIPELINE_FEATURES.threeStateStaleness && HEALTH_BADGE[v.health_status] && (
            <span className={`shrink-0 rounded border px-1 py-0.5 text-[9px] ${HEALTH_BADGE[v.health_status].cls}`}>
              {HEALTH_BADGE[v.health_status].label}
            </span>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-1">
          {v.created_at && (
            <span className="text-[10px] text-terminal-500">
              {new Date(v.created_at).toLocaleDateString()}
            </span>
          )}
          {expanded ? <ChevronUp className="size-3 text-terminal-500" /> : <ChevronDown className="size-3 text-terminal-500" />}
        </div>
      </button>
      {expanded && (
        <div className="space-y-1.5 border-t border-terminal-700/50 px-2.5 pb-2">
          {v.change_reason && (
            <p className="mt-1.5 text-[11px] text-terminal-300">{v.change_reason}</p>
          )}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            {v.net_pnl != null && (
              <div>
                <span className="text-terminal-500">PnL </span>
                <span className={v.net_pnl >= 0 ? 'font-mono text-green-400' : 'font-mono text-red-400'}>
                  ${v.net_pnl.toFixed(0)}
                </span>
              </div>
            )}
            {v.win_rate != null && (
              <div>
                <span className="text-terminal-500">Win </span>
                <span className="font-mono text-terminal-200">{(v.win_rate * 100).toFixed(1)}%</span>
              </div>
            )}
            {v.max_drawdown != null && (
              <div>
                <span className="text-terminal-500">DD </span>
                <span className="font-mono text-red-400">{(v.max_drawdown * 100).toFixed(1)}%</span>
              </div>
            )}
            {v.trade_count != null && (
              <div>
                <span className="text-terminal-500">Trades </span>
                <span className="font-mono text-terminal-200">{v.trade_count}</span>
              </div>
            )}
          </div>
          {v.parameters && Object.keys(v.parameters).length > 0 && (
            <div className="mt-1">
              <div className="mb-0.5 text-[10px] text-terminal-500">Parameters</div>
              <div className="max-h-24 overflow-y-auto rounded bg-terminal-800 p-1.5 font-mono text-[10px] text-terminal-300">
                {Object.entries(v.parameters).map(([k, val]) => (
                  <div key={k}>{k}: {JSON.stringify(val)}</div>
                ))}
              </div>
            </div>
          )}
          {v.superseded_by && (
            <div className="mt-1 text-[10px] text-terminal-500">
              Superseded by: <span className="font-mono text-amber-400">{v.superseded_by.slice(0, 8)}...</span>
            </div>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onSelectForBio() }}
            className="mt-1 text-[10px] text-amber-400 hover:text-amber-300"
          >
            View biography \u2192
          </button>
        </div>
      )}
    </div>
  )
}
