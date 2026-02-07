import { forwardRef } from 'react'
import { ChevronDown, ChevronUp, Copy, Check, ExternalLink, X, RefreshCw } from 'lucide-react'
import { useState } from 'react'
import { getLogger } from '../utils/logger'

const _uiLog = getLogger('ui')

// Card component
export function Card({ children, className = '', hover = false, ...props }) {
  return (
    <div 
      className={`
        bg-terminal-800 border border-terminal-600 rounded-lg
        ${hover ? 'card-hover cursor-pointer' : ''}
        ${className}
      `}
      {...props}
    >
      {children}
    </div>
  )
}

export function CardHeader({ children, className = '' }) {
  return (
    <div className={`px-4 py-3 border-b border-terminal-600 ${className}`}>
      {children}
    </div>
  )
}

export function CardTitle({ children, className = '' }) {
  return (
    <h3 className={`text-sm font-semibold text-terminal-100 ${className}`}>
      {children}
    </h3>
  )
}

export function CardContent({ children, className = '' }) {
  return (
    <div className={`p-4 ${className}`}>
      {children}
    </div>
  )
}

// Badge component
export function Badge({ children, variant = 'default', className = '' }) {
  const variants = {
    default: 'bg-terminal-600 text-terminal-200',
    success: 'bg-green-900/50 text-green-400 border border-green-700/50',
    error: 'bg-red-900/50 text-red-400 border border-red-700/50',
    warning: 'bg-amber-900/50 text-amber-400 border border-amber-700/50',
    info: 'bg-blue-900/50 text-blue-400 border border-blue-700/50',
    long: 'bg-signal-long/20 text-signal-long',
    short: 'bg-signal-short/20 text-signal-short',
  }

  return (
    <span className={`badge ${variants[variant] || variants.default} ${className}`}>
      {children}
    </span>
  )
}

// Direction badge
export function DirectionBadge({ direction }) {
  if (!direction) return <Badge>—</Badge>
  
  const d = direction.toLowerCase()
  const variant = d === 'long' ? 'long' : d === 'short' ? 'short' : 'default'
  
  return (
    <Badge variant={variant}>
      {direction.toUpperCase()}
    </Badge>
  )
}

// Status badge
export function StatusBadge({ status }) {
  if (!status) return <Badge>—</Badge>

  const variants = {
    PENDING: 'warning',
    QUEUED: 'warning',
    CLAIMED: 'info',
    RUNNING: 'info',
    SUCCESS: 'success',
    FAILED: 'error',
    CANCELED: 'default',
    OPEN: 'info',
    CLOSED: 'default',
    healthy: 'success',
    unhealthy: 'error',
  }

  return (
    <Badge variant={variants[status] || 'default'}>
      {status}
    </Badge>
  )
}

// Skeleton loader
export function Skeleton({ className = '', variant = 'text' }) {
  const variants = {
    text: 'h-4 w-full',
    title: 'h-6 w-3/4',
    card: 'h-32 w-full',
    circle: 'h-10 w-10 rounded-full',
    button: 'h-10 w-24',
  }

  return (
    <div className={`skeleton rounded ${variants[variant]} ${className}`} />
  )
}

export function SkeletonCard() {
  return (
    <Card>
      <CardContent className="space-y-3">
        <Skeleton variant="title" />
        <Skeleton />
        <Skeleton className="w-2/3" />
      </CardContent>
    </Card>
  )
}

// Empty state
export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      {Icon && (
        <div className="w-12 h-12 rounded-full bg-terminal-700 flex items-center justify-center mb-4">
          <Icon className="w-6 h-6 text-terminal-400" />
        </div>
      )}
      <h3 className="text-lg font-medium text-terminal-100 mb-1">{title}</h3>
      {description && (
        <p className="text-sm text-terminal-400 mb-4 max-w-sm">{description}</p>
      )}
      {action}
    </div>
  )
}

// Loading spinner
export function Spinner({ size = 'md', className = '' }) {
  const sizes = {
    sm: 'w-4 h-4',
    md: 'w-6 h-6',
    lg: 'w-8 h-8',
  }

  return (
    <svg 
      className={`animate-spin ${sizes[size]} ${className}`} 
      xmlns="http://www.w3.org/2000/svg" 
      fill="none" 
      viewBox="0 0 24 24"
    >
      <circle 
        className="opacity-25" 
        cx="12" 
        cy="12" 
        r="10" 
        stroke="currentColor" 
        strokeWidth="4"
      />
      <path 
        className="opacity-75" 
        fill="currentColor" 
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  )
}

// Copy button
export function CopyButton({ text, onCopy }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      onCopy?.()
      setTimeout(() => setCopied(false), 2000)
    } catch (err) {
      _uiLog.warn('Clipboard copy failed', { event: 'clipboard_error', error_message: err.message })
    }
  }

  return (
    <button
      onClick={handleCopy}
      className="btn btn-ghost btn-xs"
      title="Copy to clipboard"
    >
      {copied ? (
        <Check className="w-4 h-4 text-green-400" />
      ) : (
        <Copy className="w-4 h-4" />
      )}
    </button>
  )
}

