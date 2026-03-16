import { useState } from 'react'
import { FileText } from 'lucide-react'
import { useRunEvents } from '../../hooks/useData'
import {
  Card,
  CardContent,
  Badge,
  Skeleton,
  EmptyState,
  JsonView,
  Select,
} from '../../components/ui'
import { formatDateTime } from '../../utils/formatters'

export default function RunEventsTab({ runId }) {
  const [eventType, setEventType] = useState('')
  const { data, loading } = useRunEvents(runId, { event_type: eventType, limit: 100 })
  const events = data?.events || []

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <span className="text-sm text-terminal-400">Filter by type:</span>
        <Select
          value={eventType}
          onChange={(event) => setEventType(event.target.value)}
          options={[
            { value: '', label: 'All' },
            { value: 'INFO', label: 'Info' },
            { value: 'SIGNAL', label: 'Signal' },
            { value: 'TRADE', label: 'Trade' },
            { value: 'ERROR', label: 'Error' },
          ]}
          className="w-36"
        />
      </div>

      {loading ? (
        <div className="space-y-3">
          {[1, 2, 3, 4, 5].map((index) => (
            <Card key={index}>
              <CardContent>
                <Skeleton />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : events.length === 0 ? (
        <EmptyState
          icon={FileText}
          title="No events"
          description="This run hasn't logged any events yet"
        />
      ) : (
        <div className="space-y-2">
          {events.map((event) => (
            <Card key={event.id}>
              <CardContent className="py-3">
                <div className="flex items-start gap-3">
                  <Badge
                    variant={
                      event.event_type === 'ERROR' ? 'error' :
                      event.event_type === 'SIGNAL' ? 'warning' :
                      event.event_type === 'TRADE' ? 'success' :
                      'default'
                    }
                    className="mt-0.5"
                  >
                    {event.event_type}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <p className="text-terminal-200">{event.message}</p>
                    <p className="mt-1 text-xs text-terminal-500">{formatDateTime(event.ts)}</p>
                    {event.payload && Object.keys(event.payload).length > 0 && (
                      <div className="mt-2">
                        <JsonView data={event.payload} />
                      </div>
                    )}
                  </div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
