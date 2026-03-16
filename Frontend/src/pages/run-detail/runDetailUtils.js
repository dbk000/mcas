import { IntegrityStatus } from '../../utils/integrity'
import { formatNumber } from '../../utils/formatters'

export function integrityWarningLabel(status) {
  if (status === IntegrityStatus.WARN_SHAPE) return 'Data Schema Mismatch'
  if (status === IntegrityStatus.WARN_ORDER) return 'Timestamp Ordering Anomaly'
  if (status === IntegrityStatus.WARN_INCOMPLETE) return 'Incomplete Glass Box Records'
  return 'Data Inconsistency Detected'
}

export function getRunTypeBadgeVariant(runType) {
  if (runType === 'SCAN') return 'info'
  if (runType === 'BACKTEST') return 'warning'
  if (runType === 'SIMULATION') return 'success'
  return 'default'
}

export function formatAverageDailyPfValue(value) {
  if (value === Infinity) return '∞'
  if (!Number.isFinite(value)) return '—'
  return formatNumber(value, 2)
}

export function getTradeExitReasonVariant(reason) {
  if (reason === 'STOP_LOSS' || reason === 'FAIL_FAST') return 'error'
  if (reason === 'TP1' || reason === 'TP2') return 'success'
  if (reason === 'TRAIL_STOP') return 'warning'
  return 'default'
}
