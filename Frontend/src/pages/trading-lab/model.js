// Presentation settings only. Market metrics and strategy scores come from MCAS.
export const VIEWS = [
  { id: 'command', label: 'Command' },
  { id: 'discovery', label: 'Discovery' },
  { id: 'investigation', label: 'Investigation' },
  { id: 'derivatives', label: 'Derivatives' },
  { id: 'equities', label: 'Equities' },
  { id: 'health', label: 'Feed status' },
]
export const DEFAULT_DISPLAYS = [
  {
    id: 'wall-a',
    name: 'Discovery wall',
    view: 'discovery',
    follow: true,
    symbol: '',
  },
  {
    id: 'wall-b',
    name: 'Investigation wall',
    view: 'investigation',
    follow: true,
    symbol: '',
  },
  {
    id: 'desk',
    name: 'Desk / command',
    view: 'command',
    follow: true,
    symbol: '',
  },
  {
    id: 'flow',
    name: 'Derivatives',
    view: 'derivatives',
    follow: true,
    symbol: '',
  },
]

export function number(value) {
  if (
    value === null ||
    value === undefined ||
    value === '' ||
    typeof value === 'boolean'
  )
    return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

export function timestamp(value) {
  if (value === null || value === undefined || value === '') return null
  const n = Number(value)
  const ms = Number.isFinite(n) ? (n < 1e11 ? n * 1000 : n) : Date.parse(value)
  return Number.isFinite(ms) && ms > 0 ? ms : null
}

export function ageLabel(value, now = Date.now()) {
  const ms = timestamp(value)
  if (ms === null) return 'Unknown age'
  const seconds = Math.max(0, Math.floor((now - ms) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

export function isStale(value, thresholdSeconds, now = Date.now()) {
  const ms = timestamp(value)
  return (
    ms === null ||
    now - ms > thresholdSeconds * 1000 ||
    ms > now + thresholdSeconds * 1000
  )
}

export function format(value, digits = 2) {
  const n = number(value)
  return n === null
    ? '—'
    : n.toLocaleString('en-IE', { maximumFractionDigits: digits })
}

export function price(value) {
  const n = number(value)
  return n === null
    ? '—'
    : n.toLocaleString('en-IE', { maximumSignificantDigits: 7 })
}

export function signed(value, suffix = '%', digits = 2) {
  const n = number(value)
  return n === null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(digits)}${suffix}`
}

export function tone(value) {
  const n = number(value)
  return n === null || n === 0 ? '' : n > 0 ? 'lab-positive' : 'lab-negative'
}

export function normalizeCandles(rows = []) {
  const unique = new Map()
  for (const row of rows) {
    // Only finalized collector bars. Never substitute an estimate for missing OHLCV.
    if (row.is_final !== true) continue
    const ms = timestamp(row.timestamp ?? row.candle_ts)
    const values = [row.open, row.high, row.low, row.close, row.volume].map(
      number
    )
    if (!ms || values.some((v) => v === null)) continue
    const [open, high, low, close, volume] = values
    if (
      low <= 0 ||
      high < Math.max(open, close) ||
      low > Math.min(open, close) ||
      volume < 0
    )
      continue
    const time = Math.floor(ms / 1000)
    unique.set(time, { time, open, high, low, close, volume })
  }
  return [...unique.values()].sort((a, b) => a.time - b.time)
}

export function validSelection(value, scope) {
  return (
    value?.scope === scope &&
    typeof value.symbol === 'string' &&
    /^[A-Z0-9._:/-]{1,40}$/.test(value.symbol) &&
    typeof value.tf === 'string' &&
    /^\d+[mhdw]$/.test(value.tf) &&
    Number.isFinite(value.sentAt)
  )
}

export function screenUrl(
  { view, follow, symbol, id, name },
  { demo, tf, scale, theme }
) {
  const query = new URLSearchParams({
    view,
    display: id,
    name,
    follow: follow ? '1' : '0',
    tf,
    scale,
    theme,
  })
  if (symbol) query.set('symbol', symbol)
  return `${demo ? '/lab-preview' : '/app/lab'}?${query}`
}

export function readStored(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback
  } catch {
    return fallback
  }
}

export function writeStored(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
    return true
  } catch {
    return false
  }
}

export function cleanDisplays(value) {
  if (!Array.isArray(value)) return DEFAULT_DISPLAYS
  const valid = value
    .filter(
      (d) =>
        typeof d?.id === 'string' &&
        typeof d.name === 'string' &&
        VIEWS.some((v) => v.id === d.view)
    )
    .map((d) => ({
      ...d,
      follow: d.follow !== false,
      symbol: String(d.symbol || '').slice(0, 40),
    }))
  return valid.length ? valid : DEFAULT_DISPLAYS
}
