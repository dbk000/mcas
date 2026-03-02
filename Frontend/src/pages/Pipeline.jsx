import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  Panel,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import dagre from 'dagre'
import { useApi } from '../context/ApiContext'
import PipelineNode from '../components/pipeline/PipelineNode'
import NodeDetailPanel from '../components/pipeline/NodeDetailPanel'
import ChatPanel from '../components/pipeline/ChatPanel'
import PipelineToolbar from '../components/pipeline/PipelineToolbar'
import usePipelineGraph from '../hooks/usePipelineGraph'

// Subsystem colors
const SUBSYSTEM_COLORS = {
  ingestion: '#3b82f6',   // blue
  feature: '#8b5cf6',     // violet
  signal: '#f59e0b',      // amber
  filtering: '#ef4444',   // red
  strategy: '#10b981',    // emerald
  parameter: '#6366f1',   // indigo
  backtest: '#ec4899',    // pink
  metrics: '#14b8a6',     // teal
  risk: '#f97316',        // orange
  execution: '#22c55e',   // green
  meta: '#64748b',        // slate
}

const SUBSYSTEM_ORDER = [
  'ingestion', 'feature', 'signal', 'filtering', 'strategy',
  'parameter', 'backtest', 'metrics', 'risk', 'execution'
]

// Custom node types
const nodeTypes = { pipeline: PipelineNode }

// Dagre layout with subsystem column ranking
function getLayoutedElements(nodes, edges) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 25, ranksep: 40, marginx: 25, marginy: 25 })

  nodes.forEach((node) => {
    const subsystem = node.data?.subsystem || ''
    const rankIndex = SUBSYSTEM_ORDER.indexOf(subsystem)
    // Assign rank from subsystem order for strict column layout
    // Parameter nodes get same rank as strategy (cluster nearby)
    const rank = subsystem === 'parameter'
      ? SUBSYSTEM_ORDER.indexOf('strategy')
      : (rankIndex >= 0 ? rankIndex : SUBSYSTEM_ORDER.length)
    g.setNode(node.id, { width: 500, height: 320, rank })
  })

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target)
  })

  dagre.layout(g)

  const layoutedNodes = nodes.map((node) => {
    const nodeWithPosition = g.node(node.id)
    return {
      ...node,
      position: {
        x: nodeWithPosition.x - 250,
        y: nodeWithPosition.y - 160,
      },
    }
  })

  return { nodes: layoutedNodes, edges }
}

const PIPELINE_PASSWORD = 'pipeline_introspection'
const AUTH_KEY = 'pipeline_auth'

function PipelineGate() {
  const [authed, setAuthed] = useState(() => sessionStorage.getItem(AUTH_KEY) === 'true')
  const [input, setInput] = useState('')
  const [error, setError] = useState(false)

  if (authed) return <PipelineInner />

  const handleSubmit = (e) => {
    e.preventDefault()
    if (input === PIPELINE_PASSWORD) {
      sessionStorage.setItem(AUTH_KEY, 'true')
      setAuthed(true)
    } else {
      setError(true)
      setInput('')
    }
  }

  return (
    <div className="flex items-center justify-center h-[calc(100vh-8rem)]">
      <form onSubmit={handleSubmit} className="bg-terminal-800 border border-terminal-600 rounded-lg p-8 w-80">
        <h2 className="text-terminal-100 text-lg font-medium mb-1">Pipeline</h2>
        <p className="text-terminal-500 text-xs mb-6">This section is access-restricted.</p>
        <input
          type="password"
          value={input}
          onChange={(e) => { setInput(e.target.value); setError(false) }}
          placeholder="Enter password"
          autoFocus
          className="w-full bg-terminal-900 border border-terminal-600 rounded px-3 py-2 text-sm text-terminal-100 placeholder-terminal-600 focus:outline-none focus:border-blue-500 mb-3"
        />
        {error && <p className="text-red-400 text-xs mb-3">Incorrect password</p>}
        <button
          type="submit"
          className="w-full bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium rounded px-3 py-2 transition-colors"
        >
          Access
        </button>
      </form>
    </div>
  )
}

export default PipelineGate

