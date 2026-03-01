import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { 
  Settings as SettingsIcon, 
  Server, 
  Key, 
  CheckCircle, 
  XCircle, 
  Wifi,
  Database,
  RefreshCw
} from 'lucide-react'
import { useApi } from '../context/ApiContext'
import { Card, CardHeader, CardTitle, CardContent, Badge, Spinner } from '../components/ui'
import { getLogger } from '../utils/logger'

const log = getLogger('settings')

export default function Settings() {
  const navigate = useNavigate()
  const { config, updateConfig, connectionStatus, testConnection, isConnected } = useApi()
  
  const [baseUrl, setBaseUrl] = useState(config.baseUrl)
  // Never pre-populate the actual API key — only show that one is saved
  const hasExistingKey = !!config.apiKey
  const [apiKey, setApiKey] = useState('')
  const [keyTouched, setKeyTouched] = useState(false)
  const [testing, setTesting] = useState(false)

  const handleSave = () => {
    log.info('Settings saved', { event: 'settings_saved' })
    // Only update API key if the user actually typed a new one
    const updates = { baseUrl }
    if (keyTouched && apiKey) {
      updates.apiKey = apiKey
    }
    updateConfig(updates)
  }

  const handleTest = async () => {
    log.info('Connection test initiated', { event: 'settings_test_start' })
    // Save first — only update key if user typed a new one
    const updates = { baseUrl }
    if (keyTouched && apiKey) {
      updates.apiKey = apiKey
    }
    updateConfig(updates)
    setTesting(true)
    const success = await testConnection()
    setTesting(false)

    if (success) {
      log.info('Connection test succeeded', { event: 'settings_test_success' })
      // Navigate to dashboard after successful connection
      setTimeout(() => navigate('/'), 500)
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-lg bg-terminal-700 flex items-center justify-center">
          <SettingsIcon className="w-5 h-5 text-amber-400" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-terminal-100">Settings</h1>
          <p className="text-sm text-terminal-400">Configure API connection</p>
        </div>
      </div>

      {/* Connection config */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Server className="w-4 h-4" />
            API Connection
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Base URL */}
          <div>
            <label className="block text-sm font-medium text-terminal-200 mb-2">
              API Base URL
            </label>
            <input
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="https://api.example.com"
              className="w-full"
            />
            <p className="text-xs text-terminal-500 mt-1">
              The base URL of your MCAS backend API
            </p>
          </div>

          {/* API Key */}
          <div>
            <label className="block text-sm font-medium text-terminal-200 mb-2">
              <div className="flex items-center gap-2">
                <Key className="w-4 h-4" />
                API Key
              </div>
            </label>
            <div className="relative">
              <input
                type="password"
                value={apiKey}
                onChange={(e) => { setApiKey(e.target.value); setKeyTouched(true) }}
                placeholder={hasExistingKey && !keyTouched ? '••••••••••••••••  (key saved)' : 'Enter your API key'}
                autoComplete="off"
                data-1p-ignore
                className="w-full"
              />
            </div>
            <p className="text-xs text-terminal-500 mt-1">
              {hasExistingKey && !keyTouched
                ? 'API key is saved. Type a new key to replace it.'
                : 'Your API key is stored locally and never transmitted except to your backend'
              }
            </p>
          </div>

          {/* Test button */}
          <div className="flex gap-3 pt-4">
            <button
              onClick={handleTest}
              disabled={testing || !baseUrl}
              className="btn btn-primary flex-1"
            >
              {testing ? (
                <>
                  <Spinner size="sm" />
                  Testing...
                </>
              ) : (
                <>
                  <Wifi className="w-4 h-4" />
                  Test Connection
                </>
              )}
            </button>
            <button
              onClick={handleSave}
              className="btn btn-secondary"
            >
              Save
            </button>
          </div>
        </CardContent>
      </Card>

      {/* Connection status */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Database className="w-4 h-4" />
            Connection Status
          </CardTitle>
        </CardHeader>
        <CardContent>
          {connectionStatus.checking ? (
            <div className="flex items-center justify-center py-8">
              <Spinner size="lg" className="text-amber-400" />
            </div>
          ) : connectionStatus.lastChecked ? (
            <div className="space-y-4">
              {/* Health */}
              <div className="flex items-center justify-between py-2 border-b border-terminal-600">
                <span className="text-terminal-300">Backend Health</span>
                <StatusIndicator 
                  status={connectionStatus.health} 
                  labels={{ healthy: 'Healthy', unhealthy: 'Unhealthy', null: 'Unknown' }}
                />
              </div>

              {/* Auth */}
              <div className="flex items-center justify-between py-2 border-b border-terminal-600">
                <span className="text-terminal-300">Authentication</span>
                <StatusIndicator 
                  status={connectionStatus.auth} 
                  labels={{ ok: 'Authorized', unauthorized: 'Unauthorized', null: 'Not tested' }}
                />
              </div>

              {/* Latency */}
              {connectionStatus.redisMs !== null && (
                <div className="flex items-center justify-between py-2 border-b border-terminal-600">
                  <span className="text-terminal-300">Redis Latency</span>
                  <span className="font-mono text-terminal-100">{connectionStatus.redisMs}ms</span>
                </div>
              )}

              {connectionStatus.pgMs !== null && (
                <div className="flex items-center justify-between py-2 border-b border-terminal-600">
                  <span className="text-terminal-300">PostgreSQL Latency</span>
                  <span className="font-mono text-terminal-100">{connectionStatus.pgMs}ms</span>
                </div>
              )}

              {/* Last checked */}
              <div className="flex items-center justify-between py-2">
                <span className="text-terminal-300">Last Checked</span>
                <span className="text-terminal-400 text-sm">
                  {new Date(connectionStatus.lastChecked).toLocaleString()}
                </span>
              </div>

              {/* Overall status */}
              <div className="mt-4 p-4 rounded-lg border border-terminal-600 bg-terminal-900">
                {isConnected ? (
                  <div className="flex items-center gap-3 text-green-400">
                    <CheckCircle className="w-5 h-5" />
                    <span className="font-medium">Connected and ready</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-3 text-red-400">
                    <XCircle className="w-5 h-5" />
                    <span className="font-medium">
                      {connectionStatus.health === 'unhealthy' 
                        ? 'Backend is unhealthy' 
                        : connectionStatus.auth === 'unauthorized'
                          ? 'Invalid API key'
                          : 'Not connected'
                      }
                    </span>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-center py-8 text-terminal-400">
              <Wifi className="w-8 h-8 mx-auto mb-3 opacity-50" />
              <p>No connection test performed yet</p>
              <p className="text-sm mt-1">Click "Test Connection" to check your configuration</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Help */}
      <Card>
        <CardContent>
          <h4 className="text-sm font-medium text-terminal-200 mb-2">Need help?</h4>
          <p className="text-sm text-terminal-400">
            Make sure your MCAS backend is running and accessible. The API key is configured
            via the <code className="text-amber-400 bg-terminal-700 px-1 rounded">API_KEY</code> environment
            variable on your backend.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

function StatusIndicator({ status, labels }) {
  const isOk = status === 'healthy' || status === 'ok'
  const label = labels[status] || labels.null || 'Unknown'

  return (
    <div className="flex items-center gap-2">
      {isOk ? (
        <CheckCircle className="w-4 h-4 text-green-400" />
      ) : status ? (
        <XCircle className="w-4 h-4 text-red-400" />
      ) : (
        <div className="w-4 h-4 rounded-full bg-terminal-500" />
      )}
      <span className={`text-sm ${isOk ? 'text-green-400' : status ? 'text-red-400' : 'text-terminal-400'}`}>
        {label}
      </span>
    </div>
  )
}
