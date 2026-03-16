import { formatDateTime } from '../../utils/formatters'

export function parseCompositeId(id) {
  if (!id || !id.includes(':')) {
    return null
  }

  const parts = id.split(':')
  if (parts.length >= 3) {
    return { setup_id: parts[0], symbol: parts[1], tf: parts.slice(2).join(':') }
  }

  return null
}

export function getWalkthroughToneClasses(state) {
  if (state === 'verified') {
    return {
      dot: 'bg-green-400',
      title: 'text-green-300',
      border: 'border-green-700/40',
      bg: 'bg-green-900/10',
    }
  }

  if (state === 'warning') {
    return {
      dot: 'bg-amber-400',
      title: 'text-amber-300',
      border: 'border-amber-700/40',
      bg: 'bg-amber-900/10',
    }
  }

  if (state === 'error') {
    return {
      dot: 'bg-red-400',
      title: 'text-red-300',
      border: 'border-red-700/40',
      bg: 'bg-red-900/10',
    }
  }

  return {
    dot: 'bg-terminal-500',
    title: 'text-terminal-200',
    border: 'border-terminal-600',
    bg: 'bg-terminal-900/70',
  }
}

export function formatWalkthroughTimestamp(value) {
  if (!value) {
    return null
  }

  const timestamp = Date.parse(String(value))
  if (!Number.isFinite(timestamp)) {
    return null
  }

  return formatDateTime(new Date(timestamp).toISOString())
}
