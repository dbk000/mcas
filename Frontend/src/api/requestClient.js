import {
  RequestError,
  createHttpError,
  createParseError,
  normalizeRequestError,
} from './errors'

function normalizeBaseUrl(url) {
  if (!url) return ''
  return String(url).trim().replace(/\/+$/, '')
}

export function sanitizeApiConfig(config = {}) {
  return {
    baseUrl: normalizeBaseUrl(config.baseUrl),
    apiKey: '',
  }
}

export function createInitialConnectionStatus() {
  return {
    health: null,
    auth: null,
    redisMs: null,
    pgMs: null,
    lastChecked: null,
    checking: false,
  }
}

function buildSearchParams(params = {}) {
  const searchParams = new URLSearchParams()

  Object.keys(params)
    .sort()
    .forEach((key) => {
      const value = params[key]

      if (value === undefined || value === null || value === '') {
        return
      }

      if (Array.isArray(value)) {
        value.forEach((item) => {
          if (item !== undefined && item !== null && item !== '') {
            searchParams.append(key, String(item))
          }
        })
        return
      }

      searchParams.append(key, String(value))
    })

  return searchParams
}

export function buildEndpointWithParams(endpoint, params = {}) {
  const queryString = buildSearchParams(params).toString()
  return queryString ? `${endpoint}?${queryString}` : endpoint
}

function shouldSendJsonBody(method, body) {
  return body !== undefined && method !== 'GET' && method !== 'HEAD'
}

async function parseResponseBody(response, context) {
  if (response.status === 204) {
    return null
  }

  const contentType = response.headers.get('content-type') || ''

  if (contentType.includes('application/json')) {
    try {
      return await response.json()
    } catch (error) {
      throw createParseError('Failed to parse server response', {
        ...context,
        cause: error,
      })
    }
  }

  const text = await response.text()
  if (!text) {
    return null
  }

  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function unwrapEnvelope(payload) {
  if (payload && typeof payload === 'object' && payload.data !== undefined) {
    const unwrapped = payload.data

    if (payload.meta && unwrapped != null && typeof unwrapped === 'object') {
      Object.defineProperty(unwrapped, '__meta', {
        value: payload.meta,
        enumerable: false,
        configurable: true,
      })
    }

    return unwrapped
  }

  return payload
}

export async function fetchJson(url, options = {}) {
  const {
    method = 'GET',
    headers = {},
    body,
    signal,
    fetchImpl = fetch,
    credentials,
    responseType = 'json',
    unwrapResponseEnvelope = true,
    context = {},
  } = options

  const requestContext = {
    ...context,
    method,
    url,
  }

  const requestInit = {
    method,
    headers,
    signal,
    ...(credentials ? { credentials } : {}),
  }

  if (shouldSendJsonBody(method, body)) {
    requestInit.body = typeof body === 'string' ? body : JSON.stringify(body)
  }

  try {
    const response = await fetchImpl(url, requestInit)

    if (responseType === 'raw') {
      if (!response.ok) {
        const payload = await parseResponseBody(response, requestContext)
        throw createHttpError({ response, payload, context: requestContext })
      }
      return response
    }

    const payload = await parseResponseBody(response, requestContext)

    if (!response.ok) {
      throw createHttpError({ response, payload, context: requestContext })
    }

    if (responseType === 'text') {
      return typeof payload === 'string' ? payload : payload == null ? '' : JSON.stringify(payload)
    }

    return unwrapResponseEnvelope ? unwrapEnvelope(payload) : payload
  } catch (error) {
    if (error instanceof RequestError) {
      throw error
    }

    throw normalizeRequestError(error, requestContext)
  }
}
