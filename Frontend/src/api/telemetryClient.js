import { sanitizeApiConfig } from './requestClient'

function buildTelemetryUrl(config) {
  return config.baseUrl ? `${config.baseUrl}/v1/telemetry/frontend-logs` : ''
}

export function createTelemetryClient({ getConfig, fetchImpl = fetch } = {}) {
  async function shipBatch(payload) {
    if (!payload?.entries?.length) {
      return false
    }

    const config = sanitizeApiConfig(getConfig?.())
    const url = buildTelemetryUrl(config)

    if (!url) {
      return false
    }

    await fetchImpl(url, {
      method: 'POST',
      credentials: 'include',
      keepalive: true,
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    })

    return true
  }

  function sendBeaconBatch(payload) {
    if (
      !payload?.entries?.length ||
      typeof navigator === 'undefined' ||
      typeof navigator.sendBeacon !== 'function'
    ) {
      return false
    }

    const config = sanitizeApiConfig(getConfig?.())
    const url = buildTelemetryUrl(config)

    if (!url) {
      return false
    }

    return navigator.sendBeacon(url, JSON.stringify(payload))
  }

  return {
    shipBatch,
    sendBeaconBatch,
  }
}
