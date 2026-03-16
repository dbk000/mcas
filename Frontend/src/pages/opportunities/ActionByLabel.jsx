import { useEffect, useState } from 'react'
import { Clock } from 'lucide-react'

export default function ActionByLabel({ actionByTs }) {
  const [label, setLabel] = useState('')
  const [isUrgent, setIsUrgent] = useState(false)

  useEffect(() => {
    if (!actionByTs) return undefined

    function update() {
      const targetMs = typeof actionByTs === 'number'
        ? (actionByTs < 1e12 ? actionByTs * 1000 : actionByTs)
        : new Date(actionByTs).getTime()

      if (Number.isNaN(targetMs)) {
        setLabel('')
        return
      }

      const diffMs = targetMs - Date.now()
      if (diffMs <= 0) {
        setLabel('Deadline passed')
        setIsUrgent(true)
        return
      }

      const mins = Math.floor(diffMs / 60000)
      const hours = Math.floor(mins / 60)
      const remainingMinutes = mins % 60
      setLabel(hours > 0 ? `Act by: ${hours}h ${remainingMinutes}m` : `Act by: ${mins}m`)
      setIsUrgent(diffMs < 600000)
    }

    update()
    const interval = setInterval(update, 30000)
    return () => clearInterval(interval)
  }, [actionByTs])

  if (!actionByTs || !label) return null

  return (
    <span className={`inline-flex items-center gap-1 font-mono ${
      isUrgent ? 'text-red-400' : 'text-terminal-500'
    }`}>
      <Clock className="size-3" />
      {label}
    </span>
  )
}
