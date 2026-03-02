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
    <div className={`bg-terminal-900 rounded border ${isSelectedForBio ? 'border-amber-500/50' : 'border-terminal-700/50'} ${isDominated ? 'opacity-60' : ''}`}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-2.5 py-1.5 text-left hover:bg-terminal-700/30 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-mono text-terminal-200 flex-shrink-0">v{v.version_number}</span>
          <span className={`text-[10px] px-1 py-0.5 rounded border flex-shrink-0 ${STATUS_COLORS[v.status] || STATUS_COLORS.tested}`}>
            {v.status}
          </span>
          {paretoBadge && (
            <span className={`text-[9px] px-1 py-0.5 rounded border flex-shrink-0 ${paretoBadge.cls}`}>
              {paretoBadge.label}
            </span>
          )}
          {v.agg_pf != null && (
            <span className={`text-xs font-mono flex-shrink-0 ${v.agg_pf >= 1.0 ? 'text-green-400' : 'text-red-400'}`}>
              PF {v.agg_pf.toFixed(2)}
            </span>
          )}
          {PIPELINE_FEATURES.threeStateStaleness && STALENESS_BADGE[v.staleness_state] && (
            <span className={`text-[9px] px-1 py-0.5 rounded border flex-shrink-0 ${STALENESS_BADGE[v.staleness_state].cls}`}>
              {STALENESS_BADGE[v.staleness_state].label}
            </span>
          )}
          {PIPELINE_FEATURES.threeStateStaleness && HEALTH_BADGE[v.health_status] && (
            <span className={`text-[9px] px-1 py-0.5 rounded border flex-shrink-0 ${HEALTH_BADGE[v.health_status].cls}`}>
              {HEALTH_BADGE[v.health_status].label}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {v.created_at && (
            <span className="text-[10px] text-terminal-500">
              {new Date(v.created_at).toLocaleDateString()}
            </span>
          )}
          {expanded ? <ChevronUp className="w-3 h-3 text-terminal-500" /> : <ChevronDown className="w-3 h-3 text-terminal-500" />}
        </div>
      </button>
      {expanded && (
        <div className="px-2.5 pb-2 space-y-1.5 border-t border-terminal-700/50">
          {v.change_reason && (
            <p className="text-[11px] text-terminal-300 mt-1.5">{v.change_reason}</p>
          )}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            {v.net_pnl != null && (
              <div>
                <span className="text-terminal-500">PnL </span>
                <span className={v.net_pnl >= 0 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>
                  ${v.net_pnl.toFixed(0)}
                </span>
              </div>
            )}
            {v.win_rate != null && (
              <div>
                <span className="text-terminal-500">Win </span>
                <span className="text-terminal-200 font-mono">{(v.win_rate * 100).toFixed(1)}%</span>
              </div>
            )}
            {v.max_drawdown != null && (
              <div>
                <span className="text-terminal-500">DD </span>
                <span className="text-red-400 font-mono">{(v.max_drawdown * 100).toFixed(1)}%</span>
              </div>
            )}
            {v.trade_count != null && (
              <div>
                <span className="text-terminal-500">Trades </span>
                <span className="text-terminal-200 font-mono">{v.trade_count}</span>
              </div>
            )}
          </div>
          {v.parameters && Object.keys(v.parameters).length > 0 && (
            <div className="mt-1">
              <div className="text-[10px] text-terminal-500 mb-0.5">Parameters</div>
              <div className="bg-terminal-800 rounded p-1.5 text-[10px] font-mono text-terminal-300 max-h-24 overflow-y-auto">
                {Object.entries(v.parameters).map(([k, val]) => (
                  <div key={k}>{k}: {JSON.stringify(val)}</div>
                ))}
              </div>
            </div>
          )}
          {v.superseded_by && (
            <div className="text-[10px] text-terminal-500 mt-1">
              Superseded by: <span className="text-amber-400 font-mono">{v.superseded_by.slice(0, 8)}...</span>
            </div>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onSelectForBio() }}
            className="text-[10px] text-amber-400 hover:text-amber-300 mt-1"
          >
            View biography \u2192
          </button>
        </div>
      )}
    </div>
  )
}
