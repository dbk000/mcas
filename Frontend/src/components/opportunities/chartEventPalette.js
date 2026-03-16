const EVENT_COLORS = Object.freeze({
  entry: '#f59e0b',
  stop: '#ef4444',
  tp1: '#22c55e',
  tp2: '#3b82f6',
  tp3: '#a855f7',
  tpFallback: '#22c55e',
  exit: '#06b6d4',
  misc: '#94a3b8',
})

function parseTpIndex(label) {
  const match = String(label || '').match(/tp\s*([0-9]+)/i)
  if (!match) return null
  const idx = Number(match[1])
  return Number.isFinite(idx) ? idx : null
}

export function getEventColor(label, { isFinal = false } = {}) {
  if (isFinal) return EVENT_COLORS.exit
  const text = String(label || '').toLowerCase()
  if (!text) return EVENT_COLORS.misc

  if (text.includes('entry')) return EVENT_COLORS.entry
  if (text.includes('stop') || text.includes('sl')) return EVENT_COLORS.stop
  if (text.includes('tp')) {
    const tpIndex = parseTpIndex(text)
    if (tpIndex === 1) return EVENT_COLORS.tp1
    if (tpIndex === 2) return EVENT_COLORS.tp2
    if (tpIndex === 3) return EVENT_COLORS.tp3
    return EVENT_COLORS.tpFallback
  }
  if (text.includes('final exit') || text.includes('exit')) return EVENT_COLORS.exit
  return EVENT_COLORS.misc
}
