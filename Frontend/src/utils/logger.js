/**
 * MCAS Frontend Logger
 *
 * Structured, leveled logging mirroring the backend's json_logging.py conventions.
 * Console output + ring buffer + optional server-side persistence.
 */

// ---------------------------------------------------------------------------
// Levels
// ---------------------------------------------------------------------------
const LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 }
const LEVEL_NAMES = ['DEBUG', 'INFO', 'WARN', 'ERROR']

// ---------------------------------------------------------------------------
// Version (injected at build time, fallback to unknown)
// ---------------------------------------------------------------------------
const VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'

// ---------------------------------------------------------------------------
// Global context (spread into every log entry)
// ---------------------------------------------------------------------------
const _globalContext = {}

export function setContext(k, v) { _globalContext[k] = v }
export function clearContext(k) { delete _globalContext[k] }

// ---------------------------------------------------------------------------
// Correlation ID — 8-char hex, rotates every 5 min or on navigation
// ---------------------------------------------------------------------------
let _correlationId = null
let _correlationCreatedAt = 0
const CORRELATION_TTL_MS = 5 * 60 * 1000

function generateHexId(len) {
  const arr = new Uint8Array(len / 2)
  crypto.getRandomValues(arr)
  return Array.from(arr, b => b.toString(16).padStart(2, '0')).join('')
}

export function rotateCorrelationId() {
  _correlationId = generateHexId(8)
  _correlationCreatedAt = Date.now()
  setContext('correlation_id', _correlationId)
  return _correlationId
}

export function getCorrelationId() {
  if (!_correlationId || Date.now() - _correlationCreatedAt > CORRELATION_TTL_MS) {
    rotateCorrelationId()
  }
  return _correlationId
}

// Initialise on load
rotateCorrelationId()

// ---------------------------------------------------------------------------
// Ring buffer (500 entries)
// ---------------------------------------------------------------------------
const RING_SIZE = 500
const _ringBuffer = []

function pushToRing(entry) {
  if (_ringBuffer.length >= RING_SIZE) _ringBuffer.shift()
  _ringBuffer.push(entry)
}

export function getRecentLogs() { return _ringBuffer.slice() }
export function dumpLogs() { return JSON.stringify(_ringBuffer, null, 2) }

// ---------------------------------------------------------------------------
// Level control
// ---------------------------------------------------------------------------
function resolveLevel() {
  // localStorage takes priority (can change at runtime, no restart)
  try {
    const ls = localStorage.getItem('mcas_log_level')
    if (ls && LEVELS[ls.toUpperCase()] !== undefined) return LEVELS[ls.toUpperCase()]
  } catch { /* SSR / restricted */ }
  // Env-var fallback (set at build time)
  try {
    const env = import.meta.env?.VITE_LOG_LEVEL
    if (env && LEVELS[env.toUpperCase()] !== undefined) return LEVELS[env.toUpperCase()]
  } catch { /* ignore */ }
  return LEVELS.INFO
}

export function setLogLevel(level) {
  try { localStorage.setItem('mcas_log_level', String(level).toUpperCase()) } catch { /* noop */ }
}

// ---------------------------------------------------------------------------
// Server-side log shipping
// ---------------------------------------------------------------------------
const _sessionId = generateHexId(16)
let _pendingLogs = []
let _shipTimer = null
let _lastErrorShipTime = 0

function getBaseUrl() {
  try {
    const raw = localStorage.getItem('mcas_api_config')
    if (raw) {
      const parsed = JSON.parse(raw)
      return (parsed.baseUrl || '').replace(/\/+$/, '')
    }
  } catch { /* noop */ }
  return ''
}

function shipLogs() {
  if (_pendingLogs.length === 0) return
  const baseUrl = getBaseUrl()
  if (!baseUrl) return

  const payload = JSON.stringify({
    session_id: _sessionId,
    user_agent: navigator.userAgent,
    entries: _pendingLogs,
  })
  _pendingLogs = []

  fetch(`${baseUrl}/v1/telemetry/frontend-logs`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: payload,
  }).catch(() => { /* best-effort, silently drop */ })
}

function scheduleShipping() {
  if (_shipTimer) return
  _shipTimer = setInterval(shipLogs, 30_000)
}

// Ship on page unload via beacon
if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => {
    if (_pendingLogs.length === 0) return
    const baseUrl = getBaseUrl()
    if (!baseUrl) return
    const payload = JSON.stringify({
      session_id: _sessionId,
      user_agent: navigator.userAgent,
      entries: _pendingLogs,
    })
    try { navigator.sendBeacon(`${baseUrl}/v1/telemetry/frontend-logs`, payload) } catch { /* noop */ }
  })
}

// ---------------------------------------------------------------------------
// Logger factory
// ---------------------------------------------------------------------------
const CONSOLE_MAP = {
  DEBUG: 'info',
  INFO: 'info',
  WARN: 'warn',
  ERROR: 'error',
}

class Logger {
  constructor(name) {
    this.name = name
  }

  _log(levelIdx, message, extra = {}) {
    if (levelIdx < resolveLevel()) return

    const levelName = LEVEL_NAMES[levelIdx]

    // Ensure correlation ID is fresh
    getCorrelationId()

    const entry = {
      timestamp: new Date().toISOString(),
      level: levelName,
      logger: this.name,
      message,
      service: 'frontend',
      version: VERSION,
      ..._globalContext,
      ...extra,
    }

    // Console
    const method = CONSOLE_MAP[levelName]
    console[method](`[${levelName}] ${this.name}: ${message}`, entry)

    // Ring buffer
    pushToRing(entry)

    // Pending buffer for server shipping
    _pendingLogs.push(entry)
    scheduleShipping()

    // Immediate ship on ERROR (debounced: at most once per 5s)
    if (levelIdx >= LEVELS.ERROR) {
      const now = Date.now()
      if (now - _lastErrorShipTime > 5000) {
        _lastErrorShipTime = now
        shipLogs()
      }
    }
  }

  debug(message, extra) { this._log(LEVELS.DEBUG, message, extra) }
  info(message, extra)  { this._log(LEVELS.INFO, message, extra) }
  warn(message, extra)  { this._log(LEVELS.WARN, message, extra) }
  error(message, extra) { this._log(LEVELS.ERROR, message, extra) }
}

export function getLogger(name) {
  return new Logger(name)
}

// ---------------------------------------------------------------------------
// Devtools helper
// ---------------------------------------------------------------------------
if (typeof window !== 'undefined') {
  window.__mcas = { dumpLogs, getRecentLogs, setLogLevel }
}
