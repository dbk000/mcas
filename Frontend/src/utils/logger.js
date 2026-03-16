/**
 * MCAS Frontend Logger
 *
 * Structured, leveled logging mirroring the backend's json_logging.py conventions.
 * Console output + ring buffer + optional server-side persistence.
 */
import { STORAGE_KEYS } from '../config/storageKeys'

const LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 }
const LEVEL_NAMES = ['DEBUG', 'INFO', 'WARN', 'ERROR']
const VERSION = typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : 'dev'

const _globalContext = {}

function setContext(key, value) {
  _globalContext[key] = value
}

let _correlationId = null
let _correlationCreatedAt = 0
const CORRELATION_TTL_MS = 5 * 60 * 1000

function generateHexId(len) {
  const arr = new Uint8Array(len / 2)
  crypto.getRandomValues(arr)
  return Array.from(arr, (byte) => byte.toString(16).padStart(2, '0')).join('')
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

rotateCorrelationId()

const RING_SIZE = 500
const _ringBuffer = []

function pushToRing(entry) {
  if (_ringBuffer.length >= RING_SIZE) {
    _ringBuffer.shift()
  }

  _ringBuffer.push(entry)
}

function getRecentLogs() {
  return _ringBuffer.slice()
}

function dumpLogs() {
  return JSON.stringify(_ringBuffer, null, 2)
}

function resolveLevel() {
  try {
    const storedLevel = localStorage.getItem(STORAGE_KEYS.LOG_LEVEL)
    if (storedLevel && LEVELS[storedLevel.toUpperCase()] !== undefined) {
      return LEVELS[storedLevel.toUpperCase()]
    }
  } catch {
    // no-op
  }

  try {
    const envLevel = import.meta.env?.VITE_LOG_LEVEL
    if (envLevel && LEVELS[envLevel.toUpperCase()] !== undefined) {
      return LEVELS[envLevel.toUpperCase()]
    }
  } catch {
    // no-op
  }

  return LEVELS.INFO
}

function setLogLevel(level) {
  try {
    localStorage.setItem(STORAGE_KEYS.LOG_LEVEL, String(level).toUpperCase())
  } catch {
    // no-op
  }
}

const REDACTED = '[REDACTED]'
const SENSITIVE_KEY_PATTERN = /(api[-_]?key|authorization|password|secret|token|cookie|set-cookie)/i

function sanitizeLogValue(value, key = '') {
  if (SENSITIVE_KEY_PATTERN.test(key)) {
    return REDACTED
  }

  if (Array.isArray(value)) {
    return value.map((item) => sanitizeLogValue(item, key))
  }

  if (!value || typeof value !== 'object') {
    return value
  }

  return Object.fromEntries(
    Object.entries(value).map(([childKey, childValue]) => [
      childKey,
      sanitizeLogValue(childValue, childKey),
    ])
  )
}

function sanitizeLogEntry(entry) {
  return sanitizeLogValue(entry)
}

const _sessionId = generateHexId(16)
let _pendingLogs = []
let _shipTimer = null
let _lastErrorShipTime = 0
let _shipBatch = null
let _sendBeaconBatch = null

function buildPayload(entries) {
  return {
    session_id: _sessionId,
    user_agent: navigator.userAgent,
    entries,
  }
}

export function configureLogShipping({ shipBatch, sendBeaconBatch } = {}) {
  _shipBatch = typeof shipBatch === 'function' ? shipBatch : null
  _sendBeaconBatch = typeof sendBeaconBatch === 'function' ? sendBeaconBatch : null
}

async function shipLogs() {
  if (_pendingLogs.length === 0 || !_shipBatch) {
    return
  }

  const entries = _pendingLogs
  _pendingLogs = []

  try {
    await _shipBatch(buildPayload(entries))
  } catch {
    // best-effort transport
  }
}

function scheduleShipping() {
  if (_shipTimer) {
    return
  }

  _shipTimer = setInterval(() => {
    void shipLogs()
  }, 30_000)
}

if (typeof window !== 'undefined') {
  window.addEventListener('beforeunload', () => {
    if (_pendingLogs.length === 0 || !_sendBeaconBatch) {
      return
    }

    try {
      _sendBeaconBatch(buildPayload(_pendingLogs))
    } catch {
      // no-op
    }
  })
}

class Logger {
  constructor(name) {
    this.name = name
  }

  _log(levelIdx, message, extra = {}) {
    if (levelIdx < resolveLevel()) {
      return
    }

    const levelName = LEVEL_NAMES[levelIdx]
    getCorrelationId()

    const entry = sanitizeLogEntry({
      timestamp: new Date().toISOString(),
      level: levelName,
      logger: this.name,
      message,
      service: 'frontend',
      version: VERSION,
      ..._globalContext,
      ...extra,
    })

    if (levelName === 'ERROR') {
      console.error(`[${levelName}] ${this.name}: ${message}`, entry)
    } else {
      console.warn(`[${levelName}] ${this.name}: ${message}`, entry)
    }

    pushToRing(entry)
    _pendingLogs.push(entry)
    scheduleShipping()

    if (levelIdx >= LEVELS.ERROR) {
      const now = Date.now()
      if (now - _lastErrorShipTime > 5000) {
        _lastErrorShipTime = now
        void shipLogs()
      }
    }
  }

  debug(message, extra) { this._log(LEVELS.DEBUG, message, extra) }
  info(message, extra) { this._log(LEVELS.INFO, message, extra) }
  warn(message, extra) { this._log(LEVELS.WARN, message, extra) }
  error(message, extra) { this._log(LEVELS.ERROR, message, extra) }
}

export function getLogger(name) {
  return new Logger(name)
}

if (typeof window !== 'undefined') {
  window.__mcas = { dumpLogs, getRecentLogs, setLogLevel }
}
