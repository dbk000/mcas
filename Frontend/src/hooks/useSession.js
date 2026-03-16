import { useSessionContext } from '../context/SessionContext'

export function useSession() {
  return useSessionContext()
}
