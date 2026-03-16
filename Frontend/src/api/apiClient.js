import { getCorrelationId } from '../utils/logger'
import { decorateDevHeaders, applyDevRequestDelay } from './devOverrides'
import { createConfigurationError, normalizeRequestError } from './errors'
import {
  buildEndpointWithParams,
  fetchJson,
  sanitizeApiConfig,
} from './requestClient'

function shouldSendJsonBody(method, body) {
  return body !== undefined && method !== 'GET' && method !== 'HEAD'
}

export function createApiClient({
  getConfig,
  getDevOverrides,
  onUnauthorized,
  fetchImpl = fetch,
} = {}) {
  async function request(endpoint, options = {}) {
    const {
      method = 'GET',
      params = {},
      body,
      headers = {},
      signal,
      configOverride,
      responseType = 'json',
    } = options

    const config = sanitizeApiConfig(configOverride ?? getConfig?.())

    if (!config.baseUrl) {
      throw createConfigurationError('API base URL is not configured', { endpoint, method })
    }

    const path = buildEndpointWithParams(endpoint, params)
    const url = `${config.baseUrl}${path}`
    const devOverrides = getDevOverrides?.() || null

    await applyDevRequestDelay(devOverrides, signal)

    const requestHeaders = decorateDevHeaders(
      {
        Accept: 'application/json',
        ...(shouldSendJsonBody(method, body) ? { 'Content-Type': 'application/json' } : {}),
        'X-Correlation-ID': getCorrelationId(),
        ...headers,
      },
      devOverrides
    )

    return fetchJson(url, {
      method,
      headers: requestHeaders,
      body,
      signal,
      fetchImpl,
      credentials: 'include',
      responseType,
      context: { endpoint, path },
    })
  }

  async function requestWithUnauthorizedTracking(endpoint, options = {}) {
    try {
      return await request(endpoint, options)
    } catch (error) {
      const normalized = normalizeRequestError(error, {
        endpoint,
        method: options.method || 'GET',
      })

      if (normalized.status === 401) {
        onUnauthorized?.(normalized)
      }

      throw normalized
    }
  }

  return {
    request: requestWithUnauthorizedTracking,
    get(endpoint, params = {}, options = {}) {
      return requestWithUnauthorizedTracking(endpoint, {
        ...options,
        method: 'GET',
        params,
      })
    },
    post(endpoint, body = {}, options = {}) {
      return requestWithUnauthorizedTracking(endpoint, {
        ...options,
        method: 'POST',
        body,
      })
    },
  }
}
