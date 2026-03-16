import { setupWorker } from 'msw/browser'
import { initializeHandlers } from './handlers'

export const startWorker = async () => {
  const handlers = await initializeHandlers()
  const worker = setupWorker(...handlers)
  return worker.start({
    onUnhandledRequest: 'bypass',
  })
}
