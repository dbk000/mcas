import VersionEventTimeline from './VersionEventTimeline'

export default function BiographyTab({
  versions,
  selectedVersionForBio,
  setSelectedVersionForBio,
  graph,
  nodeId,
  get,
}) {
  return (
    <div className="px-4 py-3">
      <div className="flex items-center justify-between mb-3">
        <div className="text-xs font-medium text-terminal-300">Version Biography</div>
        <select
          value={selectedVersionForBio || ''}
          onChange={e => setSelectedVersionForBio(e.target.value)}
          className="text-[10px] bg-terminal-900 border border-terminal-700 rounded px-1.5 py-1 text-terminal-300 max-w-[180px]"
        >
          {versions.map(v => (
            <option key={v.version_id} value={v.version_id}>
              v{v.version_number} — {v.status}
            </option>
          ))}
        </select>
      </div>
      {selectedVersionForBio && (
        <VersionEventTimeline
          versionId={selectedVersionForBio}
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
