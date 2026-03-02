/**
 * Event Adjuster Pipeline (Wave 2.4)
 *
 * Pure function chain that pre-processes version events before rendering.
 * Each adjuster is a function: events[] -> events[]
 * Composable: compose(adjusters)(rawEvents)
 *
 * Inspired by Jaeger's adjuster/standard.go but designed for our event model.
 */

/**
 * Remove duplicate events (same event_id appearing multiple times).
 * Keeps the first occurrence.
 */
export function deduplicateEvents(events) {
  const seen = new Set()
  return events.filter(evt => {
    if (seen.has(evt.event_id)) return false
    seen.add(evt.event_id)
    return true
  })
}

/**
 * Sort events chronologically by created_at (ascending).
 * Falls back to event_id string comparison for same-timestamp events.
 */
export function sortByTimestamp(events) {
  return [...events].sort((a, b) => {
    const ta = a.created_at ? new Date(a.created_at).getTime() : 0
    const tb = b.created_at ? new Date(b.created_at).getTime() : 0
    if (ta !== tb) return ta - tb
    return (a.event_id || '').localeCompare(b.event_id || '')
  })
}

/**
 * Normalize actor names to consistent display format.
 * E.g., 'claude_code_session_xyz' -> 'claude_code'
 */
export function normalizeActors(events) {
  return events.map(evt => {
    if (!evt.actor) return evt
    let actor = evt.actor
    if (actor.startsWith('claude_code')) actor = 'claude_code'
    if (actor.startsWith('auto_capture')) actor = 'auto_capture'
    if (actor.startsWith('genesis_import')) actor = 'genesis_import'
    if (actor.startsWith('sweep_runner')) actor = 'sweep_runner'
    return actor === evt.actor ? evt : { ...evt, actor }
  })
}

/**
 * Mark events on the critical path.
 *
 * Critical path = the causal chain from root events to the most recent
 * decision/outcome event. For our event model (a forest of causal chains),
 * we find the longest chain from any root to any terminal event.
 *
 * Unlike Jaeger's LFC (Last-Finishing-Child) algorithm which operates on
 * a single-root trace tree, our events form a forest. We find the longest
 * path through the caused_by_event_id graph.
 *
 * Adds `_onCriticalPath: boolean` to each event.
 */
export function markCriticalPath(events) {
  if (events.length === 0) return events

  // Build child map: parentId -> [childEvents]
  const childMap = new Map()
  const eventMap = new Map()
  const rootEvents = []

  for (const evt of events) {
    eventMap.set(evt.event_id, evt)
    if (!evt.caused_by_event_id) {
      rootEvents.push(evt)
    } else {
      const children = childMap.get(evt.caused_by_event_id) || []
      children.push(evt)
      childMap.set(evt.caused_by_event_id, children)
    }
  }

  // If no causal links, mark the most impactful events as critical
  // (decision_made, pushed_to_live, composition_included)
  const CRITICAL_EVENT_TYPES = new Set([
    'decision_made', 'pushed_to_live', 'composition_included',
    'backtest', 'code_change',
  ])

  if (rootEvents.length === events.length) {
    // No causal links — fall back to marking by event type
    return events.map(evt => ({
      ...evt,
      _onCriticalPath: CRITICAL_EVENT_TYPES.has(evt.event_type),
    }))
  }

  // Find longest path from each root using DFS
  let longestPath = []

  function dfs(eventId, path) {
    const children = childMap.get(eventId) || []
    if (children.length === 0) {
      // Terminal node
      if (path.length > longestPath.length) {
        longestPath = [...path]
      }
      return
    }
    for (const child of children) {
      path.push(child.event_id)
      dfs(child.event_id, path)
      path.pop()
    }
  }

  for (const root of rootEvents) {
    dfs(root.event_id, [root.event_id])
  }

  const criticalSet = new Set(longestPath)

  return events.map(evt => ({
    ...evt,
    _onCriticalPath: criticalSet.has(evt.event_id),
  }))
}

/**
 * Compute time gaps between consecutive events.
 * Adds `_gapMs: number | null` (milliseconds since previous event).
 * Adds `_gapWarning: boolean` if gap > 24 hours.
 */
export function computeTimeGaps(events) {
  return events.map((evt, i) => {
    if (i === 0 || !evt.created_at || !events[i - 1].created_at) {
      return { ...evt, _gapMs: null, _gapWarning: false }
    }
    const current = new Date(evt.created_at).getTime()
    const prev = new Date(events[i - 1].created_at).getTime()
    const gap = current - prev
    return {
      ...evt,
      _gapMs: gap,
      _gapWarning: gap > 24 * 60 * 60 * 1000, // > 24 hours
    }
  })
}

/**
 * Compose multiple adjusters into a single pipeline function.
 */
export function compose(...adjusters) {
  return (events) => adjusters.reduce((result, fn) => fn(result), events)
}

/**
 * Default adjuster pipeline: dedupe -> sort -> normalize -> critical path -> time gaps
 */
export const defaultPipeline = compose(
  deduplicateEvents,
  sortByTimestamp,
  normalizeActors,
  markCriticalPath,
  computeTimeGaps,
)
