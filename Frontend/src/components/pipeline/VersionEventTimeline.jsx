import { useMemo, useState } from 'react'
import { useFetch } from '../../hooks/useData'
import { defaultPipeline } from '../../utils/eventAdjusters'
import { buildEventTree, flattenTree } from '../../utils/eventTreeBuilder'
import { EVENT_TYPE_ICONS, extractEventsFromResponse } from './eventTimelineUtils'

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
  const [expandedTreeIds, setExpandedTreeIds] = useState(new Set())
  const shouldFetch = !propEvents && !!versionId
  const { data, loading } = useFetch(
    versionId ? `/v1/meta/version/${versionId}/events` : '',
    { limit: 50 },
    { skip: !shouldFetch, showError: false },
  )
  const events = useMemo(() => (
    propEvents || extractEventsFromResponse(data)
  ), [data, propEvents])

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
    return <div className="py-2 text-center text-[10px] text-terminal-500">Loading events...</div>
  }

  if (processedEvents.length === 0) {
    return <div className="py-2 text-center text-[10px] text-terminal-500">No events recorded</div>
  }

  // Tree view rendering
  if (viewMode === 'tree' && flatTreeItems) {
    return (
      <div className="relative pl-2">
        {flatTreeItems.map((item) => (
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
      <div className="absolute inset-y-2 left-[7px] w-0.5 bg-terminal-700" />

      {processedEvents.map((evt) => (
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
        <div className={`ml-2 flex items-center gap-1 py-1 ${indent > 0 ? `pl-${indent * 4}` : ''}`}>
          <div className={`h-px flex-1 ${gapWarning ? 'bg-amber-500/40' : 'bg-terminal-700/40'}`} />
          <span className={`px-1.5 text-[8px] ${gapWarning ? 'text-amber-500' : 'text-terminal-600'}`}>
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
          <div className="mb-0.5 text-[8px] uppercase tracking-wider text-terminal-600">orphan</div>
        )}

        {/* Timeline dot / tree expand */}
        <div className="absolute -left-4 top-0.5 z-10 flex size-4 items-center justify-center text-[10px]"
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
          <div className="flex flex-wrap items-center gap-1.5">
            {hasChildren && onToggleExpand && (
              <span className="text-[10px]">{icon}</span>
            )}
            <span className={`text-[10px] font-medium ${isCritical ? 'text-amber-300' : 'text-terminal-200'}`}>
              {evt.event_type.replace(/_/g, ' ')}
            </span>
            {evt.causal_type && evt.causal_type !== 'blocking' && (
              <span className="rounded bg-terminal-700 px-1 py-0 text-[8px] text-terminal-500">
                {evt.causal_type}
              </span>
            )}
            <span className={`rounded px-1 py-0.5 text-[9px] ${actorStyle}`}>
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
              <span className="rounded bg-amber-500/15 px-1 py-0 text-[8px] text-amber-400">
                critical
              </span>
            )}
          </div>

          {/* Detail text */}
          {detailText && (
            <p className="text-[10px] leading-snug text-terminal-400">{detailText}</p>
          )}

          {/* Commit SHA */}
          {detail.commit_sha && (
            <span className="font-mono text-[9px] text-terminal-500">
              commit: {detail.commit_sha.slice(0, 7)}
            </span>
          )}

          {/* Metrics summary */}
          {detail.agg_pf != null && (
            <span className={`font-mono text-[10px] ${detail.agg_pf >= 1 ? 'text-green-400' : 'text-red-400'}`}>
              aggPF = {detail.agg_pf.toFixed(2)}
              {detail.net_pnl != null && `, $${detail.net_pnl.toFixed(0)}`}
            </span>
          )}

          {/* Before/after state (Wave 2.8) */}
          {hasBeforeAfter && (
            <div className="flex items-center gap-1 font-mono text-[9px]">
              <span className="text-red-400">{JSON.stringify(detail.old_value)}</span>
              <span className="text-terminal-500">{'\u2192'}</span>
              <span className="text-green-400">{JSON.stringify(detail.new_value)}</span>
            </div>
          )}
          {paramChanges && Object.keys(paramChanges).length > 0 && (
            <div className="space-y-0.5">
              {Object.entries(paramChanges).map(([k, v]) => (
                <div key={k} className="flex items-center gap-1 font-mono text-[9px]">
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
              className="text-[9px] text-amber-400 transition-colors hover:text-amber-300"
            >
              {'\u2197'} caused by previous event
            </button>
          )}
        </div>
      </div>
    </>
  )
}
