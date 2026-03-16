import { Copy, Check } from 'lucide-react'
import { getActiveNodeVersion } from './nodeDetailUtils'

function ProvenanceRow({ label, value, mono = false, warn = false }) {
  const hasValue = value != null && value !== '' && value !== undefined
  return (
    <div className="flex items-start gap-2 text-[10px]">
      <span className="w-24 shrink-0 text-terminal-500">{label}</span>
      {hasValue ? (
        <span className={`${mono ? 'font-mono' : ''} break-all text-terminal-300`}>{value}</span>
      ) : (
        <span className={`italic ${warn ? 'text-amber-500/70' : 'text-terminal-600'}`}>not recorded</span>
      )}
    </div>
  )
}

function formatDateRange(ctx) {
  const start = ctx?.date_range?.start_ts || ctx?.date_range?.start || ctx?.start_date
  const end = ctx?.date_range?.end_ts || ctx?.date_range?.end || ctx?.end_date
  if (!start && !end) return null
  const fmt = (d) => {
    if (!d) return '?'
    const dt = new Date(typeof d === 'number' ? d * 1000 : d)
    return isNaN(dt) ? String(d) : dt.toISOString().slice(0, 10)
  }
  return `${fmt(start)} to ${fmt(end)}`
}

function formatSymbolCount(ctx) {
  const syms = ctx?.symbols_tested || ctx?.symbols
  if (Array.isArray(syms)) return `${syms.length} symbols`
  if (typeof syms === 'number') return `${syms} symbols`
  return null
}

export default function ContextTab({
  node,
  nodeId,
  versions,
  graph,
  copied,
  handleCopyContext,
}) {
  const activeVersion = getActiveNodeVersion(versions)
  const ctx = activeVersion?.metrics_context || {}
  const runDetail = activeVersion?.run_detail
  const hasProvenance = activeVersion && (
    activeVersion.metrics_run_id ||
    Object.keys(ctx).length > 0 ||
    activeVersion.source_hash
  )
  const hasAnyMetrics = activeVersion && (
    activeVersion.agg_pf != null || activeVersion.trade_count != null
  )

  return (
    <div className="space-y-3 px-4 py-3">
      <div className="flex items-center justify-between">
        <div className="text-xs font-medium text-terminal-300">Full Context</div>
        <button
          onClick={handleCopyContext}
          className="flex items-center gap-1 text-[10px] text-amber-400 hover:text-amber-300"
        >
          {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
          {copied ? 'Copied' : 'Copy Markdown'}
        </button>
      </div>

      {/* Port contracts */}
      {(node.ports?.inputs?.length > 0 || node.ports?.outputs?.length > 0) && (
        <div>
          <div className="mb-1 text-[10px] text-terminal-500">Port Contracts</div>
          {node.ports?.inputs?.map(p => (
            <div key={p.id} className="font-mono text-[10px] text-terminal-300">
              <span className="text-blue-400">in</span> {p.name}: {p.data_type}
            </div>
          ))}
          {node.ports?.outputs?.map(p => (
            <div key={p.id} className="font-mono text-[10px] text-terminal-300">
              <span className="text-green-400">out</span> {p.name}: {p.data_type}
            </div>
          ))}
        </div>
      )}

      {/* Connected nodes */}
      {graph && (
        <div>
          <div className="mb-1 text-[10px] text-terminal-500">Connected Nodes</div>
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

      {/* Data Provenance */}
      {hasAnyMetrics && (
        <div>
          <div className="mb-1.5 flex items-center gap-1.5 text-[10px] text-terminal-500">
            Data Provenance
            {!hasProvenance && (
              <span className="italic text-amber-500/60">(incomplete)</span>
            )}
          </div>
          <div className="space-y-1.5 rounded-lg border border-terminal-700/50 bg-terminal-900/50 p-2.5">
            {/* Simulation source */}
            <div className="mb-1 text-[10px] font-medium text-terminal-400">Simulation Source</div>
            <ProvenanceRow
              label="Run ID"
              value={activeVersion?.metrics_run_id ? activeVersion.metrics_run_id.slice(0, 8) + '...' : null}
              mono
              warn={!activeVersion?.metrics_run_id}
            />
            {runDetail && (
              <>
                <ProvenanceRow label="Run Type" value={runDetail.run_type} />
                <ProvenanceRow label="Run Status" value={runDetail.run_status} />
                <ProvenanceRow
                  label="Completed"
                  value={runDetail.run_completed_at ? new Date(runDetail.run_completed_at).toLocaleString() : null}
                />
              </>
            )}

            {/* Results location */}
            <div className="mb-1 mt-2.5 text-[10px] font-medium text-terminal-400">Results Location</div>
            {activeVersion?.metrics_run_id ? (
              <div className="select-all break-all rounded bg-terminal-800 px-2 py-1.5 font-mono text-[10px] text-terminal-300">
                {`SELECT * FROM simulation_results WHERE run_id = '${activeVersion.metrics_run_id.slice(0, 8)}...'`}
              </div>
            ) : (
              <ProvenanceRow label="Query" value={null} warn />
            )}

            {/* Backfill context */}
            <div className="mb-1 mt-2.5 text-[10px] font-medium text-terminal-400">Backfill Context</div>
            <ProvenanceRow label="Date Range" value={formatDateRange(ctx)} warn={!formatDateRange(ctx)} />
            <ProvenanceRow label="Symbols" value={formatSymbolCount(ctx)} warn={!formatSymbolCount(ctx)} />
            <ProvenanceRow label="Timeframe" value={ctx?.timeframe} warn={!ctx?.timeframe} />
            <ProvenanceRow label="Slippage" value={ctx?.slippage_model || ctx?.slippage} />
            <ProvenanceRow label="Data Source" value={ctx?.data_source} />

            {/* Source & reproducibility */}
            <div className="mb-1 mt-2.5 text-[10px] font-medium text-terminal-400">Reproducibility</div>
            <ProvenanceRow label="Source Hash" value={activeVersion?.source_hash} mono />
            <ProvenanceRow
              label="Config"
              value={ctx?.config_file || ctx?.config_hash}
              mono
            />
            {ctx?.reproduce_cmd ? (
              <div className="mt-1">
                <div className="mb-0.5 text-[10px] text-terminal-500">Command</div>
                <div className="select-all break-all rounded bg-terminal-800 px-2 py-1.5 font-mono text-[10px] text-terminal-300">
                  {ctx.reproduce_cmd}
                </div>
              </div>
            ) : (
              <ProvenanceRow label="Command" value={null} warn />
            )}
          </div>
        </div>
      )}

      {/* Recent versions summary */}
      {versions.length > 0 && (
        <div>
          <div className="mb-1 text-[10px] text-terminal-500">Recent Versions</div>
          <div className="space-y-0.5">
            {versions.slice(0, 5).map(v => (
              <div key={v.version_id} className="font-mono text-[10px] text-terminal-300">
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
