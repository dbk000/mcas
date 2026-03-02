import { useState, useEffect, useMemo } from 'react'
import { X, ChevronDown, ChevronUp, Copy, Check, Save, Clipboard } from 'lucide-react'
import { useApi } from '../../context/ApiContext'
import VersionEventTimeline from './VersionEventTimeline'

const STATUS_COLORS = {
  deployed: 'bg-green-500/20 text-green-400 border-green-500/30',
  promising: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  tested: 'bg-terminal-600 text-terminal-300 border-terminal-500',
  baseline: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  retired: 'bg-red-500/20 text-red-400 border-red-500/30',
}

export default function NodeDetailPanel({ node, onClose, graph }) {
  const { get, post } = useApi()
  const [versions, setVersions] = useState([])
  const [loadingVersions, setLoadingVersions] = useState(false)
  const [showAll, setShowAll] = useState(false)
  const [showFrontierOnly, setShowFrontierOnly] = useState(false)
  const [expandedVersion, setExpandedVersion] = useState(null)
  const [activeTab, setActiveTab] = useState('versions') // 'versions' | 'biography' | 'params' | 'context'
  const [selectedVersionForBio, setSelectedVersionForBio] = useState(null)
  const [diffVersionA, setDiffVersionA] = useState(null)
  const [diffVersionB, setDiffVersionB] = useState(null)
  const [diffData, setDiffData] = useState(null)
  const [loadingDiff, setLoadingDiff] = useState(false)
  const [editParams, setEditParams] = useState({})
  const [changeReason, setChangeReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [copied, setCopied] = useState(false)
  const [contextExpanded, setContextExpanded] = useState(false)
  const [pointInTime, setPointInTime] = useState('')

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
        if (!cancelled) {
          setVersions(data)
          // Auto-select current version for biography
          if (data.length > 0) {
            setSelectedVersionForBio(data[0]?.version_id)
          }
        }
      } catch (err) {
        console.error('Failed to fetch versions:', err)
      } finally {
        if (!cancelled) setLoadingVersions(false)
      }
    }
    fetchVersions()
    setActiveTab('versions')
    setDiffData(null)
    setDiffVersionA(null)
    setDiffVersionB(null)
    return () => { cancelled = true }
  }, [nodeId, get])

  // Init edit params from current version
  useEffect(() => {
    const cv = node.current_version || node.currentVersion
    if (cv?.parameters) {
      setEditParams({ ...cv.parameters })
    } else {
      setEditParams({})
    }
    setChangeReason('')
  }, [node])

  // Filter versions by significance
  const filteredVersions = showAll
    ? versions
    : (() => {
        if (versions.length <= 5) return versions
        const sigs = versions.map(v => v.significance || 0).sort((a, b) => a - b)
        const median = sigs[Math.floor(sigs.length / 2)]
        return versions.filter(v => (v.significance || 0) >= median)
      })()

  // Point-in-time filtered versions + optional Pareto frontier filter
  const timeFilteredVersions = useMemo(() => {
    let result = filteredVersions
    if (pointInTime) {
      const cutoff = new Date(pointInTime).getTime()
      result = result.filter(v => {
        const created = new Date(v.created_at).getTime()
        return created <= cutoff
      })
    }
    if (showFrontierOnly) {
      result = result.filter(v => !v.pareto_status || v.pareto_status === 'active')
    }
    return result
  }, [filteredVersions, pointInTime, showFrontierOnly])

  const cv = node.current_version || node.currentVersion

  // Diff two versions
  const handleDiff = async () => {
    if (!diffVersionA || !diffVersionB) return
    setLoadingDiff(true)
    try {
      const resp = await get(`/v1/meta/compare/versions/${diffVersionA}/${diffVersionB}`)
      setDiffData(resp?.data || resp)
    } catch (err) {
      console.error('Diff failed:', err)
    } finally {
      setLoadingDiff(false)
    }
  }

  // Save new version from edited params
  const handleSaveVersion = async () => {
    if (!changeReason.trim()) return
    setSaving(true)
    try {
      await post('/v1/meta/node/version', {
        node_id: nodeId,
        parameters: editParams,
        change_reason: changeReason,
        status: 'tested',
      })
      // Refresh versions
      const resp = await get(`/v1/meta/node/${nodeId}/versions?limit=100`)
      setVersions(resp?.versions || resp?.data?.versions || [])
      setChangeReason('')
      // Trigger pressure wave
      graph?.triggerPressureWave?.(nodeId)
    } catch (err) {
      console.error('Save version failed:', err)
    } finally {
      setSaving(false)
    }
  }

  // Copy context to clipboard
  const handleCopyContext = () => {
    const lines = [
      `## Node Context: ${node.name || node.label}`,
      '',
      `**Type:** ${node.type || node.nodeType}`,
      `**Subsystem:** ${node.subsystem}`,
      node.description ? `**Description:** ${node.description}` : null,
      node.source_file ? `**Source:** ${node.source_file}` : null,
      node.owner ? `**Owner:** ${node.owner}` : null,
      '',
    ].filter(Boolean)

    if (cv) {
      lines.push(`### Current Version: v${cv.version_number} (${cv.status})`)
      if (cv.agg_pf != null) lines.push(`- PF: ${cv.agg_pf.toFixed(2)}`)
      if (cv.win_rate != null) lines.push(`- Win Rate: ${(cv.win_rate * 100).toFixed(1)}%`)
      if (cv.trade_count != null) lines.push(`- Trades: ${cv.trade_count}`)
      if (cv.net_pnl != null) lines.push(`- PnL: $${cv.net_pnl.toFixed(0)}`)
      lines.push('')
    }

    // Ports
    const ports = node.ports || {}
    if (ports.inputs?.length || ports.outputs?.length) {
      lines.push('### Ports')
      if (ports.inputs?.length) {
        lines.push(`**Inputs:** ${ports.inputs.map(p => `${p.name} (${p.data_type})`).join(', ')}`)
      }
      if (ports.outputs?.length) {
        lines.push(`**Outputs:** ${ports.outputs.map(p => `${p.name} (${p.data_type})`).join(', ')}`)
      }
      lines.push('')
    }

    // Recent versions
    if (versions.length > 0) {
      lines.push('### Recent Versions')
      versions.slice(0, 5).forEach(v => {
        lines.push(`- v${v.version_number} (${v.status}): ${v.change_reason || 'no reason'} — PF ${v.agg_pf?.toFixed(2) || '?'}`)
      })
    }

    navigator.clipboard.writeText(lines.join('\n')).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  // Timeline scrubber: significance sparkline
  const significanceData = useMemo(() => {
    return versions.map(v => ({
      id: v.version_id,
      number: v.version_number,
      significance: v.significance || 0,
      status: v.status,
      date: v.created_at,
    })).sort((a, b) => a.number - b.number)
  }, [versions])

  const maxSig = Math.max(...significanceData.map(d => d.significance), 0.01)

  return (
    <div className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 shadow-xl z-50 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-terminal-600 bg-terminal-800/95 backdrop-blur">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold text-terminal-100 truncate">{node.name || node.label}</h2>
          <p className="text-xs text-terminal-400 capitalize">{node.subsystem} / {node.type || node.nodeType}</p>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleCopyContext}
            className="p-1 text-terminal-400 hover:text-terminal-100 transition-colors"
            title="Copy context to clipboard"
          >
            {copied ? <Check className="w-4 h-4 text-green-400" /> : <Clipboard className="w-4 h-4" />}
          </button>
          <button
            onClick={onClose}
            className="p-1 text-terminal-400 hover:text-terminal-100 transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-terminal-700 px-2">
        {['versions', 'biography', 'params', 'context'].map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-3 py-2 text-[10px] font-medium capitalize transition-colors border-b-2 ${
              activeTab === tab
                ? 'text-amber-400 border-amber-400'
                : 'text-terminal-500 border-transparent hover:text-terminal-300'
            }`}
          >
            {tab}
          </button>
        ))}
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto">
        {/* Node info (always visible) */}
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

        {/* === VERSIONS TAB === */}
        {activeTab === 'versions' && (
          <div className="px-4 py-3">
            {/* Point-in-time selector */}
            <div className="flex items-center gap-2 mb-2">
              <label className="text-[10px] text-terminal-500">Point-in-time:</label>
              <input
                type="datetime-local"
                value={pointInTime}
                onChange={e => setPointInTime(e.target.value)}
                className="flex-1 text-[10px] bg-terminal-900 border border-terminal-700 rounded px-1.5 py-1 text-terminal-300"
              />
              {pointInTime && (
                <button onClick={() => setPointInTime('')} className="text-[10px] text-amber-400">
                  Clear
                </button>
              )}
            </div>

            <div className="flex items-center justify-between mb-2">
              <div className="text-xs font-medium text-terminal-300">
                Version History
                <span className="text-terminal-500 ml-1">
                  ({timeFilteredVersions.length}{!showAll && versions.length > timeFilteredVersions.length ? ` of ${versions.length}` : ''})
                </span>
              </div>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setShowFrontierOnly(!showFrontierOnly)}
                  className={`text-[10px] transition-colors ${showFrontierOnly ? 'text-green-400 hover:text-green-300' : 'text-terminal-500 hover:text-terminal-400'}`}
                  title={showFrontierOnly ? 'Show all versions' : 'Show frontier only'}
                >
                  {showFrontierOnly ? 'Frontier' : 'All Pareto'}
                </button>
                {versions.length > 5 && (
                  <button
                    onClick={() => setShowAll(!showAll)}
                    className="text-[10px] text-amber-400 hover:text-amber-300 transition-colors"
                  >
                    {showAll ? 'Show significant' : 'Show all'}
                  </button>
                )}
              </div>
            </div>

            {/* Timeline scrubber */}
            {significanceData.length > 1 && (
              <div className="h-10 mb-2 bg-terminal-900 rounded border border-terminal-700 flex items-end px-1 gap-px overflow-hidden">
                {significanceData.map(d => {
                  const height = Math.max(4, (d.significance / maxSig) * 32)
                  const barColor = d.status === 'deployed' ? '#22c55e' :
                    d.status === 'promising' ? '#f59e0b' : '#475569'
                  const isSelected = d.id === diffVersionA || d.id === diffVersionB
                  return (
                    <button
                      key={d.id}
                      onClick={(e) => {
                        if (e.shiftKey && diffVersionA && !diffVersionB) {
                          setDiffVersionB(d.id)
                        } else {
                          setDiffVersionA(d.id)
                          setDiffVersionB(null)
                          setDiffData(null)
                        }
                        setSelectedVersionForBio(d.id)
                      }}
                      className={`flex-1 min-w-[3px] max-w-[12px] rounded-t transition-all ${isSelected ? 'ring-1 ring-amber-400' : ''}`}
                      style={{ height: `${height}px`, backgroundColor: barColor }}
                      title={`v${d.number} — sig: ${d.significance.toFixed(2)}`}
                    />
                  )
                })}
              </div>
            )}

            {loadingVersions ? (
              <div className="text-xs text-terminal-500 text-center py-4">Loading versions...</div>
            ) : timeFilteredVersions.length === 0 ? (
              <div className="text-xs text-terminal-500 text-center py-4">No versions recorded</div>
            ) : (
              <div className="space-y-1">
                {timeFilteredVersions.map((v) => (
                  <VersionRow
                    key={v.version_id}
                    version={v}
                    expanded={expandedVersion === v.version_id}
                    onToggle={() => setExpandedVersion(
                      expandedVersion === v.version_id ? null : v.version_id
                    )}
                    isSelectedForBio={v.version_id === selectedVersionForBio}
                    onSelectForBio={() => {
                      setSelectedVersionForBio(v.version_id)
                      setActiveTab('biography')
                    }}
                  />
                ))}
              </div>
            )}

            {/* Version diff viewer */}
            {(diffVersionA && diffVersionB) && (
              <div className="mt-3 border-t border-terminal-700 pt-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-medium text-terminal-300">Version Diff</span>
                  <button
                    onClick={handleDiff}
                    disabled={loadingDiff}
                    className="text-[10px] px-2 py-1 bg-amber-600/20 text-amber-400 rounded hover:bg-amber-600/30 disabled:opacity-30"
                  >
                    {loadingDiff ? 'Loading...' : 'Compare'}
                  </button>
                </div>
                {diffData && (
                  <div className="bg-terminal-900 rounded p-2 space-y-2">
                    {/* Changed params */}
                    {Object.keys(diffData.changed_params || {}).length > 0 && (
                      <div>
                        <div className="text-[10px] text-terminal-500 mb-1">Changed Parameters</div>
                        {Object.entries(diffData.changed_params).map(([k, v]) => (
                          <div key={k} className="flex items-center text-[10px] font-mono gap-2">
                            <span className="text-terminal-400">{k}:</span>
                            <span className="text-red-400">{JSON.stringify(v.a)}</span>
                            <span className="text-terminal-500">\u2192</span>
                            <span className="text-green-400">{JSON.stringify(v.b)}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {/* Metric deltas */}
                    {Object.keys(diffData.metric_delta || {}).length > 0 && (
                      <div>
                        <div className="text-[10px] text-terminal-500 mb-1">Metric Deltas</div>
                        {Object.entries(diffData.metric_delta).map(([k, v]) => (
                          <div key={k} className="flex items-center text-[10px] font-mono gap-2">
                            <span className="text-terminal-400">{k}:</span>
                            {v.delta != null ? (
                              <span className={v.delta > 0 ? 'text-green-400' : v.delta < 0 ? 'text-red-400' : 'text-terminal-400'}>
                                {v.delta > 0 ? '+' : ''}{typeof v.delta === 'number' ? v.delta.toFixed(4) : v.delta}
                              </span>
                            ) : (
                              <span className="text-terminal-500">N/A</span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {/* === BIOGRAPHY TAB === */}
        {activeTab === 'biography' && (
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
                onCausalClick={(eventId) => {
                  // Could trigger causal arrow on canvas in future
                  console.log('Causal click:', eventId)
                }}
              />
            )}
          </div>
        )}

        {/* === PARAMS TAB (Control Surface) === */}
        {activeTab === 'params' && (
          <div className="px-4 py-3">
            <div className="text-xs font-medium text-terminal-300 mb-3">Edit Parameters</div>
            <div className="space-y-2">
              {Object.entries(editParams).map(([key, value]) => (
                <div key={key} className="flex items-center gap-2">
                  <label className="text-[10px] text-terminal-400 font-mono w-1/2 truncate" title={key}>
                    {key}
                  </label>
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
        )}

        {/* === CONTEXT TAB === */}
        {activeTab === 'context' && (
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
        )}
      </div>
    </div>
  )
}

const PARETO_BADGES = {
  active: { label: 'Frontier', cls: 'bg-green-500/15 text-green-400 border-green-500/20' },
  dominated: { label: 'Dominated', cls: 'bg-terminal-700 text-terminal-500 border-terminal-600' },
  historical: { label: 'Historical', cls: 'bg-terminal-800 text-terminal-600 border-terminal-700' },
}

function VersionRow({ version: v, expanded, onToggle, isSelectedForBio, onSelectForBio }) {
  const paretoBadge = PARETO_BADGES[v.pareto_status]
  const isDominated = v.pareto_status === 'dominated' || v.pareto_status === 'historical'

  return (
    <div className={`bg-terminal-900 rounded border ${isSelectedForBio ? 'border-amber-500/50' : 'border-terminal-700/50'} ${isDominated ? 'opacity-60' : ''}`}>
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-2.5 py-1.5 text-left hover:bg-terminal-700/30 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-mono text-terminal-200 flex-shrink-0">v{v.version_number}</span>
          <span className={`text-[10px] px-1 py-0.5 rounded border flex-shrink-0 ${STATUS_COLORS[v.status] || STATUS_COLORS.tested}`}>
            {v.status}
          </span>
          {paretoBadge && (
            <span className={`text-[9px] px-1 py-0.5 rounded border flex-shrink-0 ${paretoBadge.cls}`}>
              {paretoBadge.label}
            </span>
          )}
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
          {v.superseded_by && (
            <div className="text-[10px] text-terminal-500 mt-1">
              Superseded by: <span className="text-amber-400 font-mono">{v.superseded_by.slice(0, 8)}...</span>
            </div>
          )}
          <button
            onClick={(e) => { e.stopPropagation(); onSelectForBio() }}
            className="text-[10px] text-amber-400 hover:text-amber-300 mt-1"
          >
            View biography \u2192
          </button>
        </div>
      )}
    </div>
  )
}
