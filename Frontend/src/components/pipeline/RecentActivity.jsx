import { useMemo, useState } from 'react'
import { useFetch } from '../../hooks/useData'
import { EVENT_TYPE_ICONS, extractEventsFromResponse } from './eventTimelineUtils'

const EVENT_TYPE_FILTERS = [
  'all', 'code_change', 'backtest', 'decision_made', 'pushed_to_live',
  'composition_included', 'hypothesis_formed', 'conversation_note',
]

export default function RecentActivity({ onNavigateToNode, onClose }) {
  const [filter, setFilter] = useState('all')
  const requestParams = useMemo(() => {
    const params = { limit: 50 }
    if (filter !== 'all') params.event_type = filter
    return params
  }, [filter])
  const { data, loading } = useFetch('/v1/meta/events/recent', requestParams, { showError: false })
  const events = useMemo(() => extractEventsFromResponse(data), [data])

  // Group events by day
  const groupedEvents = useMemo(() => {
    const groups = new Map()
    for (const evt of events) {
      const day = evt.created_at ? evt.created_at.split('T')[0] : 'Unknown'
      if (!groups.has(day)) groups.set(day, [])
      groups.get(day).push(evt)
    }
    return groups
  }, [events])

  return (
    <div className="fixed right-0 top-0 z-50 flex h-full w-[400px] flex-col overflow-hidden border-l border-terminal-600 bg-terminal-800 shadow-xl">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-terminal-600 px-4 py-3">
        <h2 className="text-sm font-semibold text-terminal-100">Recent Activity</h2>
        <button
          onClick={onClose}
          className="text-sm text-terminal-400 hover:text-terminal-100"
        >
          {'\u2715'}
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex flex-wrap gap-1 border-b border-terminal-700 px-4 py-2">
        {EVENT_TYPE_FILTERS.map(t => (
          <button
            key={t}
            onClick={() => setFilter(t)}
            className={`rounded px-1.5 py-0.5 text-[9px] transition-colors ${
              filter === t
                ? 'bg-amber-500/20 text-amber-400'
                : 'bg-terminal-900 text-terminal-500 hover:text-terminal-300'
            }`}
          >
            {t === 'all' ? 'All' : t.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      {/* Event list */}
      <div className="flex-1 overflow-y-auto">
        {loading ? (
          <div className="py-8 text-center text-xs text-terminal-500">Loading activity...</div>
        ) : events.length === 0 ? (
          <div className="py-8 text-center text-xs text-terminal-500">No recent activity</div>
        ) : (
          Array.from(groupedEvents.entries()).map(([day, dayEvents]) => (
            <div key={day}>
              {/* Day header */}
              <div className="sticky top-0 border-b border-terminal-700/50 bg-terminal-800/95 px-4 py-1.5 backdrop-blur">
                <span className="text-[10px] font-medium text-terminal-500">{day}</span>
              </div>

              {dayEvents.map(evt => {
                const icon = EVENT_TYPE_ICONS[evt.event_type] || '\u{2022}'
                return (
                  <button
                    key={evt.event_id}
                    onClick={() => onNavigateToNode?.(evt.node_id)}
                    className="w-full border-b border-terminal-800 px-4 py-2 text-left transition-colors hover:bg-terminal-700/30"
                  >
                    <div className="flex items-center gap-2">
                      <span className="shrink-0 text-sm">{icon}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="truncate text-[10px] font-medium text-terminal-200">
                            {evt.event_type.replace(/_/g, ' ')}
                          </span>
                          <span className="text-[9px] text-terminal-500">
                            on {evt.node_name} v{evt.version_number}
                          </span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-1.5">
                          <span className="text-[9px] text-terminal-500">
                            {evt.actor}
                          </span>
                          {evt.created_at && (
                            <span className="text-[9px] text-terminal-600">
                              {new Date(evt.created_at).toLocaleTimeString(undefined, {
                                hour: '2-digit', minute: '2-digit',
                              })}
                            </span>
                          )}
                        </div>
                        {evt.event_detail?.summary && (
                          <p className="mt-0.5 truncate text-[9px] text-terminal-400">
                            {evt.event_detail.summary}
                          </p>
                        )}
                      </div>
                    </div>
                  </button>
                )
              })}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
