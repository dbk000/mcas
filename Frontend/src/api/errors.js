const ABORT_MESSAGE = 'Request aborted'

export class RequestError extends Error {
  constructor(message, options = {}) {
    super(message)

    this.name = options.isAbort ? 'AbortError' : 'RequestError'
    this.kind = options.kind || 'unknown'
    this.code = options.code || null
    this.status = options.status ?? null
    this.detail = options.detail ?? null
    this.method = options.method || null
    this.endpoint = options.endpoint || null
    this.url = options.url || null
    this.payload = options.payload ?? null
    this.isAbort = Boolean(options.isAbort)

    if (options.cause !== undefined) {
      this.cause = options.cause
    }
  }
}

function isAbortError(error) {
  return Boolean(
    error?.isAbort ||
    error?.name === 'AbortError' ||
    error?.code === 'ABORT_ERR' ||
    error?.code === 20
  )
}

export function createAbortError(message = ABORT_MESSAGE, context = {}) {
  return new RequestError(message, {
    ...context,
    kind: 'abort',
    code: 'ABORT_ERR',
    isAbort: true,
  })
}

export function createConfigurationError(message, context = {}) {
  return new RequestError(message, {
    ...context,
    kind: 'configuration',
    code: 'API_CONFIG_INVALID',
  })
}

export function createParseError(message, context = {}) {
  return new RequestError(message, {
    ...context,
    kind: 'parse',
    code: 'INVALID_JSON',
  })
}

function readPayloadMessage(payload) {
  if (!payload) return null
  if (typeof payload === 'string') return payload
  if (typeof payload.detail === 'string' && payload.detail.trim()) return payload.detail
  if (typeof payload.error === 'string' && payload.error.trim()) return payload.error
  if (typeof payload.message === 'string' && payload.message.trim()) return payload.message
  return null
}

export function createHttpError({ response, payload, context = {} }) {
  const detail = readPayloadMessage(payload) || `HTTP ${response.status}`

  return new RequestError(detail, {
    ...context,
    kind: 'http',
    code: `HTTP_${response.status}`,
    status: response.status,
    detail,
    payload,
  })
}

export function normalizeRequestError(error, context = {}) {
  if (error instanceof RequestError) {
    return error
  }

  if (isAbortError(error)) {
    return createAbortError(ABORT_MESSAGE, { ...context, cause: error })
  }

  if (error instanceof TypeError) {
    return new RequestError(error.message || 'Network request failed', {
      ...context,
      kind: 'network',
      code: 'NETWORK_ERROR',
      cause: error,
    })
  }

  if (error instanceof Error) {
    return new RequestError(error.message || 'Unexpected request failure', {
      ...context,
      kind: 'unknown',
      code: 'UNKNOWN_ERROR',
      cause: error,
    })
  }

  return new RequestError('Unexpected request failure', {
    ...context,
    kind: 'unknown',
    code: 'UNKNOWN_ERROR',
    cause: error,
  })
}
