import { useEffect, useMemo } from 'react'
import { Save } from 'lucide-react'
import { useStrategies } from '../../hooks/useData'

export default function ParamsTab({
  node,
  editParams,
  setEditParams,
  changeReason,
  setChangeReason,
  saving,
  handleSaveVersion,
}) {
  const { data: strategyData } = useStrategies()
  const nodeName = String(node?.name || node?.label || '')
  const isStopLossNode = /stop[-\s]?loss/i.test(nodeName)

  const strategyRows = useMemo(() => {
    const rows = (strategyData?.strategies || [])
      .filter((s) => s?.id && String(s.type || '').toLowerCase() === 'crypto')
      .map((s) => ({
        id: String(s.id),
        label: String(s.label || s.id),
      }))
    return rows.sort((a, b) => a.id.localeCompare(b.id))
  }, [strategyData])

  const stopDistanceFactors = useMemo(() => {
    const raw = editParams?.stop_distance_factors
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return {}
    return raw
  }, [editParams])

  const baseSl = useMemo(() => {
    const n = Number(editParams?.sl_r)
    return Number.isFinite(n) && n > 0 ? n : 1
  }, [editParams?.sl_r])

  useEffect(() => {
    if (!isStopLossNode || strategyRows.length === 0) return
    setEditParams((prev) => {
      const next = { ...prev }
      let changed = false

      if (!next.schema_version) {
        next.schema_version = 'v1'
        changed = true
      }
      if (!next.intrabar_policy) {
        next.intrabar_policy = 'STOP_FIRST'
        changed = true
      }
      if (typeof next.one_event_per_bar !== 'boolean') {
        next.one_event_per_bar = true
        changed = true
      }

      const existing = (
        next?.stop_distance_factors &&
        typeof next.stop_distance_factors === 'object' &&
        !Array.isArray(next.stop_distance_factors)
      ) ? { ...next.stop_distance_factors } : {}
      changed = changed || !next?.stop_distance_factors
      for (const s of strategyRows) {
        if (!(s.id in existing)) {
          existing[s.id] = baseSl
          changed = true
        }
      }
      if (!changed) return prev
      next.stop_distance_factors = existing
      return next
    })
  }, [isStopLossNode, strategyRows, baseSl, setEditParams])

  const renderParamInput = (key, value) => {
    if (typeof value === 'boolean') {
      return (
        <button
          onClick={() => setEditParams((prev) => ({ ...prev, [key]: !value }))}
          className={`text-[10px] font-mono px-2 py-1 rounded border transition-colors ${
            value
              ? 'bg-green-500/20 text-green-400 border-green-500/30'
              : 'bg-terminal-900 text-terminal-500 border-terminal-700'
          }`}
        >
          {value ? 'true' : 'false'}
        </button>
      )
    }

    return (
      <input
        type={typeof value === 'number' ? 'number' : 'text'}
        value={typeof value === 'object' ? JSON.stringify(value) : value}
        onChange={(e) => {
          const v = typeof value === 'number' ? parseFloat(e.target.value) || 0 : e.target.value
          setEditParams((prev) => ({ ...prev, [key]: v }))
        }}
        step={typeof value === 'number' ? 0.01 : undefined}
        className="flex-1 text-[10px] bg-terminal-900 border border-terminal-700 rounded px-1.5 py-1 text-terminal-200 font-mono"
      />
    )
  }

  const visibleEntries = Object.entries(editParams).filter(([key]) => {
    if (!isStopLossNode) return true
    return key !== 'stop_distance_factors' && key !== 'intrabar_policy' && key !== 'one_event_per_bar'
  })

  return (
    <div className="px-4 py-3">
      <div className="text-xs font-medium text-terminal-300 mb-3">Edit Parameters</div>
      <div className="space-y-2">
        {visibleEntries.map(([key, value]) => (
          <div key={key} className="flex items-center gap-2">
            <label className="text-[10px] text-terminal-400 font-mono w-1/2 truncate" title={key}>
              {key}
            </label>
            {renderParamInput(key, value)}
          </div>
        ))}
        {isStopLossNode && (
          <div className="mt-2 rounded border border-terminal-700 bg-terminal-900/50 p-2">
            <div className="grid grid-cols-[120px_1fr] items-center gap-2 mb-2">
              <span className="text-[10px] text-terminal-400">Intrabar Policy</span>
              <select
                value={String(editParams.intrabar_policy || 'STOP_FIRST')}
                onChange={(e) => setEditParams((prev) => ({ ...prev, intrabar_policy: e.target.value }))}
                className="w-full text-[10px] bg-terminal-900 border border-terminal-700 rounded px-1.5 py-1 text-terminal-200 font-mono"
              >
                <option value="STOP_FIRST">STOP_FIRST (Conservative)</option>
                <option value="TP_FIRST">TP_FIRST (Optimistic)</option>
                <option value="OHLC_HEURISTIC">OHLC_HEURISTIC (Path Guess)</option>
              </select>
            </div>
            <div className="grid grid-cols-[120px_1fr] items-center gap-2 mb-3">
              <span className="text-[10px] text-terminal-400">One Event Per Bar</span>
              <button
                onClick={() => setEditParams((prev) => ({ ...prev, one_event_per_bar: !Boolean(prev.one_event_per_bar) }))}
                className={`w-fit text-[10px] font-mono px-2 py-1 rounded border transition-colors ${
                  editParams.one_event_per_bar
                    ? 'bg-green-500/20 text-green-400 border-green-500/30'
                    : 'bg-terminal-900 text-terminal-500 border-terminal-700'
                }`}
              >
                {editParams.one_event_per_bar ? 'true' : 'false'}
              </button>
            </div>
            <div className="text-[10px] text-terminal-400 mb-2 font-medium">
              Per-Strategy SL (R)
            </div>
            <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
              {strategyRows.map((strategy) => (
                <div key={strategy.id} className="grid grid-cols-[58px_1fr_92px] items-center gap-2">
                  <span className="text-[10px] font-mono text-terminal-200">{strategy.id}</span>
                  <span className="text-[10px] text-terminal-500 truncate" title={strategy.label}>
                    {strategy.label}
                  </span>
                  <input
                    type="number"
                    step="0.05"
                    min="0.05"
                    value={stopDistanceFactors[strategy.id] ?? ''}
                    onChange={(e) => {
                      const parsed = parseFloat(e.target.value)
                      setEditParams((prev) => ({
                        ...prev,
                        stop_distance_factors: {
                          ...(prev.stop_distance_factors || {}),
                          [strategy.id]: Number.isFinite(parsed) ? parsed : 0,
                        },
                      }))
                    }}
                    className="w-full text-[10px] bg-terminal-900 border border-terminal-700 rounded px-1.5 py-1 text-terminal-200 font-mono"
                  />
                </div>
              ))}
            </div>
          </div>
        )}
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
