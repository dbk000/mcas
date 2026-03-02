import { Save } from 'lucide-react'

export default function ParamsTab({
  editParams,
  setEditParams,
  changeReason,
  setChangeReason,
  saving,
  handleSaveVersion,
}) {
  return (
    <div className="px-4 py-3">
      <div className="text-xs font-medium text-terminal-300 mb-3">Edit Parameters</div>
      <div className="space-y-2">
        {Object.entries(editParams).map(([key, value]) => (
          <div key={key} className="flex items-center gap-2">
            <label className="text-[10px] text-terminal-400 font-mono w-1/2 truncate" title={key}>
              {key}
            </label>
            {typeof value === 'boolean' ? (
              <button
                onClick={() => setEditParams(prev => ({ ...prev, [key]: !value }))}
                className={`text-[10px] font-mono px-2 py-1 rounded border transition-colors ${
                  value
                    ? 'bg-green-500/20 text-green-400 border-green-500/30'
                    : 'bg-terminal-900 text-terminal-500 border-terminal-700'
                }`}
              >
                {value ? 'true' : 'false'}
              </button>
            ) : (
              <input
                type={typeof value === 'number' ? 'number' : 'text'}
                value={typeof value === 'object' ? JSON.stringify(value) : value}
                onChange={e => {
                  const v = typeof value === 'number' ? parseFloat(e.target.value) || 0 : e.target.value
                  setEditParams(prev => ({ ...prev, [key]: v }))
                }}
                step={typeof value === 'number' ? 0.01 : undefined}
                className="flex-1 text-[10px] bg-terminal-900 border border-terminal-700 rounded px-1.5 py-1 text-terminal-200 font-mono"
              />
            )}
          </div>
        ))}
        {Object.keys(editParams).length === 0 && (
          <div className="text-[10px] text-terminal-500 text-center py-2">No parameters for this node</div>
        )}
      </div>
      {Object.keys(editParams).length > 0 && (
        <div className="mt-3 space-y-2">
          <input
            type="text"
            value={changeReason}
            onChange={e => setChangeReason(e.target.value)}
            placeholder="Change reason (required)"
            className="w-full text-[10px] bg-terminal-900 border border-terminal-700 rounded px-2 py-1.5 text-terminal-200 placeholder:text-terminal-600"
          />
          <button
            onClick={handleSaveVersion}
            disabled={saving || !changeReason.trim()}
            className="w-full flex items-center justify-center gap-1.5 text-[10px] px-3 py-1.5 bg-amber-600/20 text-amber-400 rounded hover:bg-amber-600/30 disabled:opacity-30 transition-colors"
          >
            <Save className="w-3 h-3" />
            {saving ? 'Saving...' : 'Save as New Version'}
          </button>
        </div>
      )}
    </div>
  )
}
