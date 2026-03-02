import { Copy, Check } from 'lucide-react'

export default function ContextTab({
  node,
  nodeId,
  versions,
  graph,
  copied,
  handleCopyContext,
}) {
  return (
    <div className="px-4 py-3 space-y-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-terminal-300">Full Context</div>
        <button
          onClick={handleCopyContext}
          className="flex items-center gap-1 text-[10px] text-amber-400 hover:text-amber-300"
        >
          {copied ? <Check className="w-3 h-3" /> : <Copy className="w-3 h-3" />}
          {copied ? 'Copied' : 'Copy Markdown'}
        </button>
      </div>

      {/* Port contracts */}
      {(node.ports?.inputs?.length > 0 || node.ports?.outputs?.length > 0) && (
        <div>
          <div className="text-[10px] text-terminal-500 mb-1">Port Contracts</div>
          {node.ports?.inputs?.map(p => (
            <div key={p.id} className="text-[10px] font-mono text-terminal-300">
              <span className="text-blue-400">in</span> {p.name}: {p.data_type}
            </div>
          ))}
          {node.ports?.outputs?.map(p => (
            <div key={p.id} className="text-[10px] font-mono text-terminal-300">
              <span className="text-green-400">out</span> {p.name}: {p.data_type}
            </div>
          ))}
        </div>
      )}

      {/* Connected nodes */}
      {graph && (
        <div>
          <div className="text-[10px] text-terminal-500 mb-1">Connected Nodes</div>
          {[...graph.getUpstream(nodeId)].length > 0 && (
            <div className="text-[10px] text-terminal-300">
              <span className="text-terminal-500">Upstream: </span>
              {[...graph.getUpstream(nodeId)].map(id => graph.nodeMap[id]?.name || id).join(', ')}
            </div>
          )}
          {[...graph.getDownstream(nodeId)].length > 0 && (
            <div className="text-[10px] text-terminal-300">
              <span className="text-terminal-500">Downstream: </span>
              {[...graph.getDownstream(nodeId)].map(id => graph.nodeMap[id]?.name || id).join(', ')}
            </div>
          )}
        </div>
      )}

      {/* Recent versions summary */}
      {versions.length > 0 && (
        <div>
          <div className="text-[10px] text-terminal-500 mb-1">Recent Versions</div>
          <div className="space-y-0.5">
            {versions.slice(0, 5).map(v => (
              <div key={v.version_id} className="text-[10px] text-terminal-300 font-mono">
                v{v.version_number} ({v.status}) — {v.change_reason || 'no reason'}
                {v.agg_pf != null && (
                  <span className={v.agg_pf >= 1 ? ' text-green-400' : ' text-red-400'}>
                    {' '}PF {v.agg_pf.toFixed(2)}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
