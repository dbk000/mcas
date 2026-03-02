import { useState, useEffect, useCallback, useMemo } from 'react'
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

// Dagre layout
function getLayoutedElements(nodes, edges) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', nodesep: 90, ranksep: 180, marginx: 60, marginy: 60 })

  nodes.forEach((node) => {
    g.setNode(node.id, { width: 360, height: 160 })
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
        x: nodeWithPosition.x - 180,
        y: nodeWithPosition.y - 80,
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
  const [selectedNode, setSelectedNode] = useState(null)
  const [topologyData, setTopologyData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [chatOpen, setChatOpen] = useState(false)

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

  // Convert API data to React Flow nodes/edges
  useEffect(() => {
    if (!topologyData) return

    const apiNodes = topologyData.nodes || []
    const apiEdges = topologyData.edges || []

    const flowNodes = apiNodes.map((n) => {
      const cv = n.current_version
      let statusColor = '#6b7280' // grey: tested only
      if (cv?.status === 'deployed') statusColor = '#22c55e' // green
      else if (cv?.status === 'promising') statusColor = '#f59e0b' // amber

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
      style: { stroke: '#475569', strokeWidth: 3 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#475569', width: 20, height: 20 },
    }))

    const { nodes: layouted, edges: layoutedEdges } = getLayoutedElements(flowNodes, flowEdges)
    setNodes(layouted)
    setEdges(layoutedEdges)
  }, [topologyData, setNodes, setEdges])

  const onNodeClick = useCallback((event, node) => {
    const apiNode = topologyData?.nodes?.find(n => n.id === node.id)
    setSelectedNode(apiNode || node.data)
  }, [topologyData])

  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  // Minimap node color
  const minimapNodeColor = useCallback((node) => {
    return node.data?.subsystemColor || '#6b7280'
  }, [])

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

  return (
    <div className="h-[calc(100vh-8rem)] flex">
      {/* React Flow Canvas */}
      <div className={`flex-1 relative transition-all duration-300 ${selectedNode ? 'mr-[400px]' : ''} ${chatOpen ? 'mr-[400px]' : ''}`}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onPaneClick={onPaneClick}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
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
          onClose={() => setSelectedNode(null)}
        />
      )}

      {/* AI Chat Panel */}
      {chatOpen && (
        <ChatPanel
          selectedNode={selectedNode}
          topologyData={topologyData}
          onClose={() => setChatOpen(false)}
          onNodeSelect={(nodeId) => {
            const apiNode = topologyData?.nodes?.find(n => n.id === nodeId)
            if (apiNode) setSelectedNode(apiNode)
          }}
        />
      )}
    </div>
  )
}
