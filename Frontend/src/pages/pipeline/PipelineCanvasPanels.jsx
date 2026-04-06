import { Panel } from '@xyflow/react'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

export default function PipelineCanvasPanels({
  chatOpen,
  clearLayoutPositions,
  graph,
  handleSearchChange,
  panelAwareFitView,
  recentActivityOpen,
  saveLayoutPositions,
  searchQuery,
  setChatOpen,
  setRecentActivityOpen,
  setSearchQuery,
  setSidebarOpen,
  setTvMode,
  sidebarOpen,
  tvMode,
  usingSavedLayout,
}) {
  return (
    <>
      {PIPELINE_FEATURES.searchFilter && (
        <Panel position="top-left">
          <div className="flex items-center gap-1.5 rounded-lg border border-terminal-600 bg-terminal-800/90 px-2 py-1 backdrop-blur">
            <span className="text-xs text-terminal-500">🔍</span>
            <input
              data-pipeline-search
              type="text"
              value={searchQuery}
              onChange={(event) => handleSearchChange(event.target.value)}
              placeholder="Search nodes... (press /)"
              className="w-40 bg-transparent text-[11px] text-terminal-200 placeholder:text-terminal-600 focus:outline-none"
            />
            {searchQuery && (
              <button
                onClick={() => {
                  setSearchQuery('')
                  graph.clearSelection()
                }}
                className="text-terminal-500 hover:text-terminal-300"
              >
                <span className="text-xs">✕</span>
              </button>
            )}
          </div>
        </Panel>
      )}

      {PIPELINE_FEATURES.focusMode && graph.focusBreadcrumb && (
        <Panel position="top-left" style={{ top: 42 }}>
          <div className="flex items-center gap-1.5 rounded-lg border border-terminal-600 bg-terminal-800/90 px-2.5 py-1.5 backdrop-blur">
            {graph.focusBreadcrumb.map((crumb, index) => (
              <span key={index} className="flex items-center gap-1.5">
                {index > 0 && <span className="text-[10px] text-terminal-600">›</span>}
                {crumb.action ? (
                  <button
                    onClick={() => {
                      if (crumb.action === 'clear') {
                        graph.clearFocusMode()
                      } else if (crumb.action === 'subsystem') {
                        const node = graph.nodeMap[graph.focusTarget]
                        if (node?.subsystem) {
                          graph.setFocusMode('subsystem', node.subsystem)
                        }
                      }
                    }}
                    className="text-[10px] text-amber-400 underline hover:text-amber-300"
                  >
                    {crumb.label}
                  </button>
                ) : (
                  <span className="text-[10px] text-terminal-300">{crumb.label}</span>
                )}
              </span>
            ))}
          </div>
        </Panel>
      )}

      <Panel position="top-center">
        <div className="max-w-md rounded-lg border border-terminal-600 bg-terminal-800/90 px-3 py-2 text-[10px] leading-4 text-terminal-400 backdrop-blur">
          Pipeline access follows the app route gate and server settings. Authentication is handled via your session.
        </div>
      </Panel>

      {PIPELINE_FEATURES.lineageDepthSlider && graph.selectedNodeId && (
        <Panel position="bottom-left" style={{ bottom: 50 }}>
          <div className="flex items-center gap-2 rounded-lg border border-terminal-600 bg-terminal-800/90 px-3 py-2 backdrop-blur">
            <span className="whitespace-nowrap text-[10px] text-terminal-400">Depth:</span>
            <input
              type="range"
              min="1"
              max="10"
              value={graph.lineageDepth || 10}
              onChange={(event) => {
                const value = parseInt(event.target.value)
                graph.setLineageDepth(value >= 10 ? null : value)
              }}
              className="h-1 w-20 accent-amber-400"
            />
            <span className="w-6 text-right font-mono text-[10px] text-terminal-300">
              {graph.lineageDepth || '∞'}
            </span>
          </div>
        </Panel>
      )}

      <Panel position="top-right">
        <div className="flex gap-2">
          {PIPELINE_FEATURES.splitPanelSidebar && (
            <button
              onClick={() => setSidebarOpen((previous) => !previous)}
              className={`pipeline-touch-target flex items-center justify-center rounded-lg border bg-terminal-800/90 px-3 py-2 text-xs backdrop-blur transition-colors hover:bg-terminal-700 hover:text-terminal-100 ${sidebarOpen ? 'border-amber-500 text-amber-400' : 'border-terminal-600 text-terminal-300'}`}
              title="Toggle node tree sidebar"
            >
              Tree
            </button>
          )}
          <button
            onClick={panelAwareFitView}
            className="pipeline-touch-target flex items-center justify-center rounded-lg border border-terminal-600 bg-terminal-800/90 px-3 py-2 text-xs text-terminal-300 backdrop-blur transition-colors hover:bg-terminal-700 hover:text-terminal-100"
            title="Fit graph to view (accounts for open panels)"
          >
            Fit
          </button>
          {usingSavedLayout ? (
            <button
              onClick={clearLayoutPositions}
              className="pipeline-touch-target flex items-center justify-center rounded-lg border border-amber-500 bg-terminal-800/90 px-3 py-2 text-xs text-amber-400 backdrop-blur transition-colors hover:bg-terminal-700 hover:text-terminal-100"
              title="Clear saved layout and revert to auto-layout"
            >
              Reset Layout
            </button>
          ) : (
            <button
              onClick={saveLayoutPositions}
              className="pipeline-touch-target flex items-center justify-center rounded-lg border border-terminal-600 bg-terminal-800/90 px-3 py-2 text-xs text-terminal-300 backdrop-blur transition-colors hover:bg-terminal-700 hover:text-terminal-100"
              title="Save current node positions as default layout"
            >
              Set Default
            </button>
          )}
          <button
            onClick={() => setTvMode((previous) => !previous)}
            className={`pipeline-touch-target flex items-center justify-center rounded-lg border bg-terminal-800/90 px-3 py-2 text-xs backdrop-blur transition-colors hover:bg-terminal-700 hover:text-terminal-100 ${tvMode ? 'border-amber-500 text-amber-400' : 'border-terminal-600 text-terminal-300'}`}
            title="Toggle TV mode (larger text for distant viewing)"
          >
            TV
          </button>
          <button
            onClick={() => setRecentActivityOpen((previous) => !previous)}
            className={`pipeline-touch-target flex items-center justify-center rounded-lg border bg-terminal-800/90 px-3 py-2 text-xs backdrop-blur transition-colors hover:bg-terminal-700 hover:text-terminal-100 ${recentActivityOpen ? 'border-amber-500 text-amber-400' : 'border-terminal-600 text-terminal-300'}`}
            title="Recent pipeline activity"
          >
            Activity
          </button>
          <button
            onClick={() => setChatOpen(!chatOpen)}
            className="pipeline-touch-target flex items-center justify-center rounded-lg border border-terminal-600 bg-terminal-800/90 px-3 py-2 text-xs text-terminal-300 backdrop-blur transition-colors hover:bg-terminal-700 hover:text-terminal-100"
            title="Chat is disabled until a server-backed proxy exists"
          >
            {chatOpen ? 'Close Notice' : 'Chat Off'}
          </button>
        </div>
      </Panel>

      {graph.sliceFrom && !graph.sliceTo && (
        <Panel position="bottom-center">
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/20 px-4 py-2 text-xs text-amber-300 backdrop-blur">
            Hold Shift + Click on another node to slice pipeline
            <button
              onClick={() => graph.clearSlice()}
              className="ml-3 text-amber-400 underline hover:text-amber-200"
            >
              Cancel
            </button>
          </div>
        </Panel>
      )}

      {graph.sliceFrom && graph.sliceTo && (
        <Panel position="bottom-center">
          <div className="flex items-center gap-3 rounded-lg border border-green-500/30 bg-green-500/20 px-4 py-2 text-xs text-green-300 backdrop-blur">
            Pipeline sliced: {graph.sliceNodeIds?.size || 0} nodes in path
            <button
              onClick={() => graph.clearSlice()}
              className="text-green-400 underline hover:text-green-200"
            >
              Clear slice
            </button>
          </div>
        </Panel>
      )}
    </>
  )
}
