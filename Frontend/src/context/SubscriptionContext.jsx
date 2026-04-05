import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useApi } from './ApiContext'
import { getLogger } from '../utils/logger'

const log = getLogger('subscription')
const SubscriptionContext = createContext(null)

const POLL_INTERVAL_MS = 60_000 // 60s — also refreshes on window focus

function createInitialSubscription() {
  return {
    hasSubscription: false,
    status: 'none',
    plan: null,
    currentPeriodEnd: null,
    cancelAtPeriodEnd: false,
    simRunsUsed: 0,
    simRunsLimit: 10,
    simRunsResetAt: null,
    loading: true,
    error: null,
  }
}

function normalizeSubscription(payload = {}) {
  return {
    hasSubscription: Boolean(payload.has_subscription),
    status: payload.status || 'none',
    plan: payload.plan || null,
    currentPeriodEnd: payload.current_period_end || null,
    cancelAtPeriodEnd: Boolean(payload.cancel_at_period_end),
    simRunsUsed: payload.sim_runs_used ?? 0,
    simRunsLimit: payload.sim_runs_limit ?? 10,
    simRunsResetAt: payload.sim_runs_reset_at || null,
    loading: false,
    error: null,
  }
}

export function SubscriptionProvider({ children }) {
  const { get, session, isConnected } = useApi()
  const [subscription, setSubscription] = useState(createInitialSubscription)
  const intervalRef = useRef(null)
  const abortRef = useRef(null)

  const fetchStatus = useCallback(async (silent = false) => {
    if (!isConnected) {
      setSubscription((prev) => ({ ...prev, loading: false }))
      return
    }

    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const data = await get('/v1/stripe/subscription-status', {}, { signal: controller.signal })
      if (!controller.signal.aborted) {
        setSubscription(normalizeSubscription(data))
      }
    } catch (err) {
      if (controller.signal.aborted) return
      if (!silent) {
        log.warn('Subscription status fetch failed', {
          event: 'subscription_fetch_error',
          error_message: err?.message,
        })
      }
      setSubscription((prev) => ({
        ...prev,
        loading: false,
        error: err?.message || 'Failed to fetch subscription status',
      }))
    }
  }, [get, isConnected])

  // Fetch on mount and when session becomes connected
  useEffect(() => {
    if (session?.hasSession && isConnected) {
      fetchStatus(true)
    } else {
      setSubscription({ ...createInitialSubscription(), loading: false })
    }
  }, [session?.hasSession, isConnected, fetchStatus])

  // Poll every 60s
  useEffect(() => {
    if (!session?.hasSession || !isConnected) return

    intervalRef.current = setInterval(() => fetchStatus(true), POLL_INTERVAL_MS)
    return () => clearInterval(intervalRef.current)
  }, [session?.hasSession, isConnected, fetchStatus])

  // Refresh on window focus (handles pay-in-another-tab)
  useEffect(() => {
    function handleFocus() {
      if (session?.hasSession && isConnected) {
        fetchStatus(true)
      }
    }

    window.addEventListener('focus', handleFocus)
    return () => window.removeEventListener('focus', handleFocus)
  }, [session?.hasSession, isConnected, fetchStatus])

  // Cleanup abort controller
  useEffect(() => () => abortRef.current?.abort(), [])

  const isPaid = subscription.status === 'active' || subscription.status === 'past_due'
  const isFreeSubscribed = session?.hasSession && !isPaid
  const tier = isPaid ? 'paid' : (session?.hasSession ? 'free_subscribed' : 'free_unsubscribed')

  const value = useMemo(() => ({
    ...subscription,
    tier,
    isPaid,
    isFreeSubscribed,
    hasLocalActivation: isPaid, // backwards compat
    accessMode: isPaid ? 'server_backed' : 'ux_only',
    entitlementSource: isPaid ? 'stripe' : (session?.hasSession ? 'session_only' : 'none'),
    hasServerBackedEntitlement: isPaid,
    refreshSubscription: fetchStatus,
  }), [subscription, tier, isPaid, isFreeSubscribed, session?.hasSession, fetchStatus])

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
