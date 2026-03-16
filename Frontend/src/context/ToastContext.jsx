import { createContext, useContext, useState, useCallback } from 'react'
import { X, CheckCircle, AlertCircle, Info, AlertTriangle } from 'lucide-react'

const ToastContext = createContext(null)

let toastId = 0

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([])

  const addToast = useCallback((message, type = 'info', duration = 5000) => {
    const id = ++toastId
    
    setToasts(prev => [...prev, { id, message, type }])
    
    if (duration > 0) {
      setTimeout(() => {
        setToasts(prev => prev.filter(t => t.id !== id))
      }, duration)
    }
    
    return id
  }, [])

  const removeToast = useCallback((id) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  return (
    <ToastContext.Provider value={{ addToast, removeToast }}>
      {children}
      <ToastContainer toasts={toasts} removeToast={removeToast} />
    </ToastContext.Provider>
  )
}

function ToastContainer({ toasts, removeToast }) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <Toast key={toast.id} toast={toast} onClose={() => removeToast(toast.id)} />
      ))}
    </div>
  )
}

function Toast({ toast, onClose }) {
  const { message, type } = toast

  const icons = {
    success: <CheckCircle className="size-5 text-green-400" />,
    error: <AlertCircle className="size-5 text-red-400" />,
    warning: <AlertTriangle className="size-5 text-amber-400" />,
    info: <Info className="size-5 text-blue-400" />,
  }

  const bgColors = {
    success: 'bg-green-900/80 border-green-700',
    error: 'bg-red-900/80 border-red-700',
    warning: 'bg-amber-900/80 border-amber-700',
    info: 'bg-blue-900/80 border-blue-700',
  }

  return (
    <div 
      className={`
        flex min-w-[300px] max-w-md animate-slide-up items-center gap-3 rounded-lg border
        px-4 py-3 backdrop-blur-sm
        ${bgColors[type] || bgColors.info}
      `}
    >
      {icons[type] || icons.info}
      <span className="flex-1 text-sm text-terminal-100">{message}</span>
      <button 
        onClick={onClose}
        className="text-terminal-400 transition-colors hover:text-terminal-100"
      >
        <X className="size-4" />
      </button>
    </div>
  )
}

export function useToast() {
  const context = useContext(ToastContext)
  if (!context) {
    throw new Error('useToast must be used within a ToastProvider')
  }
  return context
}