// Expandable JSON
export function JsonView({ data, expanded = false }) {
  const [isExpanded, setIsExpanded] = useState(expanded)

  if (!data) return <span className="text-terminal-400">null</span>

  const jsonString = JSON.stringify(data, null, 2)

  return (
    <div className="relative">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex items-center gap-1 text-xs text-terminal-400 hover:text-terminal-200 mb-2"
      >
        {isExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        {isExpanded ? 'Collapse' : 'Expand'} JSON
      </button>
      
      {isExpanded && (
        <pre className="bg-terminal-900 border border-terminal-600 rounded p-3 text-xs font-mono text-terminal-200 overflow-x-auto">
          {jsonString}
        </pre>
      )}
    </div>
  )
}

// Data row component
export function DataRow({ label, value, className = '' }) {
  return (
    <div className={`flex justify-between items-center py-2 ${className}`}>
      <span className="text-sm text-terminal-400">{label}</span>
      <span className="text-sm text-terminal-100 font-mono">{value}</span>
    </div>
  )
}

// Modal
export function Modal({ isOpen, onClose, title, children, size = 'md' }) {
  if (!isOpen) return null

  const sizes = {
    sm: 'max-w-md',
    md: 'max-w-lg',
    lg: 'max-w-2xl',
    xl: 'max-w-4xl',
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div 
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className={`relative bg-terminal-800 border border-terminal-600 rounded-lg shadow-xl ${sizes[size]} w-full animate-fade-in`}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-600">
          <h2 className="text-lg font-semibold text-terminal-100">{title}</h2>
          <button
            onClick={onClose}
            className="text-terminal-400 hover:text-terminal-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-4">
          {children}
        </div>
      </div>
    </div>
  )
}

// Tabs
export function Tabs({ tabs, activeTab, onChange }) {
  return (
    <div className="flex gap-1 border-b border-terminal-600 mb-4">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          onClick={() => onChange(tab.value)}
          className={`
            px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
            ${activeTab === tab.value
              ? 'border-amber-500 text-amber-400'
              : 'border-transparent text-terminal-400 hover:text-terminal-200'
            }
          `}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className={`ml-2 text-xs px-1.5 py-0.5 rounded-full ${
              activeTab === tab.value ? 'bg-amber-600/20' : 'bg-terminal-600'
            }`}>
              {tab.count}
            </span>
          )}
        </button>
      ))}
    </div>
  )
}

// Toggle switch
export function Toggle({ enabled, onChange, label }) {
  return (
    <label className="flex items-center cursor-pointer">
      <div className="relative">
        <input
          type="checkbox"
          className="sr-only"
          checked={enabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        <div className={`toggle ${enabled ? 'toggle-enabled' : 'toggle-disabled'}`}>
          <span className={`toggle-knob ${enabled ? 'translate-x-5' : 'translate-x-1'}`} />
        </div>
      </div>
      {label && <span className="ml-3 text-sm text-terminal-200">{label}</span>}
    </label>
  )
}

// Select
export const Select = forwardRef(({ options, className = '', ...props }, ref) => {
  return (
    <select
      ref={ref}
      className={`bg-terminal-800 border border-terminal-500 rounded px-3 py-2 text-terminal-100 focus:border-amber-500 focus:ring-1 focus:ring-amber-500/20 focus:outline-none ${className}`}
      {...props}
    >
      {options.map((option) => (
        <option key={option.value} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  )
})

// Input
export const Input = forwardRef(({ className = '', ...props }, ref) => {
  return (
    <input
      ref={ref}
      className={`bg-terminal-800 border border-terminal-500 rounded px-3 py-2 text-terminal-100 placeholder-terminal-400 focus:border-amber-500 focus:ring-1 focus:ring-amber-500/20 focus:outline-none ${className}`}
      {...props}
    />
  )
})

// Stat card
export function StatCard({ label, value, change, trend, icon: Icon }) {
  return (
    <Card>
      <CardContent className="flex items-center justify-between">
        <div>
          <p className="text-sm text-terminal-400 mb-1">{label}</p>
          <p className="text-2xl font-bold text-terminal-100 font-mono">{value}</p>
          {change && (
            <p className={`text-xs mt-1 ${trend === 'up' ? 'text-signal-long' : trend === 'down' ? 'text-signal-short' : 'text-terminal-400'}`}>
              {change}
            </p>
          )}
        </div>
        {Icon && (
          <div className="w-10 h-10 rounded-lg bg-terminal-700 flex items-center justify-center">
            <Icon className="w-5 h-5 text-terminal-400" />
          </div>
        )}
      </CardContent>
    </Card>
  )
}

// Refresh button with timestamp
export function RefreshControl({ lastUpdated, onRefresh, loading, serverAsOf }) {
  return (
    <div className="flex items-center gap-3 text-sm">
      {serverAsOf && (
        <span className="text-terminal-500" title={`Server as_of_ts: ${serverAsOf}`}>
          Server: {new Date(serverAsOf).toLocaleTimeString()}
        </span>
      )}
      {lastUpdated && (
        <span className="text-terminal-400">
          Polled: {new Date(lastUpdated).toLocaleTimeString()}
        </span>
      )}
      <button
        onClick={onRefresh}
        disabled={loading}
        className="btn btn-ghost btn-xs"
      >
        <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
      </button>
    </div>
  )
}
