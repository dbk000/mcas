export default function FormField({ label, children, hint }) {
  return (
    <div className="space-y-1.5">
      <label className="block text-sm font-medium text-terminal-300">{label}</label>
      {children}
      {hint && <p className="text-xs text-terminal-500">{hint}</p>}
    </div>
  )
}
