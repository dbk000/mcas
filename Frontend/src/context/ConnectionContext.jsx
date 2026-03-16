/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { normalizeRequestError } from '../api/errors'
import { createInitialConnectionStatus, sanitizeApiConfig } from '../api/requestClient'
import { createSessionClient } from '../api/sessionClient'
import { useSessionContext } from './SessionContext'
import { useToast } from './ToastContext'
import { getLogger } from '../utils/logger'

const log = getLogger('connection')
const ConnectionContext = createContext(null)

function connectAbortSignals(sourceSignal, controller) {
  if (!sourceSignal) {
    return () => {}
  }

  if (sourceSignal.aborted) {
    controller.abort()
    return () => {}
  }

  const handleAbort = () => controller.abort()
  sourceSignal.addEventListener('abort', handleAbort, { once: true })

  return () => {
    sourceSignal.removeEventListener('abort', handleAbort)
  }
}

function normalizeHealthStatus(status) {
  if (status === 'healthy') {
    return 'healthy'
  }

  if (!status) {
    return null
  }

  return 'unhealthy'
}

function getMostRecentTimestamp(...values) {
  return values
    .filter(Boolean)
    .sort((left, right) => new Date(right).getTime() - new Date(left).getTime())[0] || null
}

export function ConnectionProvider({ children }) {
  const { addToast } = useToast()
  const { config, session, sessionReady } = useSessionContext()
  const configRef = useRef(config)
  const requestIdRef = useRef(0)
  const healthAbortRef = useRef(null)
  const hasCheckedRef = useRef(false)
  const [healthState, setHealthState] = useState(createInitialConnectionStatus)

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => (
    () => {
      healthAbortRef.current?.abort()
    }
  ), [])

  const client = useMemo(() => createSessionClient({
    getConfig: () => configRef.current,
  }), [])

  const refreshConnectionStatus = useCallback(async (options = {}) => {
    const { silent = true, signal, configOverride } = options
    const nextConfig = sanitizeApiConfig(configOverride ?? configRef.current)

    if (!nextConfig.baseUrl) {
      setHealthState(createInitialConnectionStatus())
      return createInitialConnectionStatus()
    }

    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId

    healthAbortRef.current?.abort()

    const controller = new AbortController()
    const disconnectAbortSignals = connectAbortSignals(signal, controller)
    healthAbortRef.current = controller

    setHealthState((previous) => ({
      ...previous,
      checking: true,
    }))

    try {
      const healthData = await client.getHealth({
        configOverride: nextConfig,
        signal: controller.signal,
      })

      if (requestId !== requestIdRef.current || controller.signal.aborted) {
        return null
      }

      const nextHealthState = {
        health: normalizeHealthStatus(healthData?.status),
        auth: null,
        redisMs: healthData?.redis_ms ?? null,
        pgMs: healthData?.pg_ms ?? null,
        lastChecked: new Date().toISOString(),
        checking: false,
      }

      setHealthState(nextHealthState)
      return nextHealthState
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (normalized.isAbort || requestId !== requestIdRef.current) {
        return null
      }

      log.error('Health check failed', {
        event: 'health_check_failed',
        error_message: normalized.message,
      })

      const nextHealthState = {
        health: 'error',
        auth: null,
        redisMs: null,
        pgMs: null,
        lastChecked: new Date().toISOString(),
        checking: false,
      }

      setHealthState(nextHealthState)

      if (!silent) {
        addToast(`Connection failed: ${normalized.message}`, 'error')
      }

      return nextHealthState
    } finally {
      disconnectAbortSignals()

      if (healthAbortRef.current === controller) {
        healthAbortRef.current = null
      }
    }
  }, [addToast, client])

  useEffect(() => {
    hasCheckedRef.current = false
    setHealthState(createInitialConnectionStatus())
  }, [config.baseUrl])

  useEffect(() => {
    if (!config.baseUrl || !sessionReady || hasCheckedRef.current || healthState.checking) {
      return
    }

    hasCheckedRef.current = true
    void refreshConnectionStatus({ silent: true })
  }, [config.baseUrl, healthState.checking, refreshConnectionStatus, sessionReady])

  useEffect(() => {
    if (!config.baseUrl || !sessionReady || !session.lastChecked) {
      return
    }

    void refreshConnectionStatus({ silent: true })
  }, [config.baseUrl, refreshConnectionStatus, session.lastChecked, sessionReady])

  const connectionStatus = useMemo(() => ({
    ...healthState,
    auth: session.auth,
    hasSession: session.hasSession,
    hasAppAccess: session.hasAppAccess,
    checking: healthState.checking || session.checking,
    lastChecked: getMostRecentTimestamp(healthState.lastChecked, session.lastChecked),
  }), [healthState, session])

  const isConnected = session.hasSession && session.auth === 'ok' && healthState.health === 'healthy'

  const value = useMemo(() => ({
    connectionStatus,
    refreshConnectionStatus,
    isConnected,
  }), [connectionStatus, isConnected, refreshConnectionStatus])

  return (
    <ConnectionContext.Provider value={value}>
      {children}
    </ConnectionContext.Provider>
  )
}

export function useConnectionContext() {
  const context = useContext(ConnectionContext)

  if (!context) {
    throw new Error('useConnectionContext must be used within a ConnectionProvider')
  }

  return context
}
