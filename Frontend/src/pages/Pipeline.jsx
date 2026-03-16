import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useNodesState,
  useEdgesState,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { useApi } from '../context/ApiContext'
import PipelineNode from '../components/pipeline/PipelineNode'
import PipelineToolbar from '../components/pipeline/PipelineToolbar'
import usePipelineGraph from '../hooks/usePipelineGraph'
import usePipelineUrlState from '../hooks/usePipelineUrlState'
import { PIPELINE_FEATURES } from '../config/pipelineFeatures'
import { useToast } from '../context/ToastContext'
import PipelineCanvasPanels from './pipeline/PipelineCanvasPanels'
import PipelineSidePanels from './pipeline/PipelineSidePanels'
import { usePipelineKeyboardShortcuts } from './pipeline/usePipelineKeyboardShortcuts'
import {
  DEFAULT_FACETS,
  DEFAULT_GRID_POSITIONS,
  LAYOUT_STORAGE_KEY,
  SUBSYSTEM_COLORS,
} from './pipeline/layoutConfig'

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

export default function Pipeline() {
  return <PipelineInner />
}

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
  const facets = DEFAULT_FACETS
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

  usePipelineKeyboardShortcuts({
    chatOpen,
    graph,
    nodes,
    recentActivityOpen,
    reactFlowRef,
    searchQuery,
    setChatOpen,
    setRecentActivityOpen,
    setSearchQuery,
    setSidebarOpen,
    sidebarOpen,
  })

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

  // Build flow nodes/edges
  const { flowNodes, flowEdges } = useMemo(() => {
    if (!topologyData) return { flowNodes: [], flowEdges: [] }

    const apiNodes = topologyData.nodes || []
    const apiEdges = topologyData.edges || []
    const nodes = apiNodes.map((node) => {
      const currentVersion = node.current_version
      let statusColor = '#6b7280'
      if (currentVersion?.status === 'deployed') statusColor = '#22c55e'
      else if (currentVersion?.status === 'promising') statusColor = '#f59e0b'

      return {
        id: node.id,
        type: 'pipeline',
        data: {
          label: node.name,
          nodeType: node.type,
          subsystem: node.subsystem,
          subsystemColor: SUBSYSTEM_COLORS[node.subsystem] || '#6b7280',
          statusColor,
          versionCount: node.version_count,
          aggPf: currentVersion?.agg_pf,
          currentVersion,
          description: node.description,
          sourceFile: node.source_file,
          owner: node.owner,
          ports: node.ports,
          visualState: 'default',
          pressureWaveDelay: null,
          facets,
        },
      }
    })

    const edges = apiEdges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourceHandle: edge.sourceHandle,
      targetHandle: edge.targetHandle,
      animated: true,
      style: { stroke: '#475569', strokeWidth: 3, opacity: 1 },
      markerEnd: { type: MarkerType.ArrowClosed, color: '#475569', width: 20, height: 20 },
    }))

    return { flowNodes: nodes, flowEdges: edges }
  }, [topologyData, facets])

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
    } catch { /* localStorage full or unavailable */ }
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
    } catch { /* corrupted localStorage, fall through to default grid */ }

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
      <div className="flex h-full items-center justify-center">
        <div className="text-sm text-terminal-400">Loading pipeline topology...</div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="text-center">
          <p className="mb-2 text-sm text-red-400">Failed to load pipeline topology</p>
          <p className="text-xs text-terminal-500">{error}</p>
        </div>
      </div>
    )
  }

  const panelOpen = selectedNode || chatOpen || recentActivityOpen

  return (
    <div className={`flex h-[calc(100vh-3.5rem)] ${tvMode ? 'pipeline-tv-mode' : ''} relative`} data-complexity-tier={complexityTier}>
      <PipelineSidePanels
        chatOpen={chatOpen}
        graph={graph}
        handleExperimentOverlay={handleExperimentOverlay}
        handleHighlightNodes={handleHighlightNodes}
        handleNavigateToNode={handleNavigateToNode}
        handlePressureWave={handlePressureWave}
        recentActivityOpen={recentActivityOpen}
        selectedNode={selectedNode}
        setChatOpen={setChatOpen}
        setRecentActivityOpen={setRecentActivityOpen}
        setSidebarOpen={setSidebarOpen}
        sidebarOpen={sidebarOpen}
        topologyData={topologyData}
      />

      <div className={`relative flex-1 transition-all duration-300 ${panelOpen ? 'mr-[400px]' : ''} ${sidebarOpen ? 'ml-[260px]' : ''}`}>
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
            className="!border-terminal-600 !bg-terminal-800 !shadow-lg [&>button:hover]:!bg-terminal-600 [&>button]:!border-terminal-600 [&>button]:!bg-terminal-700 [&>button]:!text-terminal-300"
          />
          <MiniMap
            nodeColor={minimapNodeColor}
            maskColor="rgba(13, 17, 23, 0.8)"
            className="!border-terminal-600 !bg-terminal-800"
            position="bottom-right"
          />
          <PipelineCanvasPanels
            chatOpen={chatOpen}
            clearLayoutPositions={clearLayoutPositions}
            graph={graph}
            handleSearchChange={handleSearchChange}
            panelAwareFitView={panelAwareFitView}
            recentActivityOpen={recentActivityOpen}
            saveLayoutPositions={saveLayoutPositions}
            searchQuery={searchQuery}
            setChatOpen={setChatOpen}
            setRecentActivityOpen={setRecentActivityOpen}
            setSearchQuery={setSearchQuery}
            setSidebarOpen={setSidebarOpen}
            setTvMode={setTvMode}
            sidebarOpen={sidebarOpen}
            tvMode={tvMode}
            usingSavedLayout={usingSavedLayout}
          />
        </ReactFlow>

        <PipelineToolbar
          topologyData={topologyData}
          graph={graph}
          onExperimentOverlay={handleExperimentOverlay}
          onCompositionOverlay={handleCompositionOverlay}
        />
      </div>
    </div>
  )
}
