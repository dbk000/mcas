import { getCorrelationId } from '../utils/logger'
import { createConfigurationError } from './errors'
import { fetchJson, sanitizeApiConfig } from './requestClient'

function buildUrl(config, endpoint) {
  if (!config.baseUrl) {
    throw createConfigurationError('API base URL is not configured', { endpoint })
  }

  return `${config.baseUrl}${endpoint}`
}

export function createSessionClient({ getConfig, fetchImpl = fetch } = {}) {
  async function request(endpoint, options = {}) {
    const {
      method = 'GET',
      body,
      headers = {},
      signal,
      configOverride,
      responseType = 'json',
    } = options
    const config = sanitizeApiConfig(configOverride ?? getConfig?.())
    const url = buildUrl(config, endpoint)

    return fetchJson(url, {
      method,
      body,
      signal,
      fetchImpl,
      credentials: 'include',
      responseType,
      headers: {
        Accept: 'application/json',
        ...(body !== undefined && method !== 'GET' && method !== 'HEAD'
          ? { 'Content-Type': 'application/json' }
          : {}),
        'X-Correlation-ID': getCorrelationId(),
        ...headers,
      },
      context: { endpoint, url },
    })
  }

  return {
    getBootstrap(options = {}) {
      return request('/v1/session/bootstrap', options)
    },
    createSession(apiKey, options = {}) {
      return request('/v1/session', {
        ...options,
        method: 'POST',
        body: { api_key: apiKey },
      })
    },
    clearSession(options = {}) {
      return request('/v1/session', {
        ...options,
        method: 'DELETE',
      })
    },
    getHealth(options = {}) {
      return request('/health', {
        ...options,
        headers: options.headers,
      })
    },
  }
}
