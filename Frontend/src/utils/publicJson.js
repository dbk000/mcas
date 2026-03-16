import { useCallback, useEffect, useRef, useState } from 'react'
import { normalizeRequestError } from '../api/errors'
import { requestPublicJson } from '../api/publicJsonClient'

export function usePublicJson(path, initialData = null) {
  const [data, setData] = useState(initialData)
  const [loading, setLoading] = useState(Boolean(path) && initialData === null)
  const [error, setError] = useState(null)
  const [errorState, setErrorState] = useState(null)
  const abortRef = useRef(null)
  const initialDataRef = useRef(initialData)

  useEffect(() => {
    initialDataRef.current = initialData
  }, [initialData, path])

  const cancel = useCallback(() => {
    abortRef.current?.abort()
    abortRef.current = null
  }, [])

  const load = useCallback(async () => {
    if (!path) {
      setLoading(false)
      return null
    }

    cancel()

    const controller = new AbortController()
    abortRef.current = controller

    setLoading(true)
    setError(null)
    setErrorState(null)

    try {
      const json = await requestPublicJson(path, {
        signal: controller.signal,
      })

      if (abortRef.current !== controller || controller.signal.aborted) {
        return null
      }

      setData(json)
      return json
    } catch (error) {
      const normalized = normalizeRequestError(error)

      if (normalized.isAbort || abortRef.current !== controller) {
        return null
      }

      setError(normalized.message)
      setErrorState(normalized)
      return null
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null
        setLoading(false)
      }
    }
  }, [cancel, path])

  useEffect(() => {
    setData(initialDataRef.current)
    setLoading(Boolean(path) && initialDataRef.current === null)
    setError(null)
    setErrorState(null)
    cancel()
  }, [cancel, path])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => cancel, [cancel])

  return { data, loading, error, errorState, refetch: load, cancel }
}
