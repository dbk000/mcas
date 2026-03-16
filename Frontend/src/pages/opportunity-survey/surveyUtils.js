import { getEventColor } from '../../components/opportunities/chartEventPalette'

const REVEAL_STEP_MIN = 5
export const ENTRY_REVEAL_OFFSET_MIN = 5
export const REVEAL_MODE_OFFSET = 'offset'
export const REVEAL_MODE_ENTRY = 'entry'
export const REVEAL_MODE_EXIT = 'exit'
export const REVEAL_MODE_EXIT_PLUS = 'exit_plus'
export const PRE_S = 21600
export const POST_S = 21600

export const modeOptions = [
  { value: 'selected', label: 'Selected Opps (Mode A)' },
  { value: 'winners', label: 'All Winners (Mode B)' },
  { value: 'losers', label: 'All Losers (Mode C)' },
  { value: 'mixed', label: 'Mixed Hidden Labels (Mode D)' },
]

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

export function roundToStep(value, step = REVEAL_STEP_MIN) {
  return Math.round(value / step) * step
}

export function parseIds(raw) {
  if (!raw) {
    return []
  }

  const ids = raw
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => Number(part))
    .filter((value) => Number.isFinite(value))

  return Array.from(new Set(ids))
}

export function getRowRevealTs(windowData, revealOffsetMin) {
  const entryTs = windowData?.markers?.entry_ts
  const tStart = windowData?.t_start
  const tEnd = windowData?.t_end
  if (!entryTs || !tStart || !tEnd) {
    return null
  }

  const revealTs = entryTs + (revealOffsetMin * 60 * 1000)
  return clamp(revealTs, tStart, tEnd)
}

function getRowExitOffsetMin(windowData) {
  const entryTs = Number(windowData?.markers?.entry_ts)
  const exitTs = Number(windowData?.markers?.exit_ts)
  if (!Number.isFinite(entryTs) || !Number.isFinite(exitTs)) {
    return 0
  }

  return roundToStep((exitTs - entryTs) / 60000)
}

export function resolveRowRevealOffsetMin(windowData, revealMode, revealOffsetMin, exitPlusHours) {
  if (revealMode === REVEAL_MODE_ENTRY) {
    return ENTRY_REVEAL_OFFSET_MIN
  }
  if (revealMode === REVEAL_MODE_EXIT) {
    return getRowExitOffsetMin(windowData)
  }
  if (revealMode === REVEAL_MODE_EXIT_PLUS) {
    const hours = Number(exitPlusHours)
    const safeHours = Number.isFinite(hours) ? Math.max(0, hours) : 0
    return getRowExitOffsetMin(windowData) + roundToStep(safeHours * 60)
  }
  return revealOffsetMin
}

export function getBoundsForWindow(windowData) {
  const entryTs = windowData?.markers?.entry_ts
  const exitTs = windowData?.markers?.exit_ts
  const tStart = windowData?.t_start
  const tEnd = windowData?.t_end
  if (!entryTs || !tStart || !tEnd) {
    return null
  }

  const minOffset = roundToStep((tStart - entryTs) / 60000)
  const maxOffset = roundToStep((tEnd - entryTs) / 60000)
  const exitOffset = exitTs ? roundToStep((exitTs - entryTs) / 60000) : 0
  return { minOffset, maxOffset, exitOffset }
}

export function pickColumns(presentationMode) {
  return presentationMode ? 2 : 1
}

function isValidEventTs(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric > 0
}

function isValidEventPrice(value) {
  const numeric = Number(value)
  return Number.isFinite(numeric) && numeric > 0
}

export function formatEventTs(ts, includeDate = false) {
  if (!isValidEventTs(ts)) {
    return '—'
  }

  const date = new Date(Number(ts))
  const hh = String(date.getUTCHours()).padStart(2, '0')
  const mm = String(date.getUTCMinutes()).padStart(2, '0')
  if (!includeDate) {
    return `${hh}:${mm}`
  }

  const dd = String(date.getUTCDate()).padStart(2, '0')
  const mo = String(date.getUTCMonth() + 1).padStart(2, '0')
  const yy = String(date.getUTCFullYear()).slice(-2)
  return `${dd}/${mo}/${yy} ${hh}:${mm}`
}

