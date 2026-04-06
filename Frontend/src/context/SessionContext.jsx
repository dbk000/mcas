/* eslint-disable react-refresh/only-export-components */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { normalizeRequestError } from '../api/errors'
import { sanitizeApiConfig } from '../api/requestClient'
import { createSessionClient } from '../api/sessionClient'
import { useToast } from './ToastContext'
import { getLogger } from '../utils/logger'
import { setFrontendConfig } from '../config/governed'
import { STORAGE_KEYS } from '../config/storageKeys'

const log = getLogger('session')
const SessionContext = createContext(null)

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

function removeStoredValue(key) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.removeItem(key)
  } catch {
    log.warn('Storage remove failed', { event: 'storage_remove_failed', key })
  }
}

function readStoredConfig() {
  const storedBaseUrl = readStoredValue(() => window.localStorage.getItem(STORAGE_KEYS.API_BASE_URL))
  const legacySaved = readStoredValue(() => window.localStorage.getItem(STORAGE_KEYS.LEGACY_API_CONFIG))

  let rawBaseUrl = storedBaseUrl || ''
  let sawLegacyKey = false

  if (!rawBaseUrl && legacySaved) {
    try {
      const parsed = JSON.parse(legacySaved)
      rawBaseUrl = parsed?.baseUrl || ''
      sawLegacyKey = Boolean(parsed?.apiKey)
    } catch {
      log.warn('Config parse error', { event: 'config_parse_error' })
    }
  }

  const config = sanitizeApiConfig({ baseUrl: rawBaseUrl })

  readStoredValue(() => {
    if (config.baseUrl) {
      window.localStorage.setItem(STORAGE_KEYS.API_BASE_URL, config.baseUrl)
    } else {
      window.localStorage.removeItem(STORAGE_KEYS.API_BASE_URL)
    }
    window.localStorage.removeItem(STORAGE_KEYS.LEGACY_API_CONFIG)
    return null
  })

  log.info('Base URL loaded', {
    event: 'base_url_loaded',
    has_url: Boolean(config.baseUrl),
    scrubbed_legacy_key: sawLegacyKey,
  })

  return config
}

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

function createInitialSessionState() {
  return {
    checking: false,
    hasSession: false,
    hasAppAccess: false,
    auth: null,
    sessionSource: 'none',
    entitlementMode: 'preview_only',
    apiOrigin: null,
    lastChecked: null,
    error: null,
  }
}

function normalizeBootstrap(payload = {}) {
  return {
    checking: false,
    hasSession: Boolean(payload.has_session),
    hasAppAccess: Boolean(payload.has_app_access),
    auth: payload.auth || (payload.has_session ? 'ok' : 'missing'),
    sessionSource: payload.session_source || 'none',
    entitlementMode: payload.entitlement_mode || 'preview_only',
    apiOrigin: payload.api_origin || null,
    lastChecked: payload.checked_at || new Date().toISOString(),
    error: null,
  }
}

