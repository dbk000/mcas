import { useState, useEffect, useMemo } from 'react'
import { useApi } from '../../context/ApiContext'
import { EVENT_TYPE_ICONS } from './VersionEventTimeline'

const EVENT_TYPE_FILTERS = [
  'all', 'code_change', 'backtest', 'decision_made', 'pushed_to_live',
  'composition_included', 'hypothesis_formed', 'conversation_note',
]

export default function RecentActivity({ onNavigateToNode, onClose }) {
  const { get } = useApi()
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    const params = new URLSearchParams({ limit: '50' })
    if (filter !== 'all') params.set('event_type', filter)

    get(`/v1/meta/events/recent?${params}`).then(resp => {
      if (!cancelled) {
        setEvents(resp?.data?.events || resp?.events || [])
      }
    }).catch(() => {}).finally(() => {
      if (!cancelled) setLoading(false)
    })
    return () => { cancelled = true }
  }, [get, filter])

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
    <div className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 shadow-xl z-50 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-600">
        <h2 className="text-sm font-semibold text-terminal-100">Recent Activity</h2>
        <button
          onClick={onClose}
          className="text-terminal-400 hover:text-terminal-100 text-sm"
        >
          {'\u2715'}
        </button>
      </div>

      {/* Filter bar */}
      <div className="px-4 py-2 border-b border-terminal-700 flex flex-wrap gap-1">
        {EVENT_TYPE_FILTERS.map(t => (
          <button
            key={t}
            onClick={() => setFilter(t)}
            className={`text-[9px] px-1.5 py-0.5 rounded transition-colors ${
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
          <div className="text-xs text-terminal-500 text-center py-8">Loading activity...</div>
        ) : events.length === 0 ? (
          <div className="text-xs text-terminal-500 text-center py-8">No recent activity</div>
        ) : (
          Array.from(groupedEvents.entries()).map(([day, dayEvents]) => (
            <div key={day}>
              {/* Day header */}
              <div className="sticky top-0 bg-terminal-800/95 backdrop-blur px-4 py-1.5 border-b border-terminal-700/50">
                <span className="text-[10px] text-terminal-500 font-medium">{day}</span>
              </div>

              {dayEvents.map(evt => {
                const icon = EVENT_TYPE_ICONS[evt.event_type] || '\u{2022}'
                return (
                  <button
                    key={evt.event_id}
                    onClick={() => onNavigateToNode?.(evt.node_id)}
                    className="w-full text-left px-4 py-2 hover:bg-terminal-700/30 transition-colors border-b border-terminal-800"
                  >
                    <div className="flex items-center gap-2">
                      <span className="text-sm flex-shrink-0">{icon}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-1.5">
                          <span className="text-[10px] text-terminal-200 font-medium truncate">
                            {evt.event_type.replace(/_/g, ' ')}
                          </span>
                          <span className="text-[9px] text-terminal-500">
                            on {evt.node_name} v{evt.version_number}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 mt-0.5">
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
                          <p className="text-[9px] text-terminal-400 mt-0.5 truncate">
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
