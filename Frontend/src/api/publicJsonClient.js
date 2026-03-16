import { fetchJson } from './requestClient'

export function createPublicJsonClient({ fetchImpl = fetch } = {}) {
  return {
    get(path, options = {}) {
      return fetchJson(path, {
        fetchImpl,
        signal: options.signal,
        headers: {
          Accept: 'application/json',
          ...options.headers,
        },
        unwrapResponseEnvelope: false,
        context: { endpoint: path, path },
      })
    },
  }
}

const publicJsonClient = createPublicJsonClient()

export function requestPublicJson(path, options = {}) {
  return publicJsonClient.get(path, options)
}