export function SessionProvider({ children }) {
  const { addToast } = useToast()
  const [config, setConfig] = useState(readStoredConfig)
  const [session, setSession] = useState(createInitialSessionState)
  const configRef = useRef(config)
  const requestIdRef = useRef(0)
  const sessionAbortRef = useRef(null)
  const hasBootstrappedRef = useRef(false)

  useEffect(() => {
    configRef.current = config
  }, [config])

  useEffect(() => {
    if (config.baseUrl) {
      persistString(STORAGE_KEYS.API_BASE_URL, config.baseUrl)
    } else {
      removeStoredValue(STORAGE_KEYS.API_BASE_URL)
    }

    removeStoredValue(STORAGE_KEYS.LEGACY_API_CONFIG)
  }, [config.baseUrl])

  useEffect(() => (
    () => {
      sessionAbortRef.current?.abort()
    }
  ), [])

  const client = useMemo(() => createSessionClient({
    getConfig: () => configRef.current,
  }), [])

  const resetSession = useCallback(() => {
    sessionAbortRef.current?.abort()
    sessionAbortRef.current = null
    requestIdRef.current += 1
    setSession(createInitialSessionState())
  }, [])

  const saveConfig = useCallback((nextConfig = {}) => {
    const mergedConfig = sanitizeApiConfig({
      ...configRef.current,
      ...nextConfig,
    })

    const baseUrlChanged = mergedConfig.baseUrl !== configRef.current.baseUrl

    log.info('Base URL updated', {
      event: 'base_url_updated',
      has_url: Boolean(mergedConfig.baseUrl),
      base_url_changed: baseUrlChanged,
    })

    setConfig(mergedConfig)

    if (baseUrlChanged) {
      hasBootstrappedRef.current = false
      resetSession()
    }

    return mergedConfig
  }, [resetSession])

  const refreshSession = useCallback(async (options = {}) => {
    const { silent = false, signal, configOverride } = options
    const nextConfig = sanitizeApiConfig(configOverride ?? configRef.current)

    if (!nextConfig.baseUrl) {
      setSession(createInitialSessionState())
      return createInitialSessionState()
    }

    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId

    sessionAbortRef.current?.abort()

    const controller = new AbortController()
    const disconnectAbortSignals = connectAbortSignals(signal, controller)
    sessionAbortRef.current = controller

    setSession((previous) => ({
      ...previous,
      checking: true,
      error: null,
    }))

    try {
      const payload = await client.getBootstrap({
        configOverride: nextConfig,
        signal: controller.signal,
      })

      if (requestId !== requestIdRef.current || controller.signal.aborted) {
        return null
      }

      const nextSession = normalizeBootstrap(payload)
      setFrontendConfig(payload.frontend_config)
      setSession(nextSession)
      return nextSession
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (normalized.isAbort || requestId !== requestIdRef.current) {
        return null
      }

      log.error('Session bootstrap failed', {
        event: 'session_bootstrap_error',
        error_message: normalized.message,
      })

      const nextSession = {
        ...createInitialSessionState(),
        checking: false,
        auth: 'error',
        lastChecked: new Date().toISOString(),
        error: normalized,
      }

      setSession(nextSession)

      if (!silent) {
        addToast(`Session bootstrap failed: ${normalized.message}`, 'error')
      }

      return nextSession
    } finally {
      disconnectAbortSignals()

      if (sessionAbortRef.current === controller) {
        sessionAbortRef.current = null
      }
    }
  }, [addToast, client])

  const createSession = useCallback(async ({
    baseUrl,
    apiKey,
    signal,
    silent = false,
    persistBaseUrl = true,
  } = {}) => {
    const nextConfig = sanitizeApiConfig({
      ...configRef.current,
      ...(baseUrl !== undefined ? { baseUrl } : {}),
    })

    if (!nextConfig.baseUrl) {
      if (!silent) {
        addToast('Please enter an API base URL', 'error')
      }
      return { ok: false, error: null }
    }

    if (!(apiKey || '').trim()) {
      if (!silent) {
        addToast('Please enter an API key', 'error')
      }
      return { ok: false, error: null }
    }

    if (persistBaseUrl) {
      setConfig(nextConfig)
    }

    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId

    sessionAbortRef.current?.abort()

    const controller = new AbortController()
    const disconnectAbortSignals = connectAbortSignals(signal, controller)
    sessionAbortRef.current = controller

    setSession((previous) => ({
      ...previous,
      checking: true,
      error: null,
    }))

    try {
      const payload = await client.createSession(apiKey, {
        configOverride: nextConfig,
        signal: controller.signal,
      })

      if (requestId !== requestIdRef.current || controller.signal.aborted) {
        return { ok: false, error: null }
      }

      const nextSession = normalizeBootstrap(payload)
      setFrontendConfig(payload.frontend_config)
      hasBootstrappedRef.current = true
      setSession(nextSession)

      if (!silent) {
        addToast('Backend session established', 'success')
      }

      log.info('Session established', {
        event: 'session_established',
        api_origin: nextSession.apiOrigin,
      })

      return { ok: nextSession.hasSession, session: nextSession }
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (normalized.isAbort || requestId !== requestIdRef.current) {
        return { ok: false, error: normalized }
      }

      const nextSession = {
        ...createInitialSessionState(),
        checking: false,
        auth: normalized.status === 401 ? 'unauthorized' : 'error',
        lastChecked: new Date().toISOString(),
        error: normalized,
      }

      setSession(nextSession)

      if (!silent) {
        addToast(
          normalized.status === 401 ? 'Invalid API key' : `Session setup failed: ${normalized.message}`,
          'error'
        )
      }

      log.warn('Session setup failed', {
        event: 'session_setup_failed',
        error_message: normalized.message,
        status: normalized.status,
      })

      return { ok: false, error: normalized, session: nextSession }
    } finally {
      disconnectAbortSignals()

      if (sessionAbortRef.current === controller) {
        sessionAbortRef.current = null
      }
    }
  }, [addToast, client])

  const clearSession = useCallback(async (options = {}) => {
    const { silent = false, signal } = options

    if (configRef.current.baseUrl) {
      try {
        await client.clearSession({ signal })
      } catch (error) {
        const normalized = normalizeRequestError(error)

        if (!normalized.isAbort && !silent) {
          addToast(`Failed to clear session: ${normalized.message}`, 'error')
        }
      }
    }

    hasBootstrappedRef.current = false
    setSession(createInitialSessionState())

    if (!silent) {
      addToast('Backend session cleared', 'success')
    }
  }, [addToast, client])

  const markUnauthorized = useCallback(() => {
    setSession((previous) => ({
      ...previous,
      checking: false,
      hasSession: false,
      hasAppAccess: false,
      auth: 'unauthorized',
      sessionSource: 'none',
      lastChecked: new Date().toISOString(),
    }))
  }, [])

  useEffect(() => {
    if (!config.baseUrl) {
      hasBootstrappedRef.current = false
      return
    }

    if (hasBootstrappedRef.current) {
      return
    }

    hasBootstrappedRef.current = true
    console.log('[SESSION] Bootstrap effect firing, baseUrl=' + configRef.current.baseUrl)

    // No abort controller — bootstrap MUST complete even under StrictMode
    // double-fire. The hasBootstrappedRef guard prevents duplicate calls.
    client.getBootstrap({ configOverride: configRef.current })
      .then((payload) => {
        console.log('[SESSION] Bootstrap resolved: has_session=' + payload.has_session + ' auth=' + payload.auth)
        setFrontendConfig(payload.frontend_config)
        setSession(normalizeBootstrap(payload))
      })
      .catch((err) => {
        const normalized = normalizeRequestError(err)
        console.log('[SESSION] Bootstrap FAILED: ' + normalized.message + ' status=' + normalized.status + ' isAbort=' + normalized.isAbort)
        if (normalized.isAbort) return
        log.warn('Bootstrap failed', { event: 'bootstrap_error', error: normalized.message })
        setSession({
          ...createInitialSessionState(),
          checking: false,
          auth: 'error',
          lastChecked: new Date().toISOString(),
          error: normalized,
        })
      })
  }, [config.baseUrl, client])

  // sessionReady: false while bootstrap hasn't completed yet.
  // If baseUrl is set, we MUST wait for bootstrap to resolve before deciding.
  // On first render, session is initial state (lastChecked=null, auth=null).
  // AuthRoute must NOT redirect during this window.
  const sessionReady = !config.baseUrl || Boolean(session.lastChecked) || session.auth === 'error' || session.auth === 'unauthorized'

  const value = useMemo(() => ({
    config,
    saveConfig,
    session,
    sessionReady,
    hasSession: session.hasSession,
    createSession,
    refreshSession,
    clearSession,
    markUnauthorized,
  }), [
    clearSession,
    config,
    createSession,
    markUnauthorized,
    refreshSession,
    saveConfig,
    session,
    sessionReady,
  ])

  return (
    <SessionContext.Provider value={value}>
      {children}
    </SessionContext.Provider>
  )
}

export function useSessionContext() {
  const context = useContext(SessionContext)

  if (!context) {
    throw new Error('useSessionContext must be used within a SessionProvider')
  }

  return context
}
