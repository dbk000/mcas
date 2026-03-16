import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { STORAGE_KEYS } from '../config/storageKeys'

const SubscriptionContext = createContext(null)

function normalizeStoredTier(rawTier) {
  return rawTier === 'paid' || rawTier === 'free' ? rawTier : 'free'
}

function getStorage() {
  if (typeof window === 'undefined') {
    return null
  }

  return window.localStorage
}

function writeStoredTier(storage, tier, activatedAt) {
  storage.setItem(STORAGE_KEYS.ACCESS_TIER, tier)
  storage.removeItem(STORAGE_KEYS.LEGACY_TIER)

  if (tier === 'paid' && activatedAt) {
    storage.setItem(STORAGE_KEYS.ACCESS_ACTIVATED_AT, activatedAt)
  } else {
    storage.removeItem(STORAGE_KEYS.ACCESS_ACTIVATED_AT)
  }

  storage.removeItem(STORAGE_KEYS.LEGACY_TIER_ACTIVATED_AT)
}

function readStoredTier() {
  const storage = getStorage()
  if (!storage) {
    return { tier: 'free', activatedAt: null }
  }

  const rawTier = storage.getItem(STORAGE_KEYS.ACCESS_TIER) ?? storage.getItem(STORAGE_KEYS.LEGACY_TIER)
  const tier = normalizeStoredTier(rawTier)
  const rawActivatedAt = storage.getItem(STORAGE_KEYS.ACCESS_ACTIVATED_AT)
    ?? storage.getItem(STORAGE_KEYS.LEGACY_TIER_ACTIVATED_AT)
  const activatedAt = tier === 'paid' ? rawActivatedAt : null

  if (
    storage.getItem(STORAGE_KEYS.ACCESS_TIER) !== tier ||
    storage.getItem(STORAGE_KEYS.LEGACY_TIER) !== null ||
    storage.getItem(STORAGE_KEYS.LEGACY_TIER_ACTIVATED_AT) !== null ||
    (tier !== 'paid' && storage.getItem(STORAGE_KEYS.ACCESS_ACTIVATED_AT) !== null)
  ) {
    writeStoredTier(storage, tier, activatedAt)
  }

  return { tier, activatedAt }
}

export function SubscriptionProvider({ children }) {
  const [{ tier, activatedAt }, setState] = useState(() => readStoredTier())

  const persist = useCallback((nextTier, nextActivatedAt = null) => {
    const normalizedTier = normalizeStoredTier(nextTier)
    const normalizedActivatedAt = normalizedTier === 'paid'
      ? (nextActivatedAt || new Date().toISOString())
      : null

    const storage = getStorage()
    if (storage) {
      writeStoredTier(storage, normalizedTier, normalizedActivatedAt)
    }

    setState({ tier: normalizedTier, activatedAt: normalizedActivatedAt })
  }, [])

  // Browser-local activation only. This does not assert a server entitlement.
  const setPaid = useCallback((isoTs = new Date().toISOString()) => {
    persist('paid', isoTs)
  }, [persist])

  const setFree = useCallback(() => {
    persist('free')
  }, [persist])

  const clearTier = useCallback(() => {
    const storage = getStorage()
    if (storage) {
      storage.removeItem(STORAGE_KEYS.ACCESS_TIER)
      storage.removeItem(STORAGE_KEYS.ACCESS_ACTIVATED_AT)
      storage.removeItem(STORAGE_KEYS.LEGACY_TIER)
      storage.removeItem(STORAGE_KEYS.LEGACY_TIER_ACTIVATED_AT)
    }

    setState({ tier: 'free', activatedAt: null })
  }, [])

  useEffect(() => {
    function handleStorage(event) {
      if (
        event.key === null ||
        event.key === STORAGE_KEYS.ACCESS_TIER ||
        event.key === STORAGE_KEYS.ACCESS_ACTIVATED_AT ||
        event.key === STORAGE_KEYS.LEGACY_TIER ||
        event.key === STORAGE_KEYS.LEGACY_TIER_ACTIVATED_AT
      ) {
        setState(readStoredTier())
      }
    }

    window.addEventListener('storage', handleStorage)
    return () => window.removeEventListener('storage', handleStorage)
  }, [])

  const hasLocalActivation = tier === 'paid'

  const value = useMemo(() => ({
    tier,
    activatedAt,
    isPaid: hasLocalActivation,
    hasLocalActivation,
    accessMode: 'ux_only',
    entitlementSource: hasLocalActivation ? 'local_browser' : 'none',
    hasServerBackedEntitlement: false,
    setPaid,
    setFree,
    clearTier,
  }), [activatedAt, clearTier, hasLocalActivation, setFree, setPaid, tier])

  return (
    <SubscriptionContext.Provider value={value}>
      {children}
    </SubscriptionContext.Provider>
  )
}

export function useSubscription() {
  const context = useContext(SubscriptionContext)
  if (!context) {
    throw new Error('useSubscription must be used within a SubscriptionProvider')
  }
  return context
}