function PipelineInner() {
  const { get } = useApi()
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [topologyData, setTopologyData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [chatOpen, setChatOpen] = useState(false)
  const reactFlowRef = useRef(null)

  // Pipeline graph state management
  const graph = usePipelineGraph(topologyData)

  // Derive selected node from graph state
  const selectedNode = useMemo(() => {
    if (!graph.selectedNodeId || !topologyData) return null
    return topologyData.nodes?.find(n => n.id === graph.selectedNodeId) || null
  }, [graph.selectedNodeId, topologyData])

  // Fetch topology
  useEffect(() => {
    let cancelled = false
    async function fetchTopology() {
      try {
        setLoading(true)
        const resp = await get('/v1/meta/topology')
        const data = resp?.nodes ? resp : resp?.data || resp
        if (!cancelled) {
          setTopologyData(data)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) setError(err.message)
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchTopology()
    return () => { cancelled = true }
  }, [get])

  // Store layout positions so visual updates don't re-run dagre
  const layoutRef = useRef({ nodes: [], edges: [] })

  // Layout effect: run dagre only when topology changes
  useEffect(() => {
    if (!topologyData) return

    const apiNodes = topologyData.nodes || []
    const apiEdges = topologyData.edges || []

    const flowNodes = apiNodes.map((n) => {
      const cv = n.current_version
      let statusColor = '#6b7280'
      if (cv?.status === 'deployed') statusColor = '#22c55e'
      else if (cv?.status === 'promising') statusColor = '#f59e0b'

      return {
        id: n.id,
        type: 'pipeline',
        data: {
          label: n.name,
          nodeType: n.type,
          subsystem: n.subsystem,
          subsystemColor: SUBSYSTEM_COLORS[n.subsystem] || '#6b7280',
          statusColor,
          versionCount: n.version_count,
          aggPf: cv?.agg_pf,
          currentVersion: cv,
          description: n.description,
          sourceFile: n.source_file,
          owner: n.owner,
          ports: n.ports,
          visualState: 'default',
          pressureWaveDelay: null,
        },
      }
    })

    const flowEdges = apiEdges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
      animated: true,
      style: { stroke: '#475569', strokeWidth: 3, opacity: 1 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#475569', width: 20, height: 20 },
    }))

    const { nodes: layouted, edges: layoutedEdges } = getLayoutedElements(flowNodes, flowEdges)
    layoutRef.current = { nodes: layouted, edges: layoutedEdges }
    setNodes(layouted)
    setEdges(layoutedEdges)

    setTimeout(() => {
      reactFlowRef.current?.fitView({ padding: 0.15, duration: 300 })
    }, 100)
  }, [topologyData, setNodes, setEdges])

  // Visual state effect: update node/edge styles without re-running dagre
  useEffect(() => {
    if (layoutRef.current.nodes.length === 0) return

    setNodes(prev => prev.map(n => {
      const vs = graph.visualState.get(n.id) || 'default'
      const pwDist = graph.pressureWaveDistances.get(n.id)
      const pressureWaveDelay = pwDist != null ? pwDist * 150 : null
      return {
        ...n,
        data: { ...n.data, visualState: vs, pressureWaveDelay },
      }
    }))

    setEdges(prev => {
      const hasSelection = graph.highlightedNodeIds.size > 0
      const baseEdges = prev.filter(e => !e.id.startsWith('causal-')).map(e => {
        const isHighlighted = graph.highlightedEdgeIds.has(e.id)
        return {
          ...e,
          animated: isHighlighted || !hasSelection,
          style: {
            stroke: isHighlighted ? '#94a3b8' : '#475569',
            strokeWidth: isHighlighted ? 4 : 3,
            opacity: hasSelection && !isHighlighted ? 0.15 : 1,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: isHighlighted ? '#94a3b8' : '#475569',
            width: 20,
            height: 20,
          },
        }
      })

      const causalEdges = graph.causalArrows.map((arrow, i) => ({
        id: `causal-${i}`,
        source: arrow.from,
        target: arrow.to,
        type: 'default',
        animated: false,
        style: { stroke: '#d97706', strokeWidth: 2, strokeDasharray: '8 4', opacity: 0.8 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#d97706', width: 16, height: 16 },
        label: arrow.label || '',
        labelStyle: { fill: '#d97706', fontSize: 10 },
        labelBgStyle: { fill: '#1e293b', fillOpacity: 0.8 },
      }))

      return [...baseEdges, ...causalEdges]
    })
  }, [graph.visualState, graph.highlightedEdgeIds, graph.highlightedNodeIds, graph.causalArrows, graph.pressureWaveDistances, setNodes, setEdges])

  const onNodeClick = useCallback((event, node) => {
    graph.selectNode(node.id)
  }, [graph])

  const onPaneClick = useCallback(() => {
    graph.clearSelection()
  }, [graph])

  // Minimap node color
  const minimapNodeColor = useCallback((node) => {
    return node.data?.subsystemColor || '#6b7280'
  }, [])

  // Chat/graph integration callbacks
  const handleHighlightNodes = useCallback((nodeIds) => {
    graph.highlightNodes(nodeIds)
  }, [graph])

  const handlePressureWave = useCallback((nodeId) => {
    graph.triggerPressureWave(nodeId)
  }, [graph])

  const handleNavigateToNode = useCallback((nodeId) => {
    graph.navigateToNode(nodeId)
    // Scroll to node in ReactFlow
    const node = nodes.find(n => n.id === nodeId)
    if (node && reactFlowRef.current) {
      reactFlowRef.current.setCenter(
        node.position.x + 250,
        node.position.y + 160,
        { zoom: 1, duration: 500 }
      )
    }
  }, [graph, nodes])

  const handleExperimentOverlay = useCallback((experimentId, touchedNodeIds) => {
    graph.setExperimentOverlay(experimentId, touchedNodeIds)
  }, [graph])

  const handleCompositionOverlay = useCallback((compositionId, nodeVersionMap) => {
    graph.setCompositionOverlay(compositionId, nodeVersionMap)
  }, [graph])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-terminal-400 text-sm">Loading pipeline topology...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-400 text-sm mb-2">Failed to load pipeline topology</p>
          <p className="text-terminal-500 text-xs">{error}</p>
        </div>
      </div>
    )
  }

  const panelOpen = selectedNode || chatOpen

  return (
    <div className="h-[calc(100vh-8rem)] flex">
      {/* React Flow Canvas */}
      <div className={`flex-1 relative transition-all duration-300 ${panelOpen ? 'mr-[400px]' : ''}`}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          onInit={(instance) => { reactFlowRef.current = instance }}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.3}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
          className="bg-terminal-900"
        >
          <Background color="#1e293b" gap={20} size={1} />
          <Controls
            position="bottom-left"
            className="!bg-terminal-800 !border-terminal-600 !shadow-lg [&>button]:!bg-terminal-700 [&>button]:!border-terminal-600 [&>button]:!text-terminal-300 [&>button:hover]:!bg-terminal-600"
          />
          <MiniMap
            nodeColor={minimapNodeColor}
            maskColor="rgba(13, 17, 23, 0.8)"
            className="!bg-terminal-800 !border-terminal-600"
            position="bottom-right"
          />

          {/* Legend */}
          <Panel position="top-left">
            <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg p-4 text-sm">
              <div className="text-terminal-300 font-medium mb-2.5">Pipeline Subsystems</div>
              <div className="grid grid-cols-2 gap-x-5 gap-y-1.5">
                {SUBSYSTEM_ORDER.map(s => (
                  <div key={s} className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-sm" style={{ backgroundColor: SUBSYSTEM_COLORS[s] }} />
                    <span className="text-terminal-400 capitalize">{s}</span>
                  </div>
                ))}
              </div>
            </div>
          </Panel>

          {/* Toolbar */}
          <PipelineToolbar
            topologyData={topologyData}
            graph={graph}
            onExperimentOverlay={handleExperimentOverlay}
            onCompositionOverlay={handleCompositionOverlay}
          />

          {/* Chat toggle */}
          <Panel position="top-right">
            <button
              onClick={() => setChatOpen(!chatOpen)}
              className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg px-3 py-2 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 transition-colors"
            >
              {chatOpen ? 'Close Chat' : 'AI Chat'}
            </button>
          </Panel>
        </ReactFlow>
      </div>

      {/* Node Detail Panel */}
      {selectedNode && !chatOpen && (
        <NodeDetailPanel
          node={selectedNode}
          onClose={() => graph.clearSelection()}
          graph={graph}
        />
      )}

      {/* AI Chat Panel */}
      {chatOpen && (
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
      )}
    </div>
  )
}
