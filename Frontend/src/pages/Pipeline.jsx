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
import { useApi } from '../context/ApiContext'
import PipelineNode from '../components/pipeline/PipelineNode'
import NodeDetailPanel from '../components/pipeline/NodeDetailPanel'
import ChatPanel from '../components/pipeline/ChatPanel'
import PipelineToolbar from '../components/pipeline/PipelineToolbar'
import PipelineErrorBoundary from '../components/pipeline/PipelineErrorBoundary'
import RecentActivity from '../components/pipeline/RecentActivity'
import PipelineSidebar from '../components/pipeline/PipelineSidebar'
import usePipelineGraph from '../hooks/usePipelineGraph'
import usePipelineUrlState from '../hooks/usePipelineUrlState'
import { PIPELINE_FEATURES } from '../config/pipelineFeatures'
import { useToast } from '../context/ToastContext'

const LAYOUT_STORAGE_KEY = 'pipeline-layout-positions'

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

// Default grid layout — pixel-perfect alignment by row
// Horizontal spacing: 210px between node left edges, centered on widest row (9 nodes = 1680px)
// Vertical spacing: 190px between row tops
const DEFAULT_GRID_POSITIONS = {
  // Row 0 (y=0): Data Ingestion — 3 nodes, start_x=630
  'dbb7452f-ab52-44e6-bb30-d29cca9e9005': { x: 630, y: 0 },   // Derivatives Data Feed
  '0e70a279-0a31-40c8-87d2-ce985b2ab8a0': { x: 840, y: 0 },   // OHLCV Data Feed
  '0124ed33-738a-4f57-940b-9775354c2290': { x: 1050, y: 0 },  // Social Sentiment Feed

  // Row 1 (y=190): Feature Engineering — 4 nodes, start_x=525
  'fbc404e0-9684-4f53-9a25-2e2a03b60196': { x: 525, y: 190 },  // Indicator Engine
  '34f65bb1-20b0-4c61-bd93-5ba8dab536be': { x: 735, y: 190 },  // Rolling Statistics
  '678c8948-31bf-48ca-a727-2359e98ae9b2': { x: 945, y: 190 },  // Dormancy Score
  'a9d78bc2-6d69-4d10-b426-37c903a36333': { x: 1155, y: 190 }, // Accumulation Divergence

  // Row 2 (y=380): Filtering — 2 nodes, start_x=735
  'cb6f034c-c741-4bb4-ac44-f5c616a30750': { x: 735, y: 380 },  // Structural Filters
  '9fb9b539-0f4d-4c2a-ae3e-44c98c838159': { x: 945, y: 380 },  // Universe Filter

  // Row 3 (y=570): Parameters/Config — 5 nodes, start_x=420
  'a76af2f3-a171-44eb-8d84-b4d8b996e741': { x: 420, y: 570 },  // TP1 Take-Profit
  '316c09aa-f812-49ff-a105-8003b43c70f5': { x: 630, y: 570 },  // Stop-Loss Config
  '97c34b89-180d-4bde-893b-5a917add5167': { x: 840, y: 570 },  // Volume Z Threshold
  '13a0fc5e-18cf-49d7-bfa5-ff713799b326': { x: 1050, y: 570 }, // Trailing Stop Config
  'ffda1c91-508e-40a9-9560-991699a786d5': { x: 1260, y: 570 }, // Time Stop Config

  // Row 4 (y=760): Strategy/Backtest/Execution — 6 nodes, start_x=315
  '305ff981-8265-4d56-b6bc-30af2a39c74e': { x: 315, y: 760 },  // Slippage Model
  '3c321c3a-52ff-4fd8-8495-f2f397c46438': { x: 525, y: 760 },  // Walk-Forward Validator
  '0ef932ab-433d-4fb7-9c69-8a4703cf7316': { x: 735, y: 760 },  // Metric Aggregator
  'bbbd1c6e-bd64-4260-a490-cbe09887fa1d': { x: 945, y: 760 },  // U1 Strategy Assembly
  '07217426-6b8b-4835-9075-db46e6e2e271': { x: 1155, y: 760 }, // Execution Engine
  '5879264b-b331-4802-bafa-8a792ad0971a': { x: 1365, y: 760 }, // Risk Manager

  // Row 5 (y=950): Signal Generators — 6 nodes, start_x=315
  '5b61711c-5282-4a08-ac2a-4c2a396de9cf': { x: 315, y: 950 },  // S9 Short Reversion
  '396037d1-13c8-4f17-8bf9-e9f95546f419': { x: 525, y: 950 },  // S10 MACD Scalp
  'db16d8c1-259c-4ad1-9197-a39edf32c9b0': { x: 735, y: 950 },  // U1 Unicorn Ignition
  '8a3e4d49-b88e-465c-80a1-37a1af845b1e': { x: 945, y: 950 },  // S1 BB Squeeze (Legacy)
  '43e39128-8327-43c7-b3c1-8362762b6d56': { x: 1155, y: 950 }, // S2 Golden Cross (Legacy)
  'd83ee551-d609-4aa2-8c1d-096cab0c736a': { x: 1365, y: 950 }, // D1 Dormancy-to-Ignition

  // Row 6 (y=1140): Meta/Workflow — 9 nodes, start_x=0
  '014f6fc8-a3ab-4ed9-9260-5d96ca134392': { x: 0, y: 1140 },    // Requirement Capture
  'a7067064-31b8-4630-a48b-26b593ced2aa': { x: 210, y: 1140 },  // Exploration
  '31bc7c10-3532-4689-9709-f98d6859e7a4': { x: 420, y: 1140 },  // Plan Design
  '964e8b23-15b2-4f3a-b4ad-31be53e0f2a1': { x: 630, y: 1140 },  // Implementation
  '1cf501e2-cdcd-4d23-b825-821debb52c77': { x: 840, y: 1140 },  // Layout Tuning
  'b7e630b4-644b-4ec5-b3c1-8522495649ca': { x: 1050, y: 1140 }, // Deploy (CF Pages)
  '2657bdf4-ec03-4a32-9dc2-62ce718267f4': { x: 1260, y: 1140 }, // Production Verify
  '5f0ca30f-6c1f-4a4c-bdd4-a8a68f559ce0': { x: 1470, y: 1140 }, // Lessons Learned
  '9c78d2f9-45e8-4e94-b308-2d548cfb0ae4': { x: 1680, y: 1140 }, // S9 Causality Audit
}

