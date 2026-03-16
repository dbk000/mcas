import { Square } from 'lucide-react'
import { Badge, StatusBadge } from '../../components/ui'
import { formatRelativeTime } from '../../utils/formatters'

export default function QueueItem({ item, onCancel }) {
  const typeColors = {
    SCAN: 'info',
    BACKTEST: 'warning',
    PAPER: 'success',
  }

  return (
    <div className="flex items-center justify-between rounded-lg border border-terminal-700/50 bg-terminal-800/50 p-3">
      <div className="flex items-center gap-3">
        <Badge variant={typeColors[item.run_type] || 'default'}>
          {item.run_type}
        </Badge>
        <div>
          <div className="font-medium text-terminal-100">{item.run_name}</div>
          <div className="font-mono text-xs text-terminal-500">
            {item.run_id.slice(0, 8)}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="text-right">
          <div className="text-sm">
            <StatusBadge status={item.status} />
          </div>
          <div className="text-xs text-terminal-500">
            {formatRelativeTime(item.queued_at)}
          </div>
        </div>

        {(item.status === 'QUEUED' || item.status === 'CLAIMED') && (
          <button
            onClick={() => onCancel(item.run_id)}
            className="rounded p-1.5 text-terminal-400 transition-colors hover:bg-red-400/10 hover:text-red-400"
            title="Cancel"
          >
            <Square className="size-4" />
          </button>
        )}
      </div>
    </div>
  )
}
