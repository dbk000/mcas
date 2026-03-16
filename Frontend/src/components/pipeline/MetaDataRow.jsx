/**
 * Wave 4.5: MetaDataRow — reusable metadata display component
 *
 * Props:
 *   label: string — field name
 *   value: any — field value (auto-formatted based on kind)
 *   kind: 'text' | 'number' | 'percentage' | 'currency' | 'code' | 'boolean' | 'json'
 *   positive: boolean — for coloring number values (green if positive)
 *   className: string — additional classes
 */
export default function MetaDataRow({ label, value, kind = 'text', positive, className = '' }) {
  if (value == null || value === '') return null

  let formatted
  let colorClass = 'text-terminal-200'

  switch (kind) {
    case 'number':
      formatted = typeof value === 'number' ? value.toFixed(4) : String(value)
      if (positive != null) colorClass = positive ? 'text-green-400' : 'text-red-400'
      break
    case 'percentage':
      formatted = typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : String(value)
      if (positive != null) colorClass = positive ? 'text-green-400' : 'text-red-400'
      break
    case 'currency':
      formatted = typeof value === 'number' ? `$${value.toFixed(2)}` : String(value)
      if (positive != null) colorClass = positive ? 'text-green-400' : 'text-red-400'
      break
    case 'code':
      formatted = String(value)
      colorClass = 'text-terminal-300 font-mono'
      break
    case 'boolean':
      formatted = value ? '✓' : '✗'
      colorClass = value ? 'text-green-400' : 'text-red-400'
      break
    case 'json':
      formatted = typeof value === 'object' ? JSON.stringify(value, null, 1) : String(value)
      colorClass = 'text-terminal-300 font-mono'
      break
    default:
      formatted = String(value)
  }

  return (
    <div className={`flex items-baseline justify-between py-0.5 text-[11px] ${className}`}>
      <span className="mr-2 text-terminal-500">{label}</span>
      <span className={`${colorClass} ${kind === 'code' || kind === 'json' ? 'text-[10px]' : ''} max-w-[60%] truncate text-right`} title={formatted}>
        {formatted}
      </span>
    </div>
  )
}
