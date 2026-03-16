import { useConnectionContext } from '../context/ConnectionContext'

export function useConnectionStatus() {
  return useConnectionContext()
}
