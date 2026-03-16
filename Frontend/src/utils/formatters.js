import { format, formatDistanceToNow, parseISO } from 'date-fns'

// Number formatting
export function formatNumber(num, decimals = 2) {
  if (num === null || num === undefined) return '—'
  return Number(num).toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

export function formatPrice(num, decimals = null) {
  if (num === null || num === undefined) return '—'
  const n = Number(num)
  
  // Auto-detect decimals for crypto prices
  if (decimals === null) {
    if (n >= 1000) decimals = 2
    else if (n >= 1) decimals = 4
    else if (n >= 0.001) decimals = 6
    else decimals = 8
  }
  
  return formatNumber(n, decimals)
}

export function formatPercent(num, decimals = 2) {
  if (num === null || num === undefined) return '—'
  return `${formatNumber(num, decimals)}%`
}

export function formatPnL(num, decimals = 2) {
  if (num === null || num === undefined) return '—'
  const formatted = formatNumber(Math.abs(num), decimals)
  if (num > 0) return `+$${formatted}`
  if (num < 0) return `-$${formatted}`
  return `$${formatted}`
}

export function formatR(num, decimals = 2) {
  if (num === null || num === undefined) return '—'
  const formatted = formatNumber(Math.abs(num), decimals)
  if (num > 0) return `+${formatted}R`
  if (num < 0) return `-${formatted}R`
  return `${formatted}R`
}

export function formatDollar(num, decimals = 2) {
  if (num === null || num === undefined) return '—'
  return `$${formatNumber(num, decimals)}`
}

// Date formatting
export function formatDateTime(isoString) {
  if (!isoString) return '—'
  try {
    const date = typeof isoString === 'string' ? parseISO(isoString) : isoString
    return format(date, 'MMM d, yyyy HH:mm:ss')
  } catch {
    return '—'
  }
}

export function formatRelativeTime(isoString) {
  if (!isoString) return '—'
  try {
    const date = typeof isoString === 'string' ? parseISO(isoString) : isoString
    return formatDistanceToNow(date, { addSuffix: true })
  } catch {
    return '—'
  }
}

// Trade plan formatting
export function formatTradePlan(data) {
  const lines = [
    `📊 ${data.symbol || data.asset} | ${data.timeframe || 'N/A'} | ${data.setup_id || 'N/A'}`,
    `Direction: ${(data.direction || data.side || 'N/A').toUpperCase()}`,
    '',
    `Entry: ${formatPrice(data.entry_price || data.entry)}`,
    `Stop: ${formatPrice(data.stop_price || data.stop)}`,
    `TP1: ${formatPrice(data.tp1_price || data.tp1)}`,
    `TP2: ${formatPrice(data.tp2_price || data.tp2)}`,
    '',
    `R-Value: ${formatDollar(data.r_value)}`,
    data.score !== undefined ? `Score: ${formatNumber(data.score, 1)}` : null,
    '',
    data.reason ? `Reason: ${data.reason}` : null,
  ].filter(Boolean)
  
  return lines.join('\n')
}

export function copyToClipboard(text) {
  if (navigator.clipboard) {
    return navigator.clipboard.writeText(text)
  }
  // Fallback
  const textarea = document.createElement('textarea')
  textarea.value = text
  document.body.appendChild(textarea)
  textarea.select()
  document.execCommand('copy')
  document.body.removeChild(textarea)
  return Promise.resolve()
}

export function getPnLColor(pnl) {
  if (pnl === null || pnl === undefined) return 'text-terminal-300'
  if (pnl > 0) return 'text-signal-long'
  if (pnl < 0) return 'text-signal-short'
  return 'text-terminal-300'
}

/**
 * Check if a setup_id is an equities strategy.
 * Matches EQ_ prefix and also 'EQFP' which doesn't follow the prefix convention.
 */
export function isEquitiesStrategy(setupId) {
  if (!setupId) return false
  return setupId.startsWith('EQ_') || setupId === 'EQFP'
}
