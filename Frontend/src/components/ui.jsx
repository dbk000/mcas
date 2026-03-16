import { forwardRef, useEffect, useId, useRef, useState } from 'react'
import { ChevronDown, ChevronUp, X, RefreshCw } from 'lucide-react'

// Card component
export function Card({ children, className = '', hover = false, ...props }) {
  return (
    <div 
      className={`
        rounded-lg border border-terminal-600 bg-terminal-800
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
    <div className={`border-b border-terminal-600 px-4 py-3 ${className}`}>
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
        <div className="mb-4 flex size-12 items-center justify-center rounded-full bg-terminal-700">
          <Icon className="size-6 text-terminal-400" />
        </div>
      )}
      <h3 className="mb-1 text-lg font-medium text-terminal-100">{title}</h3>
      {description && (
        <p className="mb-4 max-w-sm text-sm text-terminal-400">{description}</p>
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

// Expandable JSON
export function JsonView({ data, expanded = false }) {
  const [isExpanded, setIsExpanded] = useState(expanded)

  if (!data) return <span className="text-terminal-400">null</span>

  const jsonString = JSON.stringify(data, null, 2)

  return (
    <div className="relative">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="mb-2 flex items-center gap-1 text-xs text-terminal-400 hover:text-terminal-200"
      >
        {isExpanded ? <ChevronUp className="size-3" /> : <ChevronDown className="size-3" />}
        {isExpanded ? 'Collapse' : 'Expand'} JSON
      </button>
      
      {isExpanded && (
        <pre className="overflow-x-auto rounded border border-terminal-600 bg-terminal-900 p-3 font-mono text-xs text-terminal-200">
          {jsonString}
        </pre>
      )}
    </div>
  )
}

// Data row component
export function DataRow({ label, value, className = '' }) {
  return (
    <div className={`flex items-center justify-between py-2 ${className}`}>
      <span className="text-sm text-terminal-400">{label}</span>
      <span className="font-mono text-sm text-terminal-100">{value}</span>
    </div>
  )
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

function getFocusableElements(container) {
  if (!container) {
    return []
  }

  return [...container.querySelectorAll(FOCUSABLE_SELECTOR)].filter(
    (element) => element instanceof HTMLElement && !element.hasAttribute('disabled')
  )
}

// Modal
export function Modal({ isOpen, onClose, title, children, size = 'md' }) {
  const sizes = {
    sm: 'max-w-md',
    md: 'max-w-lg',
    lg: 'max-w-2xl',
    xl: 'max-w-4xl',
  }
  const titleId = useId()
  const dialogRef = useRef(null)
  const previousFocusRef = useRef(null)

  useEffect(() => {
    if (!isOpen) {
      return undefined
    }

    previousFocusRef.current = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null

    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'

    const frameId = window.requestAnimationFrame(() => {
      const focusableElements = getFocusableElements(dialogRef.current)
      const firstElement = focusableElements[0] || dialogRef.current
      firstElement?.focus()
    })

    return () => {
      window.cancelAnimationFrame(frameId)
      document.body.style.overflow = previousOverflow
      previousFocusRef.current?.focus?.()
    }
  }, [isOpen])

  useEffect(() => {
    if (!isOpen) {
      return undefined
    }

    const handleWindowKeyDown = (event) => {
      if (event.key !== 'Escape') {
        return
      }

      event.preventDefault()
      onClose?.()
    }

    window.addEventListener('keydown', handleWindowKeyDown)
    return () => {
      window.removeEventListener('keydown', handleWindowKeyDown)
    }
  }, [isOpen, onClose])

  if (!isOpen) return null

  const handleKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault()
      onClose?.()
      return
    }

    if (event.key !== 'Tab') {
      return
    }

    const focusableElements = getFocusableElements(dialogRef.current)

    if (focusableElements.length === 0) {
      event.preventDefault()
      dialogRef.current?.focus()
      return
    }

    const firstElement = focusableElements[0]
    const lastElement = focusableElements[focusableElements.length - 1]
    const activeElement = document.activeElement

    if (event.shiftKey && activeElement === firstElement) {
      event.preventDefault()
      lastElement.focus()
    } else if (!event.shiftKey && activeElement === lastElement) {
      event.preventDefault()
      firstElement.focus()
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" role="presentation">
      <div 
        aria-hidden="true"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={onClose}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onKeyDown={handleKeyDown}
        className={`relative w-full rounded-lg border border-terminal-600 bg-terminal-800 shadow-xl ${sizes[size]} animate-fade-in`}
      >
        <div className="flex items-center justify-between border-b border-terminal-600 px-4 py-3">
          <h2 id={titleId} className="text-lg font-semibold text-terminal-100">{title}</h2>
          <button
            onClick={onClose}
            aria-label="Close dialog"
            className="text-terminal-400 transition-colors hover:text-terminal-100"
          >
            <X className="size-5" />
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
export function Tabs({ tabs, activeTab, onChange, idBase: providedIdBase }) {
  const generatedId = useId().replace(/:/g, '')
  const idBase = providedIdBase || `tabs-${generatedId}`
  const tabRefs = useRef([])

  const moveFocus = (nextIndex) => {
    const nextTab = tabs[nextIndex]
    if (!nextTab) {
      return
    }

    onChange(nextTab.value)
    tabRefs.current[nextIndex]?.focus()
  }

  const handleKeyDown = (event, index) => {
    if (!tabs.length) {
      return
    }

    if (event.key === 'ArrowRight') {
      event.preventDefault()
      moveFocus((index + 1) % tabs.length)
      return
    }

    if (event.key === 'ArrowLeft') {
      event.preventDefault()
      moveFocus((index - 1 + tabs.length) % tabs.length)
      return
    }

    if (event.key === 'Home') {
      event.preventDefault()
      moveFocus(0)
      return
    }

    if (event.key === 'End') {
      event.preventDefault()
      moveFocus(tabs.length - 1)
      return
    }

    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      onChange(tabs[index].value)
    }
  }

  return (
    <div className="mb-4 flex gap-1 border-b border-terminal-600" role="tablist" aria-orientation="horizontal">
      {tabs.map((tab, index) => {
        const tabId = `${idBase}-tab-${tab.value}`
        const panelId = `${idBase}-panel-${tab.value}`
        const isActive = activeTab === tab.value

        return (
        <button
          key={tab.value}
          id={tabId}
          ref={(element) => {
            tabRefs.current[index] = element
          }}
          role="tab"
          aria-selected={isActive}
          aria-controls={panelId}
          tabIndex={isActive ? 0 : -1}
          onClick={() => onChange(tab.value)}
          onKeyDown={(event) => handleKeyDown(event, index)}
          className={`
            -mb-px border-b-2 px-4 py-2 text-sm font-medium transition-colors
            ${isActive
              ? 'border-amber-500 text-amber-400'
              : 'border-transparent text-terminal-400 hover:text-terminal-200'
            }
          `}
        >
          {tab.label}
          {tab.count !== undefined && (
            <span className={`ml-2 rounded-full px-1.5 py-0.5 text-xs ${
              isActive ? 'bg-amber-600/20' : 'bg-terminal-600'
            }`}>
              {tab.count}
            </span>
          )}
        </button>
        )
      })}
    </div>
  )
}

// Toggle switch
export function Toggle({ enabled, onChange, label }) {
  return (
    <label className="flex cursor-pointer items-center">
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
      className={`rounded border border-terminal-500 bg-terminal-800 px-3 py-2 text-terminal-100 focus:border-amber-500 focus:outline-none focus:ring-1 focus:ring-amber-500/20 ${className}`}
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
Select.displayName = 'Select'

// Input
export const Input = forwardRef(({ className = '', ...props }, ref) => {
  return (
    <input
      ref={ref}
      className={`rounded border border-terminal-500 bg-terminal-800 px-3 py-2 text-terminal-100 placeholder:text-terminal-400 focus:border-amber-500 focus:outline-none focus:ring-1 focus:ring-amber-500/20 ${className}`}
      {...props}
    />
  )
})
Input.displayName = 'Input'

// Stat card
export function StatCard({
  label,
  value,
  change,
  trend,
  subValue,
  valueColor,
  icon: Icon,
}) {
  const secondaryText = subValue ?? change
  const valueTone = valueColor || (
    trend === 'up' ? 'text-signal-long' :
    trend === 'down' ? 'text-signal-short' :
    'text-terminal-100'
  )

  return (
    <Card>
      <CardContent className="flex items-center justify-between">
        <div>
          <p className="mb-1 text-sm text-terminal-400">{label}</p>
          <p className={`font-mono text-2xl font-bold ${valueTone}`}>{value}</p>
          {secondaryText && (
            <p className={`mt-1 text-xs ${subValue ? 'text-terminal-400' : valueTone}`}>
              {secondaryText}
            </p>
          )}
        </div>
        {Icon && (
          <div className="flex size-10 items-center justify-center rounded-lg bg-terminal-700">
            <Icon className="size-5 text-terminal-400" />
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
        <RefreshCw className={`size-4 ${loading ? 'animate-spin' : ''}`} />
      </button>
    </div>
  )
}
