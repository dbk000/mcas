import { Link } from 'react-router-dom'
import {
  AlertCircle,
  AlertTriangle,
  ArrowLeft,
  BarChart2,
  Clock,
  Hash,
  XCircle,
} from 'lucide-react'
import { Badge, StatusBadge } from '../../components/ui'
import { formatRelativeTime } from '../../utils/formatters'
import {
  getRunTypeBadgeVariant,
  integrityWarningLabel,
} from './runDetailUtils'

export default function RunDetailHeader({
  run,
  runId,
  tradesIntegrity,
  hasIntegrityWarning,
  canCancel,
  onRequestCancel,
}) {
  return (
    <>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link to="/app/runs" className="btn btn-ghost btn-sm">
            <ArrowLeft className="size-4" />
          </Link>
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-terminal-100">{run.name}</h1>
              <Badge variant={getRunTypeBadgeVariant(run.run_type)}>
                {run.run_type}
              </Badge>
              <StatusBadge status={run.status} />
              {hasIntegrityWarning && (
                <div
                  className="inline-flex items-center gap-2 rounded-full border border-red-500/30 bg-red-500/10 px-3 py-1 text-xs uppercase tracking-[0.22em] text-red-400"
                  title="Data Inconsistency Detected"
                >
                  <AlertTriangle className="size-3.5" />
                  {integrityWarningLabel(tradesIntegrity)}
                </div>
              )}
            </div>
            <div className="mt-1 flex items-center gap-4 text-sm text-terminal-400">
              <span className="flex items-center gap-1 font-mono">
                <Hash className="size-3" />
                {run.fingerprint}
              </span>
              <span className="flex items-center gap-1">
                <Clock className="size-3" />
                {formatRelativeTime(run.created_at)}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          {run.run_type === 'SIMULATION' && run.status === 'SUCCESS' && (
            <Link to={`/app/runs/${runId}/simulation`} className="btn btn-primary btn-sm">
              <BarChart2 className="size-4" />
              View Results
            </Link>
          )}
          {canCancel && (
            <button onClick={onRequestCancel} className="btn btn-danger btn-sm">
              <XCircle className="size-4" />
              Cancel Run
            </button>
          )}
        </div>
      </div>

      {run.error && (
        <div className="flex items-start gap-3 rounded-lg border border-red-700/50 bg-red-900/20 p-4">
          <AlertCircle className="mt-0.5 size-5 shrink-0 text-red-400" />
          <div>
            <p className="text-sm font-medium text-red-200">Run failed</p>
            <p className="mt-1 text-sm text-red-300">{run.error}</p>
          </div>
        </div>
      )}
    </>
  )
}
