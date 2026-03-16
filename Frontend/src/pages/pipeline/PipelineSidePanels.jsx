import PipelineErrorBoundary from '../../components/pipeline/PipelineErrorBoundary'
import PipelineSidebar from '../../components/pipeline/PipelineSidebar'
import NodeDetailPanel from '../../components/pipeline/NodeDetailPanel'
import ChatPanel from '../../components/pipeline/ChatPanel'
import RecentActivity from '../../components/pipeline/RecentActivity'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

export default function PipelineSidePanels({
  chatOpen,
  graph,
  handleExperimentOverlay,
  handleHighlightNodes,
  handleNavigateToNode,
  handlePressureWave,
  recentActivityOpen,
  selectedNode,
  setChatOpen,
  setRecentActivityOpen,
  setSidebarOpen,
  sidebarOpen,
  topologyData,
}) {
  return (
    <>
      {PIPELINE_FEATURES.splitPanelSidebar && sidebarOpen && (
        <PipelineErrorBoundary name="PipelineSidebar">
          <PipelineSidebar
            topologyData={topologyData}
            graph={graph}
            onClose={() => setSidebarOpen(false)}
          />
        </PipelineErrorBoundary>
      )}

      {selectedNode && !chatOpen && (
        <PipelineErrorBoundary name="NodeDetailPanel" className="fixed right-0 top-0 z-50 h-full w-[400px] border-l border-terminal-600 bg-terminal-800">
          <NodeDetailPanel
            node={selectedNode}
            onClose={() => graph.clearSelection()}
            graph={graph}
          />
        </PipelineErrorBoundary>
      )}

      {chatOpen && (
        <PipelineErrorBoundary name="ChatPanel" className="fixed right-0 top-0 z-50 h-full w-[400px] border-l border-terminal-600 bg-terminal-800">
          <ChatPanel
            selectedNode={selectedNode}
            topologyData={topologyData}
            onClose={() => setChatOpen(false)}
            onNodeSelect={(nodeId) => graph.selectNode(nodeId)}
            onHighlightNodes={handleHighlightNodes}
            onPressureWave={handlePressureWave}
            onNavigateToNode={handleNavigateToNode}
            onExperimentOverlay={handleExperimentOverlay}
            graph={graph}
          />
        </PipelineErrorBoundary>
      )}

      {recentActivityOpen && !chatOpen && !selectedNode && (
        <PipelineErrorBoundary name="RecentActivity" className="fixed right-0 top-0 z-50 h-full w-[400px] border-l border-terminal-600 bg-terminal-800">
          <RecentActivity
            onNavigateToNode={(nodeId) => {
              setRecentActivityOpen(false)
              handleNavigateToNode(nodeId)
            }}
            onClose={() => setRecentActivityOpen(false)}
          />
        </PipelineErrorBoundary>
      )}
    </>
  )
}
