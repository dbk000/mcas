import { useState, useMemo } from 'react'
import { ChevronDown, ChevronRight, Search, X, Focus } from 'lucide-react'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

/**
 * Wave 4.4: Split panel sidebar — node tree + filter controls
 * Grouped by subsystem, each node clickable to select/navigate.
 * Optional, off by default on TV.
 */

const SUBSYSTEM_COLORS = {
  ingestion: '#3b82f6', feature: '#8b5cf6', signal: '#f59e0b',
  filtering: '#ef4444', strategy: '#10b981', parameter: '#6366f1',
  backtest: '#ec4899', metrics: '#14b8a6', risk: '#f97316',
  execution: '#22c55e', meta: '#64748b',
}

export default function PipelineSidebar({ topologyData, graph, onClose }) {
  const [expandedGroups, setExpandedGroups] = useState(new Set(['signal', 'strategy', 'feature']))
  const [searchText, setSearchText] = useState('')

  // Group nodes by subsystem
  const groupedNodes = useMemo(() => {
    if (!topologyData?.nodes) return {}
    const groups = {}
    for (const n of topologyData.nodes) {
      const sub = n.subsystem || 'unknown'
      if (!groups[sub]) groups[sub] = []
      groups[sub].push(n)
    }
    // Sort nodes within each group by name
    for (const sub of Object.keys(groups)) {
      groups[sub].sort((a, b) => (a.name || '').localeCompare(b.name || ''))
    }
    return groups
  }, [topologyData])

  // Filter by search
  const filteredGroups = useMemo(() => {
    if (!searchText.trim()) return groupedNodes
    const q = searchText.toLowerCase()
    const result = {}
    for (const [sub, nodes] of Object.entries(groupedNodes)) {
      const matched = nodes.filter(n =>
        (n.name || '').toLowerCase().includes(q) ||
        (n.type || '').toLowerCase().includes(q) ||
        (n.description || '').toLowerCase().includes(q)
      )
      if (matched.length > 0) result[sub] = matched
    }
    return result
  }, [groupedNodes, searchText])

  const toggleGroup = (sub) => {
    setExpandedGroups(prev => {
      const next = new Set(prev)
      if (next.has(sub)) next.delete(sub)
      else next.add(sub)
      return next
    })
  }

  const sortedSubsystems = Object.keys(filteredGroups).sort()

  return (
    <div className="fixed left-0 top-14 z-40 flex h-[calc(100vh-3.5rem)] w-[260px] flex-col border-r border-terminal-600 bg-terminal-800">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-terminal-700 px-3 py-2">
        <span className="text-xs font-medium text-terminal-300">Node Tree</span>
        <button onClick={onClose} className="text-terminal-500 hover:text-terminal-300">
          <X className="size-3.5" />
        </button>
      </div>

      {/* Search */}
      <div className="border-b border-terminal-700 px-3 py-2">
        <div className="relative">
          <Search className="absolute left-2 top-1/2 size-3 -translate-y-1/2 text-terminal-500" />
          <input
            type="text"
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            placeholder="Filter nodes..."
            className="w-full rounded border border-terminal-700 bg-terminal-900 py-1.5 pl-7 pr-2 text-[11px] text-terminal-200 placeholder:text-terminal-600 focus:border-amber-500/50 focus:outline-none"
          />
          {searchText && (
            <button
              onClick={() => setSearchText('')}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 text-terminal-500 hover:text-terminal-300"
            >
              <X className="size-3" />
            </button>
          )}
        </div>
      </div>

      {/* Node tree */}
      <div className="flex-1 overflow-y-auto">
        {sortedSubsystems.map(sub => {
          const nodes = filteredGroups[sub]
          const isExpanded = expandedGroups.has(sub) || searchText.trim()
          const color = SUBSYSTEM_COLORS[sub] || '#6b7280'

          return (
            <div key={sub}>
              <button
                onClick={() => toggleGroup(sub)}
                className="flex w-full items-center gap-2 px-3 py-1.5 text-left transition-colors hover:bg-terminal-700/30"
              >
                {isExpanded
                  ? <ChevronDown className="size-3 shrink-0 text-terminal-500" />
                  : <ChevronRight className="size-3 shrink-0 text-terminal-500" />
                }
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color }}
                />
                <span className="flex-1 text-[11px] font-medium capitalize text-terminal-300">{sub}</span>
                <span className="text-[10px] text-terminal-500">{nodes.length}</span>
              </button>
              {isExpanded && (
                <div className="pb-1">
                  {nodes.map(n => {
                    const isSelected = graph.selectedNodeId === n.id
                    const cv = n.current_version
                    return (
                      <button
                        key={n.id}
                        onClick={() => graph.selectNode(n.id)}
                        onDoubleClick={() => {
                          if (PIPELINE_FEATURES.focusMode) {
                            graph.setFocusMode('node', n.id)
                          }
                        }}
                        className={`w-full py-1 pl-10 pr-3 text-left transition-colors hover:bg-terminal-700/30 ${
                          isSelected ? 'border-l-2 border-amber-400 bg-amber-500/10' : ''
                        }`}
                      >
                        <div className="truncate text-[11px] text-terminal-200">{n.name}</div>
                        <div className="mt-0.5 flex items-center gap-1.5">
                          <span className="text-[9px] text-terminal-500">{n.type}</span>
                          {cv?.status && (
                            <span className={`text-[9px] ${
                              cv.status === 'deployed' ? 'text-green-400' :
                              cv.status === 'promising' ? 'text-amber-400' : 'text-terminal-500'
                            }`}>{cv.status}</span>
                          )}
                          {cv?.agg_pf != null && (
                            <span className={`font-mono text-[9px] ${cv.agg_pf >= 1 ? 'text-green-400' : 'text-red-400'}`}>
                              PF {cv.agg_pf.toFixed(2)}
                            </span>
                          )}
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Focus mode controls */}
      {PIPELINE_FEATURES.focusMode && (
        <div className="border-t border-terminal-700 px-3 py-2">
          <div className="mb-1.5 text-[10px] text-terminal-500">Focus</div>
          <div className="flex gap-1">
            {graph.focusMode !== 'off' ? (
              <button
                onClick={() => graph.clearFocusMode()}
                className="flex flex-1 items-center justify-center gap-1 rounded bg-amber-500/20 px-2 py-1 text-[10px] text-amber-400 hover:bg-amber-500/30"
              >
                <X className="size-3" />
                Clear Focus
              </button>
            ) : (
              <>
                {graph.selectedNodeId && (
                  <button
                    onClick={() => graph.setFocusMode('node', graph.selectedNodeId)}
                    className="flex flex-1 items-center justify-center gap-1 rounded bg-terminal-700 px-2 py-1 text-[10px] text-terminal-300 hover:bg-terminal-600"
                  >
                    <Focus className="size-3" />
                    Focus Node
                  </button>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
