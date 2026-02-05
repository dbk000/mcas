import { createContext, useContext, useState, useEffect, useCallback, useRef } from 'react'
import { useToast } from './ToastContext'
import { getLogger, getCorrelationId } from '../utils/logger'

const log = getLogger('api')

const ApiContext = createContext(null)

const STORAGE_KEY = 'mcas_api_config'

// Normalize baseUrl: trim whitespace, remove trailing slashes
function normalizeBaseUrl(url) {
  if (!url) return ''
  return url.trim().replace(/\/+$/, '')
}

export function ApiProvider({ children }) {
  const { addToast } = useToast()
  const hasAutoConnected = useRef(false)

  const [config, setConfig] = useState(() => {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved) {
      try {
        const parsed = JSON.parse(saved)
        const cfg = {
          baseUrl: normalizeBaseUrl(parsed.baseUrl || ''),
          apiKey: (parsed.apiKey || '').trim(),
        }
        log.info('Config loaded', { event: 'config_loaded', has_url: !!cfg.baseUrl, has_key: !!cfg.apiKey })
        return cfg
      } catch {
        log.warn('Config parse error', { event: 'config_parse_error' })
        return { baseUrl: '', apiKey: '' }
      }
    }
    return { baseUrl: '', apiKey: '' }
  })

  const [connectionStatus, setConnectionStatus] = useState({
    health: null,       // 'healthy' | 'unhealthy' | 'error' | null
    auth: null,         // 'ok' | 'unauthorized' | 'error' | null
    redisMs: null,
    pgMs: null,
    lastChecked: null,
    checking: false,
  })

  // Save config to localStorage
  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config))
  }, [config])

  // Update config with normalization
  const updateConfig = useCallback((newConfig) => {
    log.info('Config updated', { event: 'config_updated' })
    setConfig(prev => ({
      ...prev,
      ...newConfig,
      // Always normalize baseUrl on update
      baseUrl: newConfig.baseUrl !== undefined
        ? normalizeBaseUrl(newConfig.baseUrl)
        : prev.baseUrl,
      apiKey: newConfig.apiKey !== undefined
        ? newConfig.apiKey.trim()
        : prev.apiKey,
    }))
    // Reset status when config changes
    setConnectionStatus({
      health: null,
      auth: null,
      redisMs: null,
      pgMs: null,
      lastChecked: null,
      checking: false,
    })
    // Allow auto-connect to run again after config change
    hasAutoConnected.current = false
  }, [])

  // Test connection (with optional silent mode for auto-connect)
  const testConnection = useCallback(async (silent = false) => {
    if (!config.baseUrl) {
      if (!silent) addToast('Please enter an API base URL', 'error')
      return false
    }

    log.info('Connection test', { event: 'connection_test_start', silent })
    setConnectionStatus(prev => ({ ...prev, checking: true }))

    try {
      // Test /health (unprotected)
      const healthRes = await fetch(`${config.baseUrl}/health`, {
        method: 'GET',
        headers: { 'Content-Type': 'application/json' },
      })

      let healthData = null
      let healthStatus = 'unhealthy'

      if (healthRes.ok) {
        healthData = await healthRes.json()
        healthStatus = healthData.status === 'healthy' ? 'healthy' : 'unhealthy'
      }

      // Test /v1/auth/ping (protected)
      let authStatus = 'unauthorized'

      if (config.apiKey) {
        try {
          const authRes = await fetch(`${config.baseUrl}/v1/auth/ping`, {
            method: 'GET',
            headers: {
              'Content-Type': 'application/json',
              'X-API-Key': config.apiKey,
            },
          })

          if (authRes.ok) {
            authStatus = 'ok'
          } else if (authRes.status === 401) {
            authStatus = 'unauthorized'
          }
        } catch (e) {
          authStatus = 'error'
        }
      }

      setConnectionStatus({
        health: healthStatus,
        auth: authStatus,
        redisMs: healthData?.redis_ms ?? null,
        pgMs: healthData?.pg_ms ?? null,
        lastChecked: new Date().toISOString(),
        checking: false,
      })

      if (healthStatus === 'healthy' && authStatus === 'ok') {
        log.info('Connection OK', {
          event: 'connection_test_success',
          health: healthStatus,
          auth: authStatus,
          redis_ms: healthData?.redis_ms,
          pg_ms: healthData?.pg_ms,
        })
        if (!silent) addToast('Connection successful', 'success')
        return true
      } else if (healthStatus === 'unhealthy') {
        log.warn('Connection unhealthy', { event: 'connection_test_unhealthy' })
        if (!silent) addToast('Backend unhealthy', 'error')
        return false
      } else if (authStatus === 'unauthorized') {
        log.warn('Connection unauthorized', { event: 'connection_test_unauthorized' })
        if (!silent) addToast('Invalid API key', 'error')
        return false
      }

      return false
    } catch (error) {
      log.error('Connection test failed', { event: 'connection_test_error', error_message: error.message })
      setConnectionStatus({
        health: 'error',
        auth: null,
        redisMs: null,
        pgMs: null,
        lastChecked: new Date().toISOString(),
        checking: false,
      })
      if (!silent) addToast(`Connection failed: ${error.message}`, 'error')
      return false
    }
  }, [config, addToast])

  // Auto-connect on startup if config exists
  useEffect(() => {
    if (config.baseUrl && config.apiKey && !hasAutoConnected.current && !connectionStatus.checking) {
      hasAutoConnected.current = true
      log.info('Auto-connecting', { event: 'auto_connect' })
      testConnection(true) // Silent mode - no toasts on startup
    }
  }, [config.baseUrl, config.apiKey, connectionStatus.checking, testConnection])

  // API request helper
  const apiRequest = useCallback(async (endpoint, options = {}) => {
    if (!config.baseUrl || !config.apiKey) {
      throw new Error('API not configured')
    }

    const url = `${config.baseUrl}${endpoint}`
    const method = options.method || 'GET'
    const headers = {
      'Content-Type': 'application/json',
      'X-API-Key': config.apiKey,
      'X-Correlation-ID': getCorrelationId(),
      ...options.headers,
    }

    const t0 = performance.now()

    try {
      const response = await fetch(url, {
        ...options,
        headers,
      })

      const duration_ms = Math.round(performance.now() - t0)

      if (response.status === 401) {
        log.warn('API auth failure', { event: 'api_auth_failure', path: endpoint })
        setConnectionStatus(prev => ({ ...prev, auth: 'unauthorized' }))
        throw new Error('Unauthorized')
      }

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}))
        const detail = errorData.detail || `HTTP ${response.status}`
        log.warn('API error response', { event: 'api_error', path: endpoint, status: response.status, detail })
        throw new Error(detail)
      }

      const data = await response.json()

      log.debug('API request', { event: 'api_request', method, path: endpoint, status: response.status, duration_ms })

      // Handle envelope vs plain response
      if (data.data !== undefined) {
        return data.data
      }
      return data
    } catch (error) {
      const duration_ms = Math.round(performance.now() - t0)
      // Only log network errors (not re-thrown HTTP errors already logged above)
      if (error.message !== 'Unauthorized' && !error.message.startsWith('HTTP ')) {
        log.error('API request failed', { event: 'api_network_error', path: endpoint, error_message: error.message, duration_ms })
      }
      throw error
    }
  }, [config])

  // GET helper
  const get = useCallback((endpoint, params = {}) => {
    const searchParams = new URLSearchParams()
    Object.entries(params).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') {
        searchParams.append(key, value)
      }
    })
    const queryString = searchParams.toString()
    const url = queryString ? `${endpoint}?${queryString}` : endpoint
    return apiRequest(url)
  }, [apiRequest])

  // POST helper
  const post = useCallback((endpoint, body = {}) => {
    return apiRequest(endpoint, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }, [apiRequest])

  const isConnected = connectionStatus.health === 'healthy' && connectionStatus.auth === 'ok'

  return (
    <ApiContext.Provider value={{
      config,
      updateConfig,
      connectionStatus,
      testConnection,
      isConnected,
      get,
      post,
    }}>
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
