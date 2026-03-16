import { createAbortError } from './errors'

const IS_DEV = import.meta.env.DEV

export function getDefaultChaosMode() {
  return { status: null, latency: 0 }
}

export function normalizeChaosMode(value) {
  if (!IS_DEV) {
    return null
  }

  const latency = Number(value?.latency)

  return {
    status: value?.status ?? null,
    latency: Number.isFinite(latency) && latency > 0 ? latency : 0,
  }
}

export function normalizeFixtureState(value) {
  if (!IS_DEV) {
    return 'live'
  }

  if (typeof value !== 'string') {
    return 'curated'
  }

  const nextValue = value.trim()
  return nextValue || 'curated'
}

function getChaosLatencyBucket(latency) {
  if (latency > 3000) return 5000
  if (latency > 1000) return 2000
  return 500
}

export function decorateDevHeaders(headers = {}, overrides = {}) {
  if (!IS_DEV) {
    return headers
  }

  const nextHeaders = { ...headers }
  const chaosMode = normalizeChaosMode(overrides.chaosMode)
  const fixtureState = normalizeFixtureState(overrides.fixtureState)

  if (chaosMode?.status) {
    nextHeaders['X-Chaos-Status'] = String(chaosMode.status)
  }

  if (chaosMode?.latency > 0) {
    nextHeaders['X-Chaos-Latency'] = String(getChaosLatencyBucket(chaosMode.latency))
  }

  if (fixtureState) {
    nextHeaders['X-Fixture-State'] = fixtureState
  }

  return nextHeaders
}

export async function applyDevRequestDelay(overrides = {}, signal) {
  if (!IS_DEV) {
    return
  }

  const chaosMode = normalizeChaosMode(overrides.chaosMode)
  const latency = chaosMode?.latency || 0

  if (!latency) {
    return
  }

  if (signal?.aborted) {
    throw createAbortError()
  }

  await new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      cleanup()
      resolve()
    }, latency)

    const handleAbort = () => {
      cleanup()
      reject(createAbortError())
    }

    const cleanup = () => {
      window.clearTimeout(timer)
      signal?.removeEventListener('abort', handleAbort)
    }

    signal?.addEventListener('abort', handleAbort, { once: true })
  })
}
