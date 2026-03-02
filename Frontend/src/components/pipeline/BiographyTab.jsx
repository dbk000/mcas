import { useState } from 'react'
import VersionEventTimeline from './VersionEventTimeline'

export default function BiographyTab({
  versions,
  selectedVersionForBio,
  setSelectedVersionForBio,
  graph,
  nodeId,
  get,
}) {
  // Wave 2.6: Table/tree view toggle
  const [viewMode, setViewMode] = useState('list') // 'list' | 'tree'

  // Wave 2.7: Temporal lineage slider
  // Filters events shown in biography — does NOT affect canvas rendering
  const [temporalFilter, setTemporalFilter] = useState(null)

  // Get date range for temporal slider from selected version's events
  const selectedVersion = versions.find(v => v.version_id === selectedVersionForBio)

  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs font-medium text-terminal-300">Version Biography</div>
        <div className="flex items-center gap-2">
          {/* View mode toggle (2.6) */}
          <div className="flex bg-terminal-900 rounded border border-terminal-700 overflow-hidden">
            <button
              onClick={() => setViewMode('list')}
              className={`text-[9px] px-1.5 py-0.5 transition-colors ${
                viewMode === 'list' ? 'bg-terminal-700 text-terminal-200' : 'text-terminal-500 hover:text-terminal-300'
              }`}
            >
              List
            </button>
            <button
              onClick={() => setViewMode('tree')}
              className={`text-[9px] px-1.5 py-0.5 transition-colors ${
                viewMode === 'tree' ? 'bg-terminal-700 text-terminal-200' : 'text-terminal-500 hover:text-terminal-300'
              }`}
            >
              Tree
            </button>
          </div>
          <select
            value={selectedVersionForBio || ''}
            onChange={e => {
              setSelectedVersionForBio(e.target.value)
              setTemporalFilter(null) // Reset slider on version change
            }}
            className="text-[10px] bg-terminal-900 border border-terminal-700 rounded px-1.5 py-1 text-terminal-300 max-w-[180px]"
          >
            {versions.map(v => (
              <option key={v.version_id} value={v.version_id}>
                v{v.version_number} — {v.status}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Temporal slider (2.7) */}
      {selectedVersionForBio && (
        <div className="mb-2">
          <div className="flex items-center gap-2">
            <label className="text-[9px] text-terminal-500 shrink-0">Time scrub:</label>
            <input
              type="datetime-local"
              value={temporalFilter || ''}
              onChange={e => setTemporalFilter(e.target.value || null)}
              className="flex-1 text-[9px] bg-terminal-900 border border-terminal-700 rounded px-1 py-0.5 text-terminal-400"
            />
            {temporalFilter && (
              <button
                onClick={() => setTemporalFilter(null)}
                className="text-[9px] text-amber-400 hover:text-amber-300"
              >
                Clear
              </button>
            )}
          </div>
        </div>
      )}

      {selectedVersionForBio && (
        <VersionEventTimeline
          versionId={selectedVersionForBio}
          viewMode={viewMode}
          temporalFilter={temporalFilter}
          onCausalClick={async (eventId) => {
            try {
              const resp = await get(`/v1/meta/event/${eventId}/causal_chain`)
              const chain = resp?.data || resp
              // Draw causal arrows from ancestors → descendants
              graph?.clearCausalArrows?.()
              const links = [...(chain.ancestors || []), ...(chain.descendants || [])]
              for (const link of links) {
                if (link.from_node_id && link.to_node_id) {
                  graph?.showCausalArrow?.(link.from_node_id, link.to_node_id, link.event_type || '')
                }
              }
            } catch (err) {
              console.error('Causal chain fetch failed:', err)
            }
          }}
        />
      )}
    </div>
  )
}
