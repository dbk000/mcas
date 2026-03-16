import { IntegrityStatus } from '../../utils/integrity'

export function integrityWarningLabel(status) {
  if (status === IntegrityStatus.WARN_SHAPE) return 'Data Schema Mismatch'
  if (status === IntegrityStatus.WARN_ORDER) return 'Timestamp Ordering Anomaly'
  if (status === IntegrityStatus.WARN_INCOMPLETE) return 'Incomplete Glass Box Records'
  return 'Data Inconsistency Detected'
}

export function sortOpportunities(opportunities, sortBy) {
  if (sortBy === 'score' || !sortBy) return opportunities

  const sorted = [...opportunities]

  switch (sortBy) {
    case 'newest':
      sorted.sort((a, b) => {
        const tsA = a.created_ts ? new Date(a.created_ts).getTime() : 0
        const tsB = b.created_ts ? new Date(b.created_ts).getTime() : 0
        return tsB - tsA
      })
      break
    case 'expiring':
      sorted.sort((a, b) => {
        const expiryA = a.expires_at || Infinity
        const expiryB = b.expires_at || Infinity
        return expiryA - expiryB
      })
      break
    default:
      break
  }

  return sorted
}
