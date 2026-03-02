import { useState, useMemo } from 'react'
import VersionRow from './VersionRow'

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
                      <span className="text-terminal-500">{'\u2192'}</span>
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
  )
}
