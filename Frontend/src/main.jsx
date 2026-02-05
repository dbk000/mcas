import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { ApiProvider } from './context/ApiContext'
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

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <ToastProvider>
        <ApiProvider>
          <App />
        </ApiProvider>
      </ToastProvider>
    </BrowserRouter>
  </React.StrictMode>,
)
