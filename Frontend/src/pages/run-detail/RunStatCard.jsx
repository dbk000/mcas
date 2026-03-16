export default function RunStatCard({ label, value, sub, color }) {
  return (
    <div className="rounded-lg border border-terminal-700/50 bg-terminal-800/50 p-3">
      <div className="mb-1 text-xs text-terminal-500">{label}</div>
      <div className={`font-mono text-lg font-bold ${color || 'text-terminal-100'}`}>
        {value}
      </div>
      {sub && <div className="mt-0.5 text-xs text-terminal-500">{sub}</div>}
    </div>
  )
}
