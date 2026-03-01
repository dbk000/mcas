import { useState, useEffect } from 'react'
import { X, ChevronDown, ChevronUp } from 'lucide-react'
import { useApi } from '../../context/ApiContext'

const STATUS_COLORS = {
  deployed: 'bg-green-500/20 text-green-400 border-green-500/30',
  promising: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  tested: 'bg-terminal-600 text-terminal-300 border-terminal-500',
  baseline: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  retired: 'bg-red-500/20 text-red-400 border-red-500/30',
}

export default function NodeDetailPanel({ node, onClose }) {
  const { get } = useApi()
  const [versions, setVersions] = useState([])
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [expandedVersion, setExpandedVersion] = useState(null)

  const nodeId = node.id || node.node_id

  // Fetch versions when node changes
  useEffect(() => {
    if (!nodeId) return
    let cancelled = false
    async function fetchVersions() {
      setLoadingVersions(true)
      try {
        const resp = await get(`/v1/meta/node/${nodeId}/versions?limit=100`)
        const data = resp?.versions || resp?.data?.versions || []
        if (!cancelled) setVersions(data)
      } catch (err) {
        console.error('Failed to fetch versions:', err)
      } finally {
        if (!cancelled) setLoadingVersions(false)
      }
    }
    fetchVersions()
    return () => { cancelled = true }
  }, [nodeId, get])

  // Filter versions by significance (above median)
  const filteredVersions = showAll
    ? versions
    : (() => {
        if (versions.length <= 5) return versions
        const sigs = versions.map(v => v.significance || 0).sort((a, b) => a - b)
        const median = sigs[Math.floor(sigs.length / 2)]
        return versions.filter(v => (v.significance || 0) >= median)
      })()

  const cv = node.current_version || node.currentVersion

  return (
    <div className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 shadow-xl z-50 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-600 bg-terminal-800/95 backdrop-blur">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-terminal-100 truncate">{node.name || node.label}</h2>
          <p className="text-xs text-terminal-400 capitalize">{node.subsystem} / {node.type || node.nodeType}</p>
        </div>
        <button
          onClick={onClose}
          className="p-1 text-terminal-400 hover:text-terminal-100 transition-colors"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {/* Node info */}
        <div className="px-4 py-3 space-y-2 border-b border-terminal-700">
          {node.description && (
            <p className="text-xs text-terminal-300">{node.description}</p>
          )}
          {node.source_file && (
            <p className="text-xs text-terminal-500 font-mono truncate" title={node.source_file}>
              {node.source_file}
            </p>
          )}
          {node.owner && (
            <p className="text-xs text-terminal-400">Owner: <span className="text-terminal-300">{node.owner}</span></p>
          )}
        </div>

        {/* Current version */}
        {cv && (
          <div className="px-4 py-3 border-b border-terminal-700">
            <div className="text-xs font-medium text-terminal-300 mb-2">Active Version</div>
            <div className="bg-terminal-900 rounded-lg p-3 space-y-1.5">
              <div className="flex items-center justify-between">
                <span className="text-xs font-mono text-terminal-100">v{cv.version_number}</span>
                <span className={`text-[10px] px-1.5 py-0.5 rounded border ${STATUS_COLORS[cv.status] || STATUS_COLORS.tested}`}>
                  {cv.status}
                </span>
              </div>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
                {cv.agg_pf != null && (
                  <div>
                    <span className="text-terminal-500">PF </span>
                    <span className={cv.agg_pf >= 1.0 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>
                      {cv.agg_pf.toFixed(2)}
                    </span>
                  </div>
                )}
                {cv.win_rate != null && (
                  <div>
                    <span className="text-terminal-500">Win </span>
                    <span className="text-terminal-200 font-mono">{(cv.win_rate * 100).toFixed(1)}%</span>
                  </div>
                )}
                {cv.trade_count != null && (
                  <div>
                    <span className="text-terminal-500">Trades </span>
                    <span className="text-terminal-200 font-mono">{cv.trade_count}</span>
                  </div>
                )}
                {cv.net_pnl != null && (
                  <div>
                    <span className="text-terminal-500">PnL </span>
                    <span className={cv.net_pnl >= 0 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>
                      ${cv.net_pnl.toFixed(0)}
                    </span>
                  </div>
                )}
              </div>
            </div>
          </div>
        )}

        {/* Version history */}
        <div className="px-4 py-3">
          <div className="flex items-center justify-between mb-2">
            <div className="text-xs font-medium text-terminal-300">
              Version History
              <span className="text-terminal-500 ml-1">
                ({filteredVersions.length}{!showAll && versions.length > filteredVersions.length ? ` of ${versions.length}` : ''})
              </span>
            </div>
            {versions.length > 5 && (
              <button
                onClick={() => setShowAll(!showAll)}
                className="text-[10px] text-amber-400 hover:text-amber-300 transition-colors"
              >
                {showAll ? 'Show significant' : 'Show all'}
              </button>
            )}
          </div>

          {/* Significance sparkline placeholder */}
          <div className="h-6 mb-2 bg-terminal-900 rounded border border-terminal-700 flex items-center justify-center">
            <span className="text-[10px] text-terminal-600">Timeline scrubber (coming soon)</span>
          </div>

          {loadingVersions ? (
            <div className="text-xs text-terminal-500 text-center py-4">Loading versions...</div>
          ) : filteredVersions.length === 0 ? (
            <div className="text-xs text-terminal-500 text-center py-4">No versions recorded</div>
          ) : (
            <div className="space-y-1">
              {filteredVersions.map((v) => (
                <VersionRow
                  key={v.version_id}
                  version={v}
                  expanded={expandedVersion === v.version_id}
                  onToggle={() => setExpandedVersion(
                    expandedVersion === v.version_id ? null : v.version_id
                  )}
                />
              ))}
            </div>
          )}
        </div>

        {/* Version diff viewer placeholder */}
        <div className="px-4 py-3 border-t border-terminal-700">
          <div className="h-16 bg-terminal-900 rounded border border-terminal-700 flex items-center justify-center">
            <span className="text-[10px] text-terminal-600">Version diff viewer (coming soon)</span>
          </div>
        </div>
      </div>
    </div>
  )
}

function VersionRow({ version: v, expanded, onToggle }) {
  return (
    <div className="bg-terminal-900 rounded border border-terminal-700/50">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-2.5 py-1.5 text-left hover:bg-terminal-700/30 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-mono text-terminal-200 flex-shrink-0">v{v.version_number}</span>
          <span className={`text-[10px] px-1 py-0.5 rounded border flex-shrink-0 ${STATUS_COLORS[v.status] || STATUS_COLORS.tested}`}>
            {v.status}
          </span>
          {v.agg_pf != null && (
            <span className={`text-xs font-mono flex-shrink-0 ${v.agg_pf >= 1.0 ? 'text-green-400' : 'text-red-400'}`}>
              PF {v.agg_pf.toFixed(2)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {v.created_at && (
            <span className="text-[10px] text-terminal-500">
              {new Date(v.created_at).toLocaleDateString()}
            </span>
          )}
          {expanded ? <ChevronUp className="w-3 h-3 text-terminal-500" /> : <ChevronDown className="w-3 h-3 text-terminal-500" />}
        </div>
      </button>
      {expanded && (
        <div className="px-2.5 pb-2 space-y-1.5 border-t border-terminal-700/50">
          {v.change_reason && (
            <p className="text-[11px] text-terminal-300 mt-1.5">{v.change_reason}</p>
          )}
          <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
            {v.net_pnl != null && (
              <div>
                <span className="text-terminal-500">PnL </span>
                <span className={v.net_pnl >= 0 ? 'text-green-400 font-mono' : 'text-red-400 font-mono'}>
                  ${v.net_pnl.toFixed(0)}
                </span>
              </div>
            )}
            {v.win_rate != null && (
              <div>
                <span className="text-terminal-500">Win </span>
                <span className="text-terminal-200 font-mono">{(v.win_rate * 100).toFixed(1)}%</span>
              </div>
            )}
            {v.max_drawdown != null && (
              <div>
                <span className="text-terminal-500">DD </span>
                <span className="text-red-400 font-mono">{(v.max_drawdown * 100).toFixed(1)}%</span>
              </div>
            )}
            {v.trade_count != null && (
              <div>
                <span className="text-terminal-500">Trades </span>
                <span className="text-terminal-200 font-mono">{v.trade_count}</span>
              </div>
            )}
          </div>
          {v.parameters && Object.keys(v.parameters).length > 0 && (
            <div className="mt-1">
              <div className="text-[10px] text-terminal-500 mb-0.5">Parameters</div>
              <div className="bg-terminal-800 rounded p-1.5 text-[10px] font-mono text-terminal-300 max-h-24 overflow-y-auto">
                {Object.entries(v.parameters).map(([k, val]) => (
                  <div key={k}>{k}: {JSON.stringify(val)}</div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
