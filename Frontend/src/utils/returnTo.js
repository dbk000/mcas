import { STORAGE_KEYS } from '../config/storageKeys'

const PENDING_RETURN_TO_KEY = STORAGE_KEYS.CHECKOUT_RETURN_TO
const ALLOWED_PREFIXES = ['/app', '/settings']

export const DEFAULT_RETURN_TO = '/app/strategies'

function getSessionStorage() {
  if (typeof window === 'undefined') {
    return null
  }

  return window.sessionStorage
}

export function sanitizeReturnTo(rawValue, options = {}) {
  const { fallback = null } = options
  if (!rawValue || typeof rawValue !== 'string') return fallback

  const value = rawValue.trim()
  if (!value.startsWith('/')) return fallback
  if (value.startsWith('//') || value.includes('\\')) return fallback

  const baseOrigin = typeof window === 'undefined'
    ? 'https://mcas.invalid'
    : window.location.origin

  let parsed
  try {
    parsed = new URL(value, baseOrigin)
  } catch {
    return fallback
  }

  if (parsed.origin !== baseOrigin) return fallback

  const normalized = `${parsed.pathname}${parsed.search}${parsed.hash}`
  const isAllowed = ALLOWED_PREFIXES.some((prefix) => (
    normalized === prefix ||
    normalized.startsWith(`${prefix}/`) ||
    normalized.startsWith(`${prefix}?`) ||
    normalized.startsWith(`${prefix}#`)
  ))

  return isAllowed ? normalized : fallback
}

export function buildUpgradeHref(rawReturnTo) {
  const safeReturnTo = sanitizeReturnTo(rawReturnTo)
  return safeReturnTo
    ? `/upgrade?returnTo=${encodeURIComponent(safeReturnTo)}`
    : '/upgrade'
}

export function storePendingReturnTo(returnTo) {
  const storage = getSessionStorage()
  if (!storage) return null

  const safeReturnTo = sanitizeReturnTo(returnTo)

  if (!safeReturnTo) {
    storage.removeItem(PENDING_RETURN_TO_KEY)
    return null
  }

  storage.setItem(PENDING_RETURN_TO_KEY, safeReturnTo)
  return safeReturnTo
}

export function readPendingReturnTo() {
  const storage = getSessionStorage()
  if (!storage) return null

  return sanitizeReturnTo(storage.getItem(PENDING_RETURN_TO_KEY))
}

export function clearPendingReturnTo() {
  const storage = getSessionStorage()
  if (!storage) return

  storage.removeItem(PENDING_RETURN_TO_KEY)
}

export function takePendingReturnTo() {
  const pending = readPendingReturnTo()
  clearPendingReturnTo()
  return pending
}