export function withAlpha(hexColor, alphaHex = 'cc') {
  if (typeof hexColor !== 'string') {
    return '#94a3b8'
  }
  if (/^#[0-9a-fA-F]{6}$/.test(hexColor)) {
    return `${hexColor}${alphaHex}`
  }
  return hexColor
}

function toEpochMs(value) {
  const numeric = Number(value)
  if (Number.isFinite(numeric) && numeric > 0) {
    return numeric > 1e12 ? numeric : numeric * 1000
  }

  const parsed = Date.parse(String(value || ''))
  return Number.isFinite(parsed) ? parsed : null
}

export function resolveOppTsMs(opportunity) {
  return (
    toEpochMs(opportunity?.entry_ts) ??
    toEpochMs(opportunity?.payload?.entry_ts) ??
    toEpochMs(opportunity?.created_ts) ??
    toEpochMs(opportunity?.updated_ts)
  )
}

export function resolveOppStrategy(opportunity) {
  const raw = String(opportunity?.setup_id ?? opportunity?.payload?.setup_id ?? '').trim()
  return raw || '—'
}

export function resolveOppDirection(opportunity) {
  const raw = String(opportunity?.direction ?? opportunity?.decision ?? '').toLowerCase()
  if (raw.includes('long') || raw.includes('buy')) {
    return 'long'
  }
  if (raw.includes('short') || raw.includes('sell')) {
    return 'short'
  }
  return 'unknown'
}

export function toDateBoundaryMs(rawDate, endOfDay = false) {
  if (!rawDate) {
    return null
  }

  const timeSuffix = endOfDay ? 'T23:59:59.999Z' : 'T00:00:00.000Z'
  const parsed = Date.parse(`${rawDate}${timeSuffix}`)
  return Number.isFinite(parsed) ? parsed : null
}

function extractTsFromExitPoints(exitPoints, token) {
  if (!Array.isArray(exitPoints)) {
    return null
  }

  const tokenLower = String(token || '').toLowerCase()
  for (const point of exitPoints) {
    const label = String(point?.label || '').toLowerCase()
    const ts = Number(point?.ts)
    if (!isValidEventTs(ts)) {
      continue
    }
    if (label.includes(tokenLower)) {
      return ts
    }
  }

  return null
}

export function buildTradeEventRows(markers) {
  const exitPoints = Array.isArray(markers?.exit_points) ? markers.exit_points : []
  const tp = Array.isArray(markers?.tp_px) ? markers.tp_px : []
  const tp1Ts = extractTsFromExitPoints(exitPoints, 'tp1')
  const tp2Ts = extractTsFromExitPoints(exitPoints, 'tp2')
  const stopTs = extractTsFromExitPoints(exitPoints, 'stop') ?? extractTsFromExitPoints(exitPoints, 'sl')
  const finalExitTs = isValidEventTs(markers?.exit_ts) ? Number(markers.exit_ts) : null

  return [
    {
      key: 'entry',
      label: 'ENTRY',
      price: isValidEventPrice(markers?.entry_px) ? Number(markers.entry_px) : null,
      ts: isValidEventTs(markers?.entry_ts) ? Number(markers.entry_ts) : null,
      color: getEventColor('ENTRY'),
      includeDate: true,
    },
    {
      key: 'tp1',
      label: 'TP1',
      price: isValidEventPrice(tp[0]) ? Number(tp[0]) : null,
      ts: tp1Ts,
      color: getEventColor('TP1'),
      includeDate: false,
    },
    {
      key: 'tp2',
      label: 'TP2',
      price: isValidEventPrice(tp[1]) ? Number(tp[1]) : null,
      ts: tp2Ts,
      color: getEventColor('TP2'),
      includeDate: false,
    },
    {
      key: 'stop',
      label: 'STOP',
      price: isValidEventPrice(markers?.stop_px) ? Number(markers.stop_px) : null,
      ts: stopTs,
      color: getEventColor('STOP'),
      includeDate: false,
    },
    {
      key: 'final',
      label: 'FINAL EXIT',
      price: isValidEventPrice(markers?.exit_px) ? Number(markers.exit_px) : null,
      ts: finalExitTs,
      color: getEventColor('FINAL EXIT', { isFinal: true }),
      includeDate: true,
    },
  ]
}
