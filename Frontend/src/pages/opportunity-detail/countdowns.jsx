import { useEffect, useState } from 'react'

export function ExpiryCountdown({ expiresAt }) {
  const [remaining, setRemaining] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!expiresAt) {
      return undefined
    }

    function update() {
      const now = Date.now() / 1000
      const diff = expiresAt - now

      if (diff <= 0) {
        setRemaining('Expired')
        setIsUrgent(true)
        return
      }

      const mins = Math.floor(diff / 60)
      const secs = Math.floor(diff % 60)
      setRemaining(`${mins}m ${secs}s`)
      setIsUrgent(diff < 600)
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [expiresAt])

  if (!expiresAt) {
    return null
  }

  return (
    <span className={`font-mono ${isUrgent ? 'text-red-400' : 'text-terminal-300'}`}>
      {remaining}
    </span>
  )
}

export function ActionByCountdown({ actionByTs }) {
  const [remaining, setRemaining] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!actionByTs) {
      return undefined
    }

    function update() {
      const targetMs = typeof actionByTs === 'number'
        ? (actionByTs < 1e12 ? actionByTs * 1000 : actionByTs)
        : new Date(actionByTs).getTime()

      if (Number.isNaN(targetMs)) {
        setRemaining('')
        return
      }

      const diffMs = targetMs - Date.now()
      if (diffMs <= 0) {
        setRemaining('Deadline passed')
        setIsUrgent(true)
        return
      }

      const totalMins = Math.floor(diffMs / 60000)
      const hours = Math.floor(totalMins / 60)
      const mins = totalMins % 60
      const secs = Math.floor((diffMs % 60000) / 1000)

      if (hours > 0) {
        setRemaining(`${hours}h ${mins}m ${secs}s`)
      } else {
        setRemaining(`${mins}m ${secs}s`)
      }

      setIsUrgent(diffMs < 600000)
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [actionByTs])

  if (!actionByTs || !remaining) {
    return null
  }

  return (
    <span className={`font-mono ${isUrgent ? 'text-red-400' : 'text-terminal-300'}`}>
      {remaining}
    </span>
  )
}
