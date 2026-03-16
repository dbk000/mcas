import { useEffect, useMemo } from 'react'
import { CheckCircle2, Shield, ShieldAlert, XCircle } from 'lucide-react'
import { getLogger } from '../../utils/logger'

const log = getLogger('glass_box_badge')

function isValidIsoTimestamp(value) {
  if (!value) return false
  return Number.isFinite(Date.parse(String(value)))
}

function buildGlassBoxModel(opportunity) {
  const lineage = opportunity?.sim_trace?.kline_lineage || null
  const hasMalformedTimestamps = !!lineage && (
    !isValidIsoTimestamp(lineage.decision_ts) ||
    !isValidIsoTimestamp(lineage.bar_start_ts)
  )

  if (!lineage) {
    return {
      status: 'na',
      label: 'Glass Box: N/A',
      Icon: Shield,
      className: 'border-terminal-600 bg-terminal-900/70 text-terminal-300',
      lineage: null,
      hasMalformedTimestamps: false,
      title: 'No lineage payload is attached to this opportunity.',
    }
  }

  if (lineage.causality_verified === true) {
    return {
      status: 'verified',
      label: 'Glass Box: Verified',
      Icon: CheckCircle2,
      className: 'border-green-700/40 bg-green-900/20 text-green-300',
      lineage,
      hasMalformedTimestamps,
      title: 'Lineage timing verified against the attached decision and bar timestamps.',
    }
  }

  if (lineage.causality_verified === false) {
    return {
      status: 'violation',
      label: 'Glass Box: Violation',
      Icon: XCircle,
      className: 'border-red-700/40 bg-red-900/20 text-red-300',
      lineage,
      hasMalformedTimestamps,
      title: 'Lineage indicates a timing violation in the attached proof data.',
    }
  }

  return {
    status: 'unknown',
    label: 'Glass Box: Unknown',
    Icon: ShieldAlert,
    className: 'border-amber-700/40 bg-amber-900/20 text-amber-300',
    lineage,
    hasMalformedTimestamps,
    title: 'Lineage is present, but causality resolution is still unknown.',
  }
}

export default function GlassBoxBadge({ opportunity, className = '' }) {
  const model = useMemo(() => buildGlassBoxModel(opportunity), [opportunity])

  useEffect(() => {
    if (!model.lineage || !model.hasMalformedTimestamps) return

    log.warn('Malformed Glass Box timestamps', {
      event: 'glass_box_badge_degraded_payload',
      opportunity_id: opportunity?.id ?? null,
      decision_ts: model.lineage.decision_ts ?? null,
      bar_start_ts: model.lineage.bar_start_ts ?? null,
    })
  }, [model, opportunity?.id])

  return (
    <span
      title={model.title}
      className={`inline-flex items-center gap-1.5 rounded border px-2.5 py-1 text-xs font-medium ${model.className} ${className}`}
    >
      <model.Icon className="size-3.5 shrink-0" />
      <span>{model.label}</span>
    </span>
  )
}
