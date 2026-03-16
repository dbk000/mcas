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
      <div className="mb-2 flex items-center gap-2">
        <label className="text-[10px] text-terminal-500">Point-in-time:</label>
        <input
          type="datetime-local"
          value={pointInTime}
          onChange={e => setPointInTime(e.target.value)}
          className="flex-1 rounded border border-terminal-700 bg-terminal-900 px-1.5 py-1 text-[10px] text-terminal-300"
        />
        {pointInTime && (
          <button onClick={() => setPointInTime('')} className="text-[10px] text-amber-400">
            Clear
          </button>
        )}
      </div>

      <div className="mb-2 flex items-center justify-between">
        <div className="text-xs font-medium text-terminal-300">
          Version History
          <span className="ml-1 text-terminal-500">
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
              className="text-[10px] text-amber-400 transition-colors hover:text-amber-300"
            >
              {showAll ? 'Show significant' : 'Show all'}
            </button>
          )}
        </div>
      </div>

      {/* Timeline scrubber */}
      {significanceData.length > 1 && (
        <div className="mb-2 flex h-10 items-end gap-px overflow-hidden rounded border border-terminal-700 bg-terminal-900 px-1">
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
                className={`min-w-[3px] max-w-[12px] flex-1 rounded-t transition-all ${isSelected ? 'ring-1 ring-amber-400' : ''}`}
                style={{ height: `${height}px`, backgroundColor: barColor }}
                title={`v${d.number} — sig: ${d.significance.toFixed(2)}`}
              />
            )
          })}
        </div>
      )}

      {loadingVersions ? (
        <div className="py-4 text-center text-xs text-terminal-500">Loading versions...</div>
      ) : timeFilteredVersions.length === 0 ? (
        <div className="py-4 text-center text-xs text-terminal-500">No versions recorded</div>
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
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[10px] font-medium text-terminal-300">
            Version Diff: v{vA?.version_number || '?'} {'\u2192'} v{vB?.version_number || '?'}
          </span>
          {diffData && (
            <div className="flex gap-0.5">
              <button
                onClick={() => setDiffMode('compact')}
                className={`rounded px-1.5 py-0.5 text-[9px] ${diffMode === 'compact' ? 'bg-amber-500/20 text-amber-400' : 'text-terminal-500 hover:text-terminal-300'}`}
              >
                Compact
              </button>
              <button
                onClick={() => setDiffMode('inline')}
                className={`rounded px-1.5 py-0.5 text-[9px] ${diffMode === 'inline' ? 'bg-amber-500/20 text-amber-400' : 'text-terminal-500 hover:text-terminal-300'}`}
              >
                Inline
              </button>
            </div>
          )}
        </div>
        <button
          onClick={handleDiff}
          disabled={loadingDiff}
          className="rounded bg-amber-600/20 px-2 py-1 text-[10px] text-amber-400 hover:bg-amber-600/30 disabled:opacity-30"
        >
          {loadingDiff ? 'Loading...' : 'Compare'}
        </button>
      </div>

      {diffData && (
        <div className="overflow-hidden rounded border border-terminal-700 bg-terminal-900">
          {/* Summary bar */}
          <div className="flex items-center gap-3 border-b border-terminal-700 bg-terminal-800/50 px-2 py-1.5">
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
                  <div key={k} className="flex items-center gap-2 px-2 py-0.5 font-mono text-[10px]">
                    <span className="min-w-[80px] text-terminal-400">{k}:</span>
                    <span className="rounded bg-red-500/10 px-1 text-red-400">{JSON.stringify(v.a)}</span>
                    <span className="text-terminal-500">{'\u2192'}</span>
                    <span className="rounded bg-green-500/10 px-1 text-green-400">{JSON.stringify(v.b)}</span>
                  </div>
                ))
              ) : (
                Object.entries(diffData.changed_params).map(([k, v]) => (
                  <div key={k} className="border-b border-terminal-800 px-2 py-1 last:border-b-0">
                    <div className="mb-0.5 text-[10px] font-medium text-terminal-300">{k}</div>
                    <div className="rounded bg-red-500/5 px-1.5 py-0.5 font-mono text-[10px] text-red-400">- {JSON.stringify(v.a)}</div>
                    <div className="mt-0.5 rounded bg-green-500/5 px-1.5 py-0.5 font-mono text-[10px] text-green-400">+ {JSON.stringify(v.b)}</div>
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
                <div key={k} className="flex items-center gap-2 px-2 py-0.5 font-mono text-[10px]">
                  <span className="min-w-[80px] text-terminal-400">{k}:</span>
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
              <div className="flex items-center gap-2 px-2 py-0.5 font-mono text-[10px]">
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
                <div className="flex items-center gap-2 px-2 py-0.5 font-mono text-[10px]">
                  <span className="min-w-[80px] text-terminal-400">staleness:</span>
                  <span className="text-amber-400">{vA?.staleness_state || 'clean'}</span>
                  <span className="text-terminal-500">{'\u2192'}</span>
                  <span className="text-amber-300">{vB?.staleness_state || 'clean'}</span>
                </div>
              )}
              {vA?.health_status !== vB?.health_status && (
                <div className="flex items-center gap-2 px-2 py-0.5 font-mono text-[10px]">
                  <span className="min-w-[80px] text-terminal-400">health:</span>
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
        className="flex w-full items-center justify-between px-2 py-1.5 text-left transition-colors hover:bg-terminal-800/50"
      >
        <span className="text-[10px] font-medium text-terminal-400">{title}</span>
        <div className="flex items-center gap-1">
          {count != null && <span className="text-[9px] text-terminal-500">{count}</span>}
          <span className="text-[9px] text-terminal-600">{expanded ? '\u25B2' : '\u25BC'}</span>
        </div>
      </button>
      {expanded && <div className="pb-1.5">{children}</div>}
    </div>
  )
}
