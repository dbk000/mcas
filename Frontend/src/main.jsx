import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App.jsx'
import { ApiProvider } from './context/ApiContext'
import { ConnectionProvider } from './context/ConnectionContext'
import { SessionProvider } from './context/SessionContext'
import { SubscriptionProvider } from './context/SubscriptionContext'
import { ToastProvider } from './context/ToastContext'
import { getLogger } from './utils/logger'
import './index.css'

const log = getLogger('main')

window.addEventListener('error', (event) => {
  log.error('Unhandled error', {
    event: 'unhandled_error',
    error_message: event.message,
    filename: event.filename,
    lineno: event.lineno,
    colno: event.colno,
  })
})

window.addEventListener('unhandledrejection', (event) => {
  log.error('Unhandled promise rejection', {
    event: 'unhandled_rejection',
    reason: String(event.reason),
  })
})

log.info('App starting', { event: 'app_start' })

// Apply saved theme before first paint to prevent flash
try {
  const savedTheme = localStorage.getItem('mcas_theme')
  if (savedTheme) document.documentElement.setAttribute('data-theme', savedTheme)
} catch { /* noop */ }

async function enableMocking() {
  // STRICT: Do not even attempt import in production
  if (!import.meta.env.DEV) {
    return
  }

  // Skip MSW when testing against real backend (?nomock in URL or localStorage flag)
  const params = new URLSearchParams(window.location.search)
  if (params.has('nomock')) {
    localStorage.setItem('mcas_nomock', '1')
  }
  if (localStorage.getItem('mcas_nomock') === '1') {
    console.log('[MSW] Disabled — real backend mode (clear with localStorage.removeItem("mcas_nomock"))')
    return
  }

  const { startWorker } = await import('./mocks/browser')
  return startWorker()
}

enableMocking().then(() => {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <BrowserRouter>
        <ToastProvider>
          <SessionProvider>
            <ConnectionProvider>
              <ApiProvider>
                <SubscriptionProvider>
                  <App />
                </SubscriptionProvider>
              </ApiProvider>
            </ConnectionProvider>
          </SessionProvider>
        </ToastProvider>
      </BrowserRouter>
    </React.StrictMode>,
  )
})