// Custom node types
const nodeTypes = { pipeline: PipelineNode }

// Layout Web Worker (Wave 1.5) — init on module load
let layoutWorker = null
try {
  layoutWorker = new Worker(
    new URL('../workers/layoutWorker.js', import.meta.url),
    { type: 'module' }
  )
} catch {
  console.warn('Layout Web Worker failed to init, will use main-thread fallback')
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
    <div className="flex items-center justify-center h-[calc(100vh-3.5rem)]">
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
  const { addToast } = useToast()
  const [nodes, setNodes, onNodesChange] = useNodesState([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])
  const [usingSavedLayout, setUsingSavedLayout] = useState(false)
  const [layoutVersion, setLayoutVersion] = useState(0)
  const [topologyData, setTopologyData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [chatOpen, setChatOpen] = useState(false)
  const [tvMode, setTvMode] = useState(false)
  const [recentActivityOpen, setRecentActivityOpen] = useState(false)
  // Wave 4.4: sidebar toggle
  const [sidebarOpen, setSidebarOpen] = useState(false)
  // Wave 4.2: search/filter
  const [searchQuery, setSearchQuery] = useState('')
  const searchDebounceRef = useRef(null)
  // Wave 2.12: configurable facets — which metrics show on nodes
  const [facets, setFacets] = useState({
    pf: true, winRate: true, trades: true, status: true,
    owner: true, description: true, connections: true,
  })
  const reactFlowRef = useRef(null)

  // Pipeline graph state management
  const graph = usePipelineGraph(topologyData)

  // URL state sync (deep linking + browser back/forward)
  usePipelineUrlState(graph, topologyData, chatOpen, setChatOpen)

  // Wave 4.2: Search/filter — debounced 250ms, highlights matching nodes
  const handleSearchChange = useCallback((query) => {
    setSearchQuery(query)
    if (searchDebounceRef.current) clearTimeout(searchDebounceRef.current)
    if (!query.trim()) {
      graph.clearSelection()
      return
    }
    searchDebounceRef.current = setTimeout(() => {
      if (!topologyData?.nodes) return
      const q = query.toLowerCase()
      const matchIds = topologyData.nodes
        .filter(n =>
          (n.name || '').toLowerCase().includes(q) ||
          (n.type || '').toLowerCase().includes(q) ||
          (n.subsystem || '').toLowerCase().includes(q) ||
          (n.description || '').toLowerCase().includes(q)
        )
        .map(n => n.id)
      if (matchIds.length > 0) {
        graph.highlightNodes(matchIds)
      }
    }, 250)
  }, [topologyData, graph])

  // Derive selected node from graph state
  const selectedNode = useMemo(() => {
    if (!graph.selectedNodeId || !topologyData) return null
    return topologyData.nodes?.find(n => n.id === graph.selectedNodeId) || null
  }, [graph.selectedNodeId, topologyData])

  // Graph preflight complexity check (Wave 1.8)
  // Returns tier as data — renderer decides what to do with it
  const complexityTier = useMemo(() => {
    const nodeCount = topologyData?.nodes?.length || 0
    if (nodeCount > 120) return 3
    if (nodeCount > 60) return 2
    return 1
  }, [topologyData])

  // LOD tier from viewport density (Wave 1.2)
  // Computed from f(nodesVisible, viewportArea), NOT zoom level
  const [lodTier, setLodTier] = useState(1)
  const computeLodTier = useCallback((viewport) => {
    if (!reactFlowRef.current || !topologyData?.nodes?.length) return
    const nodeCount = topologyData.nodes.length
    // Viewport area in screen pixels
    const container = reactFlowRef.current.getViewport()
    const zoom = container?.zoom || viewport?.zoom || 1
    // Approximate visible area: nodes spread across canvas, scaled by zoom
    // Density = nodes per unit visible area — higher density = less detail
    // At zoom 1, each node is ~460x300px. We compute how many could fit on screen.
    const viewportW = window.innerWidth
    const viewportH = window.innerHeight
    const viewportArea = viewportW * viewportH
    const nodeArea = 460 * 300 * zoom * zoom
    const nodeCapacity = viewportArea / nodeArea
    const density = nodeCount / Math.max(nodeCapacity, 1)
    // density < 1 means nodes fit comfortably → tier 1
    // density 1-3 means crowded → tier 2
    // density > 3 means very dense → tier 3
    // complexityTier acts as a floor (large graphs never go to tier 1)
    let tier = 1
    if (density > 3) tier = 3
    else if (density > 1) tier = 2
    return Math.max(tier, complexityTier === 3 ? 2 : 1)
  }, [topologyData, complexityTier])

  // Debounced viewport tick for edge culling (Wave 1.9)
  const [viewportTick, setViewportTick] = useState(0)
  const viewportDebounceRef = useRef(null)

  const onViewportChange = useCallback((viewport) => {
    const tier = computeLodTier(viewport)
    if (tier != null && tier !== lodTier) setLodTier(tier)
    // Debounce viewport tick for edge culling (100ms)
    if (viewportDebounceRef.current) clearTimeout(viewportDebounceRef.current)
    viewportDebounceRef.current = setTimeout(() => setViewportTick(t => t + 1), 100)
  }, [computeLodTier, lodTier])

  // Keyboard shortcuts (Wave 1.3)
  // Disabled when text input focused. All input detection in renderer, not hook.
  useEffect(() => {
    function isTextInputFocused() {
      const el = document.activeElement
      if (!el) return false
      const tag = el.tagName.toLowerCase()
      if (tag === 'input' || tag === 'textarea') return true
      if (el.contentEditable === 'true') return true
      return false
    }

    function handleKeyDown(e) {
      if (isTextInputFocused()) return

      switch (e.key) {
        case 'Escape':
          // Universal dismiss: search → slice → focus → overlay → sidebar → recent → chat → selection
          if (searchQuery) {
            setSearchQuery('')
            graph.clearSelection()
          } else if (graph.sliceFrom) {
            graph.clearSlice()
          } else if (graph.focusMode !== 'off') {
            graph.clearFocusMode()
          } else if (graph.activeOverlay) {
            graph.clearOverlay()
          } else if (sidebarOpen) {
            setSidebarOpen(false)
          } else if (recentActivityOpen) {
            setRecentActivityOpen(false)
          } else if (chatOpen) {
            setChatOpen(false)
          } else if (graph.selectedNodeId) {
            graph.clearSelection()
          }
          e.preventDefault()
          break

        case 'ArrowRight':
        case 'ArrowDown': {
          // Navigate downstream in DAG
          if (!graph.selectedNodeId) break
          const downstream = graph.downstreamMap?.[graph.selectedNodeId]
          if (downstream?.size > 0) {
            const nextId = downstream.values().next().value
            graph.selectNode(nextId)
            // Pan to node
            const node = nodes.find(n => n.id === nextId)
            if (node && reactFlowRef.current) {
              reactFlowRef.current.setCenter(node.position.x + 250, node.position.y + 160, { zoom: undefined, duration: 300 })
            }
            e.preventDefault()
          }
          break
        }

        case 'ArrowLeft':
        case 'ArrowUp': {
          // Navigate upstream in DAG
          if (!graph.selectedNodeId) break
          const upstream = graph.upstreamMap?.[graph.selectedNodeId]
          if (upstream?.size > 0) {
            const prevId = upstream.values().next().value
            graph.selectNode(prevId)
            const node = nodes.find(n => n.id === prevId)
            if (node && reactFlowRef.current) {
              reactFlowRef.current.setCenter(node.position.x + 250, node.position.y + 160, { zoom: undefined, duration: 300 })
            }
            e.preventDefault()
          }
          break
        }

        // Wave 4.2: '/' to focus search
        case '/':
          if (PIPELINE_FEATURES.searchFilter) {
            e.preventDefault()
            // Focus the search input if it exists
            const searchInput = document.querySelector('[data-pipeline-search]')
            if (searchInput) searchInput.focus()
          }
          break

        // Wave 4.3: 'f' to toggle focus on selected node
        case 'f':
          if (PIPELINE_FEATURES.focusMode && graph.selectedNodeId) {
            if (graph.focusMode !== 'off') {
              graph.clearFocusMode()
            } else {
              graph.setFocusMode('node', graph.selectedNodeId)
            }
          }
          break

        default:
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [graph, chatOpen, setChatOpen, recentActivityOpen, nodes, searchQuery, sidebarOpen])

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
  const layoutRequestIdRef = useRef(0)

  // Collapsible subsystem groups (Wave 1.6)
  const [collapsedGroups, setCollapsedGroups] = useState(new Set())

  const toggleGroupCollapse = useCallback((subsystem) => {
    setCollapsedGroups(prev => {
      const next = new Set(prev)
      if (next.has(subsystem)) next.delete(subsystem)
      else next.add(subsystem)
      return next
    })
  }, [])

  // Build flow nodes/edges, applying group collapse
  const { flowNodes, flowEdges } = useMemo(() => {
    if (!topologyData) return { flowNodes: [], flowEdges: [] }

    const apiNodes = topologyData.nodes || []
    const apiEdges = topologyData.edges || []

    // If no groups collapsed, render all nodes normally
    if (collapsedGroups.size === 0) {
      const nodes = apiNodes.map((n) => {
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
            facets,
          },
        }
      })
      const edges = apiEdges.map((e) => ({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: e.sourceHandle,
        targetHandle: e.targetHandle,
        animated: true,
        style: { stroke: '#475569', strokeWidth: 3, opacity: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#475569', width: 20, height: 20 },
      }))
      return { flowNodes: nodes, flowEdges: edges }
    }

    // Collapsed: replace nodes in collapsed subsystems with a single group node
    const collapsedNodeIds = new Set()
    const groupNodes = new Map() // subsystem -> group node id

    for (const n of apiNodes) {
      if (collapsedGroups.has(n.subsystem)) {
        collapsedNodeIds.add(n.id)
        if (!groupNodes.has(n.subsystem)) {
          groupNodes.set(n.subsystem, `group-${n.subsystem}`)
        }
      }
    }

    // Count nodes per collapsed group and find worst status
    const groupInfo = {}
    for (const n of apiNodes) {
      if (!collapsedNodeIds.has(n.id)) continue
      const sub = n.subsystem
      if (!groupInfo[sub]) groupInfo[sub] = { count: 0, statuses: [] }
      groupInfo[sub].count++
      if (n.current_version?.status) groupInfo[sub].statuses.push(n.current_version.status)
    }

    // Create visible nodes: non-collapsed + group summary nodes
    const nodes = []
    for (const n of apiNodes) {
      if (collapsedNodeIds.has(n.id)) continue
      const cv = n.current_version
      let statusColor = '#6b7280'
      if (cv?.status === 'deployed') statusColor = '#22c55e'
      else if (cv?.status === 'promising') statusColor = '#f59e0b'
      nodes.push({
        id: n.id,
        type: 'pipeline',
        data: {
          label: n.name, nodeType: n.type, subsystem: n.subsystem,
          subsystemColor: SUBSYSTEM_COLORS[n.subsystem] || '#6b7280', statusColor,
          versionCount: n.version_count, aggPf: cv?.agg_pf, currentVersion: cv,
          description: n.description, sourceFile: n.source_file, owner: n.owner,
          ports: n.ports, visualState: 'default', pressureWaveDelay: null,
        },
      })
    }

    for (const [sub, groupId] of groupNodes) {
      const info = groupInfo[sub] || { count: 0, statuses: [] }
      const worstStatus = info.statuses.includes('promising') ? 'promising'
        : info.statuses.includes('tested') ? 'tested' : 'deployed'
      nodes.push({
        id: groupId,
        type: 'pipeline',
        data: {
          label: `${sub} (${info.count})`,
          nodeType: 'composition',
          subsystem: sub,
          subsystemColor: SUBSYSTEM_COLORS[sub] || '#6b7280',
          statusColor: worstStatus === 'deployed' ? '#22c55e' : worstStatus === 'promising' ? '#f59e0b' : '#6b7280',
          versionCount: 0, aggPf: null, currentVersion: { status: worstStatus },
          description: `${info.count} collapsed ${sub} nodes`,
          sourceFile: null, owner: null, ports: { inputs: [], outputs: [] },
          visualState: 'default', pressureWaveDelay: null, isGroupNode: true,
        },
      })
    }

    // Remap edges: replace collapsed node refs with group node refs, dedup
    const edgeSet = new Set()
    const edges = []
    for (const e of apiEdges) {
      let src = e.source
      let tgt = e.target
      // Remap collapsed nodes to their group
      for (const [sub, groupId] of groupNodes) {
        if (collapsedNodeIds.has(src)) {
          const srcNode = apiNodes.find(n => n.id === src)
          if (srcNode && collapsedGroups.has(srcNode.subsystem)) src = groupNodes.get(srcNode.subsystem)
        }
        if (collapsedNodeIds.has(tgt)) {
          const tgtNode = apiNodes.find(n => n.id === tgt)
          if (tgtNode && collapsedGroups.has(tgtNode.subsystem)) tgt = groupNodes.get(tgtNode.subsystem)
        }
      }
      if (src === tgt) continue // skip self-loops from collapsed groups
      const key = `${src}->${tgt}`
      if (edgeSet.has(key)) continue
      edgeSet.add(key)
      edges.push({
        id: `e-${src}-${tgt}`,
        source: src, target: tgt,
        animated: true,
        style: { stroke: '#475569', strokeWidth: 3, opacity: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, color: '#475569', width: 20, height: 20 },
      })
    }

    return { flowNodes: nodes, flowEdges: edges }
  }, [topologyData, collapsedGroups, facets])

  // Save current node positions as default layout
  const saveLayoutPositions = useCallback(() => {
    const positions = {}
    for (const n of nodes) {
      if (n.position) positions[n.id] = { x: n.position.x, y: n.position.y }
    }
    try {
      localStorage.setItem(LAYOUT_STORAGE_KEY, JSON.stringify(positions))
      setUsingSavedLayout(true)
      addToast('Layout saved as default', 'success')
    } catch (e) { /* localStorage full or unavailable */ }
  }, [nodes, addToast])

  // Clear saved layout and revert to default grid
  const clearLayoutPositions = useCallback(() => {
    localStorage.removeItem(LAYOUT_STORAGE_KEY)
    setUsingSavedLayout(false)
    setLayoutVersion(v => v + 1) // trigger layout effect re-run
    addToast('Layout reset to default grid', 'success')
  }, [addToast])

  // Layout effect: saved positions > default grid > dagre (for unknown nodes only)
  useEffect(() => {
    if (flowNodes.length === 0) return

    const requestId = ++layoutRequestIdRef.current
    const workerNodes = flowNodes.map(n => ({ id: n.id, subsystem: n.data.subsystem }))
    const workerEdges = flowEdges.map(e => ({ source: e.source, target: e.target }))
    const nodeIds = new Set(flowNodes.map(n => n.id))

    function applyPositions(positions, isSaved = false) {
      // Check staleness
      if (requestId !== layoutRequestIdRef.current) return

      const layouted = flowNodes.map(n => ({
        ...n,
        position: positions[n.id] || { x: 0, y: 0 },
      }))
      layoutRef.current = { nodes: layouted, edges: flowEdges }
      setNodes(layouted)
      setEdges(flowEdges)
      setUsingSavedLayout(isSaved)

      setTimeout(() => {
        reactFlowRef.current?.fitView({ padding: 0.15, duration: 300 })
      }, 100)
    }

    // Try saved layout first
    try {
      const raw = localStorage.getItem(LAYOUT_STORAGE_KEY)
      if (raw) {
        const saved = JSON.parse(raw)
        // Use saved layout if it covers all current nodes
        const allCovered = [...nodeIds].every(id => saved[id])
        if (allCovered) {
          applyPositions(saved, true)
          return
        }
      }
    } catch (e) { /* corrupted localStorage, fall through to default grid */ }

    // Use hardcoded default grid layout — pixel-perfect aligned rows
    // For any nodes not in the grid (e.g. newly added), fall back to dagre for those
    const allInGrid = [...nodeIds].every(id => DEFAULT_GRID_POSITIONS[id])
    if (allInGrid) {
      applyPositions(DEFAULT_GRID_POSITIONS, false)
    } else {
      // Hybrid: use grid positions for known nodes, dagre for unknown ones
      const unknownNodes = workerNodes.filter(n => !DEFAULT_GRID_POSITIONS[n.id])
      const unknownEdges = workerEdges.filter(e => {
        const srcUnknown = !DEFAULT_GRID_POSITIONS[e.source]
        const tgtUnknown = !DEFAULT_GRID_POSITIONS[e.target]
        return srcUnknown || tgtUnknown
      })
      if (unknownNodes.length > 0 && layoutWorker) {
        const handler = (e) => {
          if (e.data.requestId !== requestId) return
          layoutWorker.removeEventListener('message', handler)
          const merged = { ...DEFAULT_GRID_POSITIONS, ...e.data.positions }
          applyPositions(merged, false)
        }
        layoutWorker.addEventListener('message', handler)
        layoutWorker.postMessage({ requestId, nodes: unknownNodes, edges: unknownEdges })
      } else {
        // Main-thread fallback for unknown nodes
        const merged = { ...DEFAULT_GRID_POSITIONS }
        // Place unknown nodes in a row below the last grid row
        let offsetX = 0
        for (const n of unknownNodes) {
          merged[n.id] = { x: offsetX, y: 1330 }
          offsetX += 210
        }
        applyPositions(merged, false)
      }
    }
  }, [flowNodes, flowEdges, setNodes, setEdges, layoutVersion])

  // Panel-aware fitView (Wave 1.7)
  // Subtracts panel widths so fitView doesn't center behind open panels
  const panelAwareFitView = useCallback(() => {
    if (!reactFlowRef.current) return
    const panelWidth = (selectedNode || chatOpen) ? 400 : 0
    // React Flow fitView doesn't natively exclude panel area,
    // so we fit and then pan left by half the panel width to compensate
    reactFlowRef.current.fitView({ padding: 0.15, duration: 300 })
    if (panelWidth > 0) {
      setTimeout(() => {
        const viewport = reactFlowRef.current?.getViewport()
        if (viewport) {
          reactFlowRef.current.setViewport(
            { x: viewport.x - (panelWidth / 2) * viewport.zoom, y: viewport.y, zoom: viewport.zoom },
            { duration: 200 }
          )
        }
      }, 350)
    }
  }, [selectedNode, chatOpen])

  // Visual state effect: update node/edge styles without re-running dagre
  // Reference equality: only create new node objects when visual state actually changed
  // Also applies LOD tier per-node (selected/search-match nodes force tier 1)
  useEffect(() => {
    if (layoutRef.current.nodes.length === 0) return

    setNodes(prev => {
      let changed = false
      const next = prev.map(n => {
        const vs = graph.visualState.get(n.id) || 'default'
        const pwDist = graph.pressureWaveDistances.get(n.id)
        const pressureWaveDelay = pwDist != null ? pwDist * 150 : null
        // Per-node LOD override: selected/highlighted/alert nodes force tier 1
        const nodeLod = (vs === 'selected' || vs === 'highlighted' || vs === 'alert' || vs === 'searchMatch')
          ? 1
          : lodTier
        // Skip creating new object if nothing changed
        if (n.data.visualState === vs && n.data.pressureWaveDelay === pressureWaveDelay && n.data.lodTier === nodeLod) {
          return n
        }
        changed = true
        return { ...n, data: { ...n.data, visualState: vs, pressureWaveDelay, lodTier: nodeLod } }
      })
      return changed ? next : prev
    })

    setEdges(prev => {
      const hasSelection = graph.highlightedNodeIds.size > 0
      const prevBase = prev.filter(e => !e.id.startsWith('causal-'))

      // Edge viewport culling (Wave 1.9): hide edges where both endpoints
      // are off-screen. Highlighted edges + causal arrows are exempt.
      // Only active when edge count > 50 to avoid overhead on small graphs.
      const MAX_VISIBLE_EDGES = 50
      let culledEdgeIds = null
      if (prevBase.length > MAX_VISIBLE_EDGES && reactFlowRef.current) {
        try {
          const vp = reactFlowRef.current.getViewport()
          const w = window.innerWidth / vp.zoom
          const h = window.innerHeight / vp.zoom
          const vpLeft = -vp.x / vp.zoom
          const vpTop = -vp.y / vp.zoom
          const vpRight = vpLeft + w
          const vpBottom = vpTop + h
          // Margin: include nodes slightly off-screen (1 node width)
          const margin = 500
          const nodePositions = {}
          for (const n of (layoutRef.current.nodes || [])) {
            nodePositions[n.id] = n.position
          }
          culledEdgeIds = new Set()
          for (const e of prevBase) {
            if (graph.highlightedEdgeIds.has(e.id)) continue // never cull highlighted
            const sp = nodePositions[e.source]
            const tp = nodePositions[e.target]
            if (!sp || !tp) continue
            const srcVisible = sp.x + 460 >= vpLeft - margin && sp.x <= vpRight + margin &&
                               sp.y + 300 >= vpTop - margin && sp.y <= vpBottom + margin
            const tgtVisible = tp.x + 460 >= vpLeft - margin && tp.x <= vpRight + margin &&
                               tp.y + 300 >= vpTop - margin && tp.y <= vpBottom + margin
            if (!srcVisible && !tgtVisible) {
              culledEdgeIds.add(e.id)
            }
          }
        } catch { /* viewport not ready */ }
      }

      let edgesChanged = false
      const baseEdges = prevBase.map(e => {
        const isHighlighted = graph.highlightedEdgeIds.has(e.id)
        const newStroke = isHighlighted ? '#94a3b8' : '#475569'
        const newWidth = isHighlighted ? 4 : 3
        const newOpacity = hasSelection && !isHighlighted ? 0.15 : 1
        const newAnimated = isHighlighted || !hasSelection
        const newHidden = culledEdgeIds?.has(e.id) || false
        // Skip if unchanged
        if (
          e.style?.stroke === newStroke &&
          e.style?.strokeWidth === newWidth &&
          e.style?.opacity === newOpacity &&
          e.animated === newAnimated &&
          (e.hidden || false) === newHidden
        ) {
          return e
        }
        edgesChanged = true
        return {
          ...e,
          animated: newAnimated,
          hidden: newHidden,
          style: { stroke: newStroke, strokeWidth: newWidth, opacity: newOpacity },
          markerEnd: { type: MarkerType.ArrowClosed, color: newStroke, width: 20, height: 20 },
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

      // Check if causal edges changed
      const prevCausal = prev.filter(e => e.id.startsWith('causal-'))
      const causalChanged = causalEdges.length !== prevCausal.length ||
        causalEdges.some((ce, i) => !prevCausal[i] || ce.source !== prevCausal[i].source || ce.target !== prevCausal[i].target)

      if (!edgesChanged && !causalChanged) return prev
      return [...baseEdges, ...causalEdges]
    })
  }, [graph.visualState, graph.highlightedEdgeIds, graph.highlightedNodeIds, graph.causalArrows, graph.pressureWaveDistances, lodTier, viewportTick, setNodes, setEdges])

  const onNodeClick = useCallback((event, node) => {
    // Wave 2.13: Shift+click for pipeline slicing
    if (event.shiftKey) {
      if (!graph.sliceFrom) {
        graph.startSlice(node.id)
      } else {
        graph.completeSlice(node.id)
      }
      return
    }
    // Clear search on node click
    if (searchQuery) setSearchQuery('')
    graph.selectNode(node.id)
  }, [graph, searchQuery])

  // Wave 4.3: Double-click for focus mode
  const onNodeDoubleClick = useCallback((event, node) => {
    if (PIPELINE_FEATURES.focusMode) {
      graph.setFocusMode('node', node.id)
    }
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

  const panelOpen = selectedNode || chatOpen || recentActivityOpen

  return (
    <div className={`h-[calc(100vh-3.5rem)] flex ${tvMode ? 'pipeline-tv-mode' : ''} relative`} data-complexity-tier={complexityTier}>
      {/* Wave 4.4: Sidebar */}
      {PIPELINE_FEATURES.splitPanelSidebar && sidebarOpen && (
        <PipelineErrorBoundary name="PipelineSidebar">
          <PipelineSidebar
            topologyData={topologyData}
            graph={graph}
            onClose={() => setSidebarOpen(false)}
          />
        </PipelineErrorBoundary>
      )}

      {/* React Flow Canvas */}
      <div className={`flex-1 relative transition-all duration-300 ${panelOpen ? 'mr-[400px]' : ''} ${sidebarOpen ? 'ml-[260px]' : ''}`}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
          onNodeDoubleClick={onNodeDoubleClick}
          onPaneClick={onPaneClick}
          onViewportChange={onViewportChange}
          nodeTypes={nodeTypes}
          onInit={(instance) => { reactFlowRef.current = instance }}
          fitView
          fitViewOptions={{ padding: 0.15 }}
          minZoom={0.1}
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

          {/* Wave 4.2: Search bar (top-left floating) */}
          {PIPELINE_FEATURES.searchFilter && (
            <Panel position="top-left">
              <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg flex items-center gap-1.5 px-2 py-1">
                <span className="text-terminal-500 text-xs">🔍</span>
                <input
                  data-pipeline-search
                  type="text"
                  value={searchQuery}
                  onChange={e => handleSearchChange(e.target.value)}
                  placeholder="Search nodes... (press /)"
                  className="w-40 text-[11px] bg-transparent text-terminal-200 placeholder-terminal-600 focus:outline-none"
                />
                {searchQuery && (
                  <button
                    onClick={() => { setSearchQuery(''); graph.clearSelection() }}
                    className="text-terminal-500 hover:text-terminal-300"
                  >
                    <span className="text-xs">✕</span>
                  </button>
                )}
              </div>
            </Panel>
          )}

          {/* Wave 4.3: Focus mode breadcrumb */}
          {PIPELINE_FEATURES.focusMode && graph.focusBreadcrumb && (
            <Panel position="top-left" style={{ top: 42 }}>
              <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg px-2.5 py-1.5 flex items-center gap-1.5">
                {graph.focusBreadcrumb.map((crumb, i) => (
                  <span key={i} className="flex items-center gap-1.5">
                    {i > 0 && <span className="text-terminal-600 text-[10px]">›</span>}
                    {crumb.action ? (
                      <button
                        onClick={() => {
                          if (crumb.action === 'clear') graph.clearFocusMode()
                          else if (crumb.action === 'subsystem') {
                            const node = graph.nodeMap[graph.focusTarget]
                            if (node?.subsystem) graph.setFocusMode('subsystem', node.subsystem)
                          }
                        }}
                        className="text-[10px] text-amber-400 hover:text-amber-300 underline"
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

          {/* Wave 4.1: Lineage depth slider (visible when node selected) */}
          {PIPELINE_FEATURES.lineageDepthSlider && graph.selectedNodeId && (
            <Panel position="bottom-left" style={{ bottom: 50 }}>
              <div className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg px-3 py-2 flex items-center gap-2">
                <span className="text-[10px] text-terminal-400 whitespace-nowrap">Depth:</span>
                <input
                  type="range"
                  min="1"
                  max="10"
                  value={graph.lineageDepth || 10}
                  onChange={e => {
                    const val = parseInt(e.target.value)
                    graph.setLineageDepth(val >= 10 ? null : val)
                  }}
                  className="w-20 h-1 accent-amber-400"
                />
                <span className="text-[10px] text-terminal-300 font-mono w-6 text-right">
                  {graph.lineageDepth || '∞'}
                </span>
              </div>
            </Panel>
          )}

          {/* Chat toggle + TV mode + Fit View + Recent Activity */}
          <Panel position="top-right">
            <div className="flex gap-2">
              {/* Wave 4.4: Sidebar toggle */}
              {PIPELINE_FEATURES.splitPanelSidebar && (
                <button
                  onClick={() => setSidebarOpen(prev => !prev)}
                  className={`bg-terminal-800/90 backdrop-blur border rounded-lg px-3 py-2 text-xs hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center ${
                    sidebarOpen ? 'border-amber-500 text-amber-400' : 'border-terminal-600 text-terminal-300'
                  }`}
                  title="Toggle node tree sidebar"
                >
                  Tree
                </button>
              )}
              <button
                onClick={panelAwareFitView}
                className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg px-3 py-2 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center"
                title="Fit graph to view (accounts for open panels)"
              >
                Fit
              </button>
              {usingSavedLayout ? (
                <button
                  onClick={clearLayoutPositions}
                  className="bg-terminal-800/90 backdrop-blur border border-amber-500 rounded-lg px-3 py-2 text-xs text-amber-400 hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center"
                  title="Clear saved layout and revert to auto-layout"
                >
                  Reset Layout
                </button>
              ) : (
                <button
                  onClick={saveLayoutPositions}
                  className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg px-3 py-2 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center"
                  title="Save current node positions as default layout"
                >
                  Set Default
                </button>
              )}
              <button
                onClick={() => setTvMode(prev => !prev)}
                className={`bg-terminal-800/90 backdrop-blur border rounded-lg px-3 py-2 text-xs hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center ${
                  tvMode ? 'border-amber-500 text-amber-400' : 'border-terminal-600 text-terminal-300'
                }`}
                title="Toggle TV mode (larger text for distant viewing)"
              >
                TV
              </button>
              <button
                onClick={() => setRecentActivityOpen(prev => !prev)}
                className={`bg-terminal-800/90 backdrop-blur border rounded-lg px-3 py-2 text-xs hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center ${
                  recentActivityOpen ? 'border-amber-500 text-amber-400' : 'border-terminal-600 text-terminal-300'
                }`}
                title="Recent pipeline activity"
              >
                Activity
              </button>
              <button
                onClick={() => setChatOpen(!chatOpen)}
                className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg px-3 py-2 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center"
              >
                {chatOpen ? 'Close Chat' : 'AI Chat'}
              </button>
            </div>
          </Panel>

          {/* Slice notification toast (Wave 2.13) */}
          {graph.sliceFrom && !graph.sliceTo && (
            <Panel position="bottom-center">
              <div className="bg-amber-500/20 border border-amber-500/30 rounded-lg px-4 py-2 text-xs text-amber-300 backdrop-blur">
                Hold Shift + Click on another node to slice pipeline
                <button
                  onClick={() => graph.clearSlice()}
                  className="ml-3 text-amber-400 hover:text-amber-200 underline"
                >
                  Cancel
                </button>
              </div>
            </Panel>
          )}
          {graph.sliceFrom && graph.sliceTo && (
            <Panel position="bottom-center">
              <div className="bg-green-500/20 border border-green-500/30 rounded-lg px-4 py-2 text-xs text-green-300 backdrop-blur flex items-center gap-3">
                Pipeline sliced: {graph.sliceNodeIds?.size || 0} nodes in path
                <button
                  onClick={() => graph.clearSlice()}
                  className="text-green-400 hover:text-green-200 underline"
                >
                  Clear slice
                </button>
              </div>
            </Panel>
          )}
        </ReactFlow>

        {/* Toolbar — positioned absolutely over canvas, tight to top */}
        <PipelineErrorBoundary name="PipelineToolbar">
          <PipelineToolbar
            topologyData={topologyData}
            graph={graph}
            onExperimentOverlay={handleExperimentOverlay}
            onCompositionOverlay={handleCompositionOverlay}
          />
        </PipelineErrorBoundary>
      </div>

      {/* Node Detail Panel */}
      {selectedNode && !chatOpen && (
        <PipelineErrorBoundary name="NodeDetailPanel" className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 z-50">
          <NodeDetailPanel
            node={selectedNode}
            onClose={() => graph.clearSelection()}
            graph={graph}
          />
        </PipelineErrorBoundary>
      )}

      {/* AI Chat Panel */}
      {chatOpen && (
        <PipelineErrorBoundary name="ChatPanel" className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 z-50">
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

      {/* Recent Activity Panel (Wave 2.14) */}
      {recentActivityOpen && !chatOpen && !selectedNode && (
        <PipelineErrorBoundary name="RecentActivity" className="fixed right-0 top-0 h-full w-[400px] bg-terminal-800 border-l border-terminal-600 z-50">
          <RecentActivity
            onNavigateToNode={(nodeId) => {
              setRecentActivityOpen(false)
              handleNavigateToNode(nodeId)
            }}
            onClose={() => setRecentActivityOpen(false)}
          />
        </PipelineErrorBoundary>
      )}
    </div>
  )
}
