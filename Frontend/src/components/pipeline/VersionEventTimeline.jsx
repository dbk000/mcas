import { useState, useEffect, useMemo } from 'react'
import { useApi } from '../../context/ApiContext'
import { defaultPipeline } from '../../utils/eventAdjusters'
import { buildEventTree, flattenTree } from '../../utils/eventTreeBuilder'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

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
  node_added: '\u{2795}',        // plus
  node_retired: '\u{1F5D1}',     // wastebasket
  edge_added: '\u{1F517}',       // link
  edge_removed: '\u{26D4}',      // no entry
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

function formatGap(ms) {
  if (ms == null) return null
  if (ms < 60000) return `${Math.round(ms / 1000)}s`
  if (ms < 3600000) return `${Math.round(ms / 60000)}m`
  if (ms < 86400000) return `${(ms / 3600000).toFixed(1)}h`
  return `${(ms / 86400000).toFixed(1)}d`
}

export default function VersionEventTimeline({
  versionId,
  events: propEvents,
  onCausalClick,
  viewMode = 'list', // 'list' | 'tree'
  temporalFilter = null, // ISO string — hide events after this timestamp
}) {
  const { get } = useApi()
  const [events, setEvents] = useState(propEvents || [])
  const [loading, setLoading] = useState(!propEvents)
  const [expandedTreeIds, setExpandedTreeIds] = useState(new Set())

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

  // Run adjuster pipeline on raw events
  const processedEvents = useMemo(() => {
    let adjusted = defaultPipeline(events)
    // Temporal filter: hide events after the cutoff
    if (temporalFilter) {
      const cutoff = new Date(temporalFilter).getTime()
      adjusted = adjusted.filter(e => {
        if (!e.created_at) return true
        return new Date(e.created_at).getTime() <= cutoff
      })
    }
    return adjusted
  }, [events, temporalFilter])

  // Build tree structure for tree view mode
  const treeData = useMemo(() => {
    if (viewMode !== 'tree') return null
    return buildEventTree(processedEvents)
  }, [processedEvents, viewMode])

  const flatTreeItems = useMemo(() => {
    if (!treeData) return null
    return flattenTree(treeData, 2, expandedTreeIds)
  }, [treeData, expandedTreeIds])

  const toggleTreeExpand = (eventId) => {
    setExpandedTreeIds(prev => {
      const next = new Set(prev)
      if (next.has(eventId)) next.delete(eventId)
      else next.add(eventId)
      return next
    })
  }

  if (loading) {
    return <div className="text-[10px] text-terminal-500 py-2 text-center">Loading events...</div>
  }

  if (processedEvents.length === 0) {
    return <div className="text-[10px] text-terminal-500 py-2 text-center">No events recorded</div>
  }

  // Tree view rendering
  if (viewMode === 'tree' && flatTreeItems) {
    return (
      <div className="relative pl-2">
        {flatTreeItems.map((item, i) => (
          <EventRow
            key={item.event.event_id}
            evt={item.event}
            onCausalClick={onCausalClick}
            indent={item.depth}
            hasChildren={item.hasChildren}
            childCount={item.childCount}
            isExpanded={item.isExpanded}
            onToggleExpand={() => toggleTreeExpand(item.event.event_id)}
            isOrphan={item.isOrphan && item.depth === 0}
          />
        ))}
      </div>
    )
  }

  // List view rendering (default)
  return (
    <div className="relative pl-4">
      {/* Vertical timeline line */}
      <div className="absolute left-[7px] top-2 bottom-2 w-0.5 bg-terminal-700" />

      {processedEvents.map((evt, i) => (
        <EventRow
          key={evt.event_id}
          evt={evt}
          onCausalClick={onCausalClick}
          showGap={evt._gapMs != null && evt._gapMs > 300000} // Show gaps > 5 minutes
          gapMs={evt._gapMs}
          gapWarning={evt._gapWarning}
          isCritical={evt._onCriticalPath}
        />
      ))}
    </div>
  )
}

