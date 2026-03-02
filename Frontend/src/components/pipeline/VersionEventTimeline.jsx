import { useState, useEffect } from 'react'
import { useApi } from '../../context/ApiContext'

const EVENT_TYPE_ICONS = {
  code_change: '\u{1F527}',      // wrench
  backtest: '\u{1F4CA}',         // chart
  script_run: '\u{25B6}',        // play
  composition_included: '\u{1F3AF}', // target
  composition_removed: '\u{274C}',   // cross
  decision_made: '\u{1F4A1}',    // lightbulb
  hypothesis_formed: '\u{1F9EA}', // test tube
  conversation_note: '\u{1F4AC}', // speech bubble
  manual_observation: '\u{1F441}', // eye
  optimisation_started: '\u{2699}', // gear
  optimisation_referenced: '\u{1F517}', // link
  referenced_in_experiment: '\u{1F52C}', // microscope
  abandoned: '\u{1F6D1}',        // stop sign
  revived: '\u{267B}',           // recycle
  revalidated: '\u{2705}',       // check
  downstream_triggered: '\u{27A1}', // right arrow
  parameter_informed_by: '\u{1F4D0}', // triangular ruler
  pushed_to_live: '\u{1F680}',   // rocket
}

const ACTOR_BADGE_STYLES = {
  darren: 'bg-blue-500/20 text-blue-400',
  claude_code: 'bg-green-500/20 text-green-400',
  sweep_runner: 'bg-amber-500/20 text-amber-400',
  auto_capture: 'bg-terminal-600 text-terminal-400',
  genesis_import: 'bg-purple-500/20 text-purple-400',
  api: 'bg-terminal-600 text-terminal-400',
}

function getActorStyle(actor) {
  for (const [key, style] of Object.entries(ACTOR_BADGE_STYLES)) {
    if (actor?.toLowerCase().includes(key)) return style
  }
  return 'bg-terminal-600 text-terminal-400'
}

export default function VersionEventTimeline({ versionId, events: propEvents, onCausalClick }) {
  const { get } = useApi()
  const [events, setEvents] = useState(propEvents || [])
  const [loading, setLoading] = useState(!propEvents)

  useEffect(() => {
    if (propEvents) {
      setEvents(propEvents)
      return
    }
    if (!versionId) return
    let cancelled = false
    setLoading(true)
    get(`/v1/meta/version/${versionId}/events?limit=50`).then(resp => {
      if (!cancelled) {
        setEvents(resp?.data?.events || resp?.events || [])
      }
    }).catch(() => {}).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [versionId, propEvents, get])

  if (loading) {
    return <div className="text-[10px] text-terminal-500 py-2 text-center">Loading events...</div>
  }

  if (events.length === 0) {
    return <div className="text-[10px] text-terminal-500 py-2 text-center">No events recorded</div>
  }

  return (
    <div className="relative pl-4">
      {/* Vertical timeline line */}
      <div className="absolute left-[7px] top-2 bottom-2 w-0.5 bg-terminal-700" />

      {events.map((evt, i) => {
        const icon = EVENT_TYPE_ICONS[evt.event_type] || '\u{2022}'
        const actorStyle = getActorStyle(evt.actor)
        const detail = evt.event_detail || {}
        const detailText = detail.summary || detail.change_reason || detail.message ||
          (typeof detail === 'string' ? detail : null)

        return (
          <div key={evt.event_id} className="relative mb-3 last:mb-0">
            {/* Timeline dot */}
            <div className="absolute -left-4 top-0.5 w-4 h-4 flex items-center justify-center text-[10px] z-10">
              <span>{icon}</span>
            </div>

            <div className="ml-2 space-y-0.5">
              {/* Header: type + actor + timestamp */}
              <div className="flex items-center gap-1.5 flex-wrap">
                <span className="text-[10px] text-terminal-200 font-medium">
                  {evt.event_type.replace(/_/g, ' ')}
                </span>
                <span className={`text-[9px] px-1 py-0.5 rounded ${actorStyle}`}>
                  {evt.actor}
                </span>
                {evt.created_at && (
                  <span className="text-[9px] text-terminal-500">
                    {new Date(evt.created_at).toLocaleString(undefined, {
                      month: 'short', day: 'numeric',
                      hour: '2-digit', minute: '2-digit',
                    })}
                  </span>
                )}
              </div>

              {/* Detail text */}
              {detailText && (
                <p className="text-[10px] text-terminal-400 leading-snug">{detailText}</p>
              )}

              {/* Commit SHA */}
              {detail.commit_sha && (
                <span className="text-[9px] font-mono text-terminal-500">
                  commit: {detail.commit_sha.slice(0, 7)}
                </span>
              )}

              {/* Metrics summary */}
              {detail.agg_pf != null && (
                <span className={`text-[10px] font-mono ${detail.agg_pf >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                  aggPF = {detail.agg_pf.toFixed(2)}
                  {detail.net_pnl != null && `, $${detail.net_pnl.toFixed(0)}`}
                </span>
              )}

              {/* Causal link */}
              {evt.caused_by_event_id && (
                <button
                  onClick={() => onCausalClick?.(evt.caused_by_event_id)}
                  className="text-[9px] text-amber-400 hover:text-amber-300 transition-colors"
                >
                  \u2197 caused by previous event
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
