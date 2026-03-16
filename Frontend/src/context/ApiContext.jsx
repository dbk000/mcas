import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { getDefaultChaosMode, normalizeChaosMode, normalizeFixtureState } from '../api/devOverrides'
import { createApiClient } from '../api/apiClient'
import { createTelemetryClient } from '../api/telemetryClient'
import { sanitizeApiConfig } from '../api/requestClient'
import { useConnectionStatus } from '../hooks/useConnectionStatus'
import { useSession } from '../hooks/useSession'
import { configureLogShipping, getLogger } from '../utils/logger'
import { STORAGE_KEYS } from '../config/storageKeys'

const log = getLogger('api')
const ApiContext = createContext(null)

function readStoredValue(read) {
  if (typeof window === 'undefined') {
    return null
  }

  try {
    return read()
  } catch {
    return null
  }
}

function persistJson(key, value) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    log.warn('Storage write failed', { event: 'storage_write_failed', key })
  }
}

function persistString(key, value) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(key, value)
  } catch {
    log.warn('Storage write failed', { event: 'storage_write_failed', key })
  }
}

function readStoredChaosMode() {
  if (!import.meta.env.DEV) {
    return null
  }

  const saved = readStoredValue(() => window.localStorage.getItem(STORAGE_KEYS.CHAOS_CONFIG))
  if (!saved) {
    return getDefaultChaosMode()
  }

  try {
    return normalizeChaosMode(JSON.parse(saved))
  } catch {
    return getDefaultChaosMode()
  }
}

function readStoredFixtureState() {
  if (!import.meta.env.DEV) {
    return 'live'
  }

  const saved = readStoredValue(() => window.localStorage.getItem(STORAGE_KEYS.FIXTURE_CONFIG))
  return normalizeFixtureState(saved || 'curated')
}

export function ApiProvider({ children }) {
  const {
    config,
    saveConfig,
    session,
    sessionReady,
    createSession,
    refreshSession,
    clearSession,
    markUnauthorized,
  } = useSession()
  const {
    connectionStatus,
    refreshConnectionStatus,
    isConnected,
  } = useConnectionStatus()

  const [chaosMode, setChaosMode] = useState(readStoredChaosMode)
  const [fixtureState, setFixtureState] = useState(readStoredFixtureState)

  useEffect(() => {
    if (chaosMode) {
      persistJson(STORAGE_KEYS.CHAOS_CONFIG, chaosMode)
    }
  }, [chaosMode])

  useEffect(() => {
    if (fixtureState) {
      persistString(STORAGE_KEYS.FIXTURE_CONFIG, fixtureState)
    }
  }, [fixtureState])

  const client = useMemo(() => createApiClient({
    getConfig: () => config,
    getDevOverrides: () => ({
      chaosMode,
      fixtureState,
    }),
    onUnauthorized: () => {
      markUnauthorized()
    },
  }), [chaosMode, config, fixtureState, markUnauthorized])

  const telemetryClient = useMemo(() => createTelemetryClient({
    getConfig: () => config,
  }), [config])

  useEffect(() => {
    configureLogShipping({
      shipBatch: (payload) => telemetryClient.shipBatch(payload),
      sendBeaconBatch: (payload) => telemetryClient.sendBeaconBatch(payload),
    })

    return () => {
      configureLogShipping({
        shipBatch: null,
        sendBeaconBatch: null,
      })
    }
  }, [telemetryClient])

  const toggleChaos = useCallback((nextChaos = {}) => {
    if (!import.meta.env.DEV) {
      return
    }

    setChaosMode((previous) => normalizeChaosMode({
      ...(previous || getDefaultChaosMode()),
      ...nextChaos,
    }))
  }, [])

  const toggleFixtureState = useCallback((nextState) => {
    if (!import.meta.env.DEV) {
      return
    }

    setFixtureState(normalizeFixtureState(nextState))
  }, [])

  const testConfig = useCallback(async (candidateConfig, options = {}) => {
    const { silent = false, signal } = options
    const nextConfig = sanitizeApiConfig(candidateConfig)

    const result = await createSession({
      baseUrl: nextConfig.baseUrl,
      apiKey: candidateConfig?.apiKey,
      signal,
      silent,
      persistBaseUrl: true,
    })

    if (result.ok) {
      await refreshConnectionStatus({ silent: true, signal })
    }

    return result.ok
  }, [createSession, refreshConnectionStatus])

  const testConnection = useCallback((arg = false) => {
    if (typeof arg === 'boolean') {
      return refreshConnectionStatus({ silent: arg })
    }

    const { silent = false, configOverride, signal } = arg || {}
    return refreshConnectionStatus({
      silent,
      signal,
      configOverride,
    })
  }, [refreshConnectionStatus])

  const value = useMemo(() => ({
    config,
    saveConfig,
    updateConfig: saveConfig,
    session,
    sessionReady,
    refreshSession,
    clearSession,
    connectionStatus,
    testConfig,
    testConnection,
    isConnected,
    request: client.request,
    get: client.get,
    post: client.post,
    chaosMode,
    toggleChaos,
    fixtureState,
    toggleFixtureState,
  }), [
    chaosMode,
    clearSession,
    client.get,
    client.post,
    client.request,
    config,
    connectionStatus,
    fixtureState,
    isConnected,
    refreshSession,
    saveConfig,
    session,
    sessionReady,
    testConfig,
    testConnection,
    toggleChaos,
    toggleFixtureState,
  ])

  return (
    <ApiContext.Provider value={value}>
      {children}
    </ApiContext.Provider>
  )
}

export function useApi() {
  const context = useContext(ApiContext)

  if (!context) {
    throw new Error('useApi must be used within an ApiProvider')
  }

  return context
}
