import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'

export default function ExpiryBadge({ expiresAt }) {
  const [label, setLabel] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!expiresAt) return undefined

    function update() {
      const now = Date.now() / 1000
      const diff = expiresAt - now
      if (diff <= 0) {
        setLabel('Expired')
        setIsUrgent(true)
        return
      }
      const minutes = Math.floor(diff / 60)
      setLabel(minutes < 1 ? '<1m' : `${minutes}m`)
      setIsUrgent(diff < 600)
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [expiresAt])

  if (!expiresAt) return null

  return (
    <span className={`inline-flex items-center gap-1 font-mono text-xs ${
      isUrgent ? 'text-red-400' : 'text-terminal-400'
    }`}>
      <Clock className="size-3" />
      {label}
    </span>
  )
}
