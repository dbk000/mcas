import { useState, useMemo } from 'react'
import VersionRow from './VersionRow'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

export default function VersionsTab({
  versions,
  loadingVersions,
  pointInTime,
  setPointInTime,
  showAll,
  setShowAll,
  showFrontierOnly,
  setShowFrontierOnly,
  expandedVersion,
  setExpandedVersion,
  selectedVersionForBio,
  setSelectedVersionForBio,
  setActiveTab,
  diffVersionA,
  setDiffVersionA,
  diffVersionB,
  setDiffVersionB,
  diffData,
  setDiffData,
  loadingDiff,
  handleDiff,
}) {
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

      {/* Version diff viewer (Wave 3.5: enhanced diff visualization) */}
      {(diffVersionA && diffVersionB) && (
        <DiffViewer
          diffVersionA={diffVersionA}
          diffVersionB={diffVersionB}
          diffData={diffData}
          loadingDiff={loadingDiff}
          handleDiff={handleDiff}
          versions={versions}
        />
      )}
    </div>
  )
}


// Wave 3.5 + 3.9: Enhanced diff visualization with compact/inline toggle
function DiffViewer({ diffVersionA, diffVersionB, diffData, loadingDiff, handleDiff, versions }) {
  const [diffMode, setDiffMode] = useState('compact') // 'compact' | 'inline'
  const [expandedSections, setExpandedSections] = useState(new Set(['params', 'metrics']))

  const vA = versions.find(v => v.version_id === diffVersionA)
  const vB = versions.find(v => v.version_id === diffVersionB)

  const toggleSection = (section) => {
    setExpandedSections(prev => {
      const next = new Set(prev)
      next.has(section) ? next.delete(section) : next.add(section)
      return next
    })
  }

  const hasParams = Object.keys(diffData?.changed_params || {}).length > 0
  const hasMetrics = Object.keys(diffData?.metric_delta || {}).length > 0
  const hasStatus = diffData && (diffData.status_a !== diffData.status_b)
  const hasStaleness = PIPELINE_FEATURES.threeStateStaleness && diffData &&
    (vA?.staleness_state !== vB?.staleness_state || vA?.health_status !== vB?.health_status)

  return (
    <div className="mt-3 border-t border-terminal-700 pt-3">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-terminal-300">
            Version Diff: v{vA?.version_number || '?'} {'\u2192'} v{vB?.version_number || '?'}
          </span>
          {diffData && (
            <div className="flex gap-0.5">
              <button
                onClick={() => setDiffMode('compact')}
                className={`text-[9px] px-1.5 py-0.5 rounded ${diffMode === 'compact' ? 'bg-amber-500/20 text-amber-400' : 'text-terminal-500 hover:text-terminal-300'}`}
              >
                Compact
              </button>
              <button
                onClick={() => setDiffMode('inline')}
                className={`text-[9px] px-1.5 py-0.5 rounded ${diffMode === 'inline' ? 'bg-amber-500/20 text-amber-400' : 'text-terminal-500 hover:text-terminal-300'}`}
              >
                Inline
              </button>
            </div>
          )}
        </div>
        <button
          onClick={handleDiff}
          disabled={loadingDiff}
          className="text-[10px] px-2 py-1 bg-amber-600/20 text-amber-400 rounded hover:bg-amber-600/30 disabled:opacity-30"
        >
          {loadingDiff ? 'Loading...' : 'Compare'}
        </button>
      </div>

      {diffData && (
        <div className="bg-terminal-900 rounded border border-terminal-700 overflow-hidden">
          {/* Summary bar */}
          <div className="flex items-center gap-3 px-2 py-1.5 bg-terminal-800/50 border-b border-terminal-700">
            {hasParams && <span className="text-[9px] text-amber-400">{Object.keys(diffData.changed_params).length} params</span>}
            {hasMetrics && <span className="text-[9px] text-blue-400">{Object.keys(diffData.metric_delta).length} metrics</span>}
            {hasStatus && <span className="text-[9px] text-purple-400">status changed</span>}
            {hasStaleness && <span className="text-[9px] text-red-400">health/staleness</span>}
            {!hasParams && !hasMetrics && !hasStatus && !hasStaleness && (
              <span className="text-[9px] text-terminal-500">No differences found</span>
            )}
          </div>

          {/* Parameter changes section */}
          {hasParams && (
            <DiffSection
              title="Changed Parameters"
              expanded={expandedSections.has('params')}
              onToggle={() => toggleSection('params')}
              count={Object.keys(diffData.changed_params).length}
            >
              {diffMode === 'compact' ? (
                Object.entries(diffData.changed_params).map(([k, v]) => (
                  <div key={k} className="flex items-center text-[10px] font-mono gap-2 px-2 py-0.5">
                    <span className="text-terminal-400 min-w-[80px]">{k}:</span>
                    <span className="text-red-400 bg-red-500/10 px-1 rounded">{JSON.stringify(v.a)}</span>
                    <span className="text-terminal-500">{'\u2192'}</span>
                    <span className="text-green-400 bg-green-500/10 px-1 rounded">{JSON.stringify(v.b)}</span>
                  </div>
                ))
              ) : (
                Object.entries(diffData.changed_params).map(([k, v]) => (
                  <div key={k} className="px-2 py-1 border-b border-terminal-800 last:border-b-0">
                    <div className="text-[10px] font-medium text-terminal-300 mb-0.5">{k}</div>
                    <div className="text-[10px] font-mono text-red-400 bg-red-500/5 px-1.5 py-0.5 rounded">- {JSON.stringify(v.a)}</div>
                    <div className="text-[10px] font-mono text-green-400 bg-green-500/5 px-1.5 py-0.5 rounded mt-0.5">+ {JSON.stringify(v.b)}</div>
                  </div>
                ))
              )}
            </DiffSection>
          )}

          {/* Metric deltas section */}
          {hasMetrics && (
            <DiffSection
              title="Metric Deltas"
              expanded={expandedSections.has('metrics')}
              onToggle={() => toggleSection('metrics')}
              count={Object.keys(diffData.metric_delta).length}
            >
              {Object.entries(diffData.metric_delta).map(([k, v]) => (
                <div key={k} className="flex items-center text-[10px] font-mono gap-2 px-2 py-0.5">
                  <span className="text-terminal-400 min-w-[80px]">{k}:</span>
                  {v.delta != null ? (
                    <>
                      <span className="text-terminal-500">{typeof v.a === 'number' ? v.a.toFixed(4) : v.a ?? 'N/A'}</span>
                      <span className="text-terminal-600">{'\u2192'}</span>
                      <span className="text-terminal-300">{typeof v.b === 'number' ? v.b.toFixed(4) : v.b ?? 'N/A'}</span>
                      <span className={`ml-1 ${v.delta > 0 ? 'text-green-400' : v.delta < 0 ? 'text-red-400' : 'text-terminal-500'}`}>
                        ({v.delta > 0 ? '+' : ''}{typeof v.delta === 'number' ? v.delta.toFixed(4) : v.delta})
                      </span>
                    </>
                  ) : (
                    <span className="text-terminal-500">N/A</span>
                  )}
                </div>
              ))}
            </DiffSection>
          )}

          {/* Status change section */}
          {hasStatus && (
            <DiffSection
              title="Status Change"
              expanded={expandedSections.has('status')}
              onToggle={() => toggleSection('status')}
            >
              <div className="flex items-center text-[10px] font-mono gap-2 px-2 py-0.5">
                <span className="text-red-400">{diffData.status_a}</span>
                <span className="text-terminal-500">{'\u2192'}</span>
                <span className="text-green-400">{diffData.status_b}</span>
              </div>
            </DiffSection>
          )}

          {/* Staleness/health change section */}
          {hasStaleness && (
            <DiffSection
              title="Health & Staleness"
              expanded={expandedSections.has('health')}
              onToggle={() => toggleSection('health')}
            >
              {vA?.staleness_state !== vB?.staleness_state && (
                <div className="flex items-center text-[10px] font-mono gap-2 px-2 py-0.5">
                  <span className="text-terminal-400 min-w-[80px]">staleness:</span>
                  <span className="text-amber-400">{vA?.staleness_state || 'clean'}</span>
                  <span className="text-terminal-500">{'\u2192'}</span>
                  <span className="text-amber-300">{vB?.staleness_state || 'clean'}</span>
                </div>
              )}
              {vA?.health_status !== vB?.health_status && (
                <div className="flex items-center text-[10px] font-mono gap-2 px-2 py-0.5">
                  <span className="text-terminal-400 min-w-[80px]">health:</span>
                  <span className="text-amber-400">{vA?.health_status || 'unknown'}</span>
                  <span className="text-terminal-500">{'\u2192'}</span>
                  <span className="text-amber-300">{vB?.health_status || 'unknown'}</span>
                </div>
              )}
            </DiffSection>
          )}
        </div>
      )}
    </div>
  )
}


function DiffSection({ title, expanded, onToggle, count, children }) {
  return (
    <div className="border-b border-terminal-800 last:border-b-0">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-2 py-1.5 text-left hover:bg-terminal-800/50 transition-colors"
      >
        <span className="text-[10px] text-terminal-400 font-medium">{title}</span>
        <div className="flex items-center gap-1">
          {count != null && <span className="text-[9px] text-terminal-500">{count}</span>}
          <span className="text-[9px] text-terminal-600">{expanded ? '\u25B2' : '\u25BC'}</span>
        </div>
      </button>
      {expanded && <div className="pb-1.5">{children}</div>}
    </div>
  )
}