function EventRow({
  evt,
  onCausalClick,
  indent = 0,
  hasChildren = false,
  childCount = 0,
  isExpanded = true,
  onToggleExpand,
  isOrphan = false,
  showGap = false,
  gapMs = null,
  gapWarning = false,
  isCritical = false,
}) {
  const icon = EVENT_TYPE_ICONS[evt.event_type] || '\u{2022}'
  const actorStyle = getActorStyle(evt.actor)
  const detail = evt.event_detail || {}
  const detailText = detail.summary || detail.change_reason || detail.message ||
    (typeof detail === 'string' ? detail : null)

  // Before/after state for parameter changes (Wave 2.8)
  const hasBeforeAfter = detail.old_value !== undefined && detail.new_value !== undefined
  const paramChanges = detail.changed_params || null

  return (
    <>
      {/* Time gap indicator (Wave 2.5) */}
      {showGap && (
        <div className={`flex items-center gap-1 py-1 ml-2 ${indent > 0 ? `pl-${indent * 4}` : ''}`}>
          <div className={`h-px flex-1 ${gapWarning ? 'bg-amber-500/40' : 'bg-terminal-700/40'}`} />
          <span className={`text-[8px] px-1.5 ${gapWarning ? 'text-amber-500' : 'text-terminal-600'}`}>
            {formatGap(gapMs)} gap
          </span>
          <div className={`h-px flex-1 ${gapWarning ? 'bg-amber-500/40' : 'bg-terminal-700/40'}`} />
        </div>
      )}

      <div
        className={`relative mb-3 last:mb-0 ${isCritical ? 'border-l-2 border-amber-500/40 pl-1' : ''}`}
        style={{ paddingLeft: indent > 0 ? `${indent * 16}px` : undefined }}
      >
        {/* Orphan marker */}
        {isOrphan && (
          <div className="text-[8px] text-terminal-600 mb-0.5 uppercase tracking-wider">orphan</div>
        )}

        {/* Timeline dot / tree expand */}
        <div className="absolute -left-4 top-0.5 w-4 h-4 flex items-center justify-center text-[10px] z-10"
          style={{ left: indent > 0 ? `${indent * 16 - 16}px` : undefined }}
        >
          {hasChildren && onToggleExpand ? (
            <button
              onClick={onToggleExpand}
              className="text-[10px] text-terminal-400 hover:text-terminal-200"
            >
              {isExpanded ? '\u25BC' : '\u25B6'}
            </button>
          ) : (
            <span>{icon}</span>
          )}
        </div>

        <div className="ml-2 space-y-0.5">
          {/* Header: type + actor + timestamp */}
          <div className="flex items-center gap-1.5 flex-wrap">
            {hasChildren && onToggleExpand && (
              <span className="text-[10px]">{icon}</span>
            )}
            <span className={`text-[10px] font-medium ${isCritical ? 'text-amber-300' : 'text-terminal-200'}`}>
              {evt.event_type.replace(/_/g, ' ')}
            </span>
            {evt.causal_type && evt.causal_type !== 'blocking' && (
              <span className="text-[8px] px-1 py-0 rounded bg-terminal-700 text-terminal-500">
                {evt.causal_type}
              </span>
            )}
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
            {isCritical && (
              <span className="text-[8px] px-1 py-0 rounded bg-amber-500/15 text-amber-400">
                critical
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

          {/* Before/after state (Wave 2.8) */}
          {hasBeforeAfter && (
            <div className="text-[9px] font-mono flex items-center gap-1">
              <span className="text-red-400">{JSON.stringify(detail.old_value)}</span>
              <span className="text-terminal-500">{'\u2192'}</span>
              <span className="text-green-400">{JSON.stringify(detail.new_value)}</span>
            </div>
          )}
          {paramChanges && Object.keys(paramChanges).length > 0 && (
            <div className="space-y-0.5">
              {Object.entries(paramChanges).map(([k, v]) => (
                <div key={k} className="text-[9px] font-mono flex items-center gap-1">
                  <span className="text-terminal-500">{k}:</span>
                  <span className="text-red-400">{JSON.stringify(v.old)}</span>
                  <span className="text-terminal-500">{'\u2192'}</span>
                  <span className="text-green-400">{JSON.stringify(v.new)}</span>
                </div>
              ))}
            </div>
          )}

          {/* Collapsed children count */}
          {hasChildren && !isExpanded && childCount > 0 && (
            <button
              onClick={onToggleExpand}
              className="text-[9px] text-terminal-500 hover:text-terminal-300"
            >
              +{childCount} more event{childCount > 1 ? 's' : ''}
            </button>
          )}

          {/* Causal link */}
          {evt.caused_by_event_id && !hasChildren && (
            <button
              onClick={() => onCausalClick?.(evt.caused_by_event_id)}
              className="text-[9px] text-amber-400 hover:text-amber-300 transition-colors"
            >
              {'\u2197'} caused by previous event
            </button>
          )}
        </div>
      </div>
    </>
  )
}

export { EVENT_TYPE_ICONS }
