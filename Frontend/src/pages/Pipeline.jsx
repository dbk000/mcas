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
import usePipelineGraph from '../hooks/usePipelineGraph'
import usePipelineUrlState from '../hooks/usePipelineUrlState'

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
  const [tvMode, setTvMode] = useState(false)
  const [recentActivityOpen, setRecentActivityOpen] = useState(false)
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
          // Universal dismiss: slice → overlay → recent → chat → selection
          if (graph.sliceFrom) {
            graph.clearSlice()
          } else if (graph.activeOverlay) {
            graph.clearOverlay()
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

        default:
          break
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [graph, chatOpen, setChatOpen, recentActivityOpen, nodes])

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

  // Layout effect: use Web Worker for dagre, with requestId staleness check
  useEffect(() => {
    if (flowNodes.length === 0) return

    const requestId = ++layoutRequestIdRef.current
    const workerNodes = flowNodes.map(n => ({ id: n.id, subsystem: n.data.subsystem }))
    const workerEdges = flowEdges.map(e => ({ source: e.source, target: e.target }))

    function applyPositions(positions) {
      // Check staleness
      if (requestId !== layoutRequestIdRef.current) return

      const layouted = flowNodes.map(n => ({
        ...n,
        position: positions[n.id] || { x: 0, y: 0 },
      }))
      layoutRef.current = { nodes: layouted, edges: flowEdges }
      setNodes(layouted)
      setEdges(flowEdges)

      setTimeout(() => {
        reactFlowRef.current?.fitView({ padding: 0.15, duration: 300 })
      }, 100)
    }

    if (layoutWorker) {
      const handler = (e) => {
        if (e.data.requestId !== requestId) return // discard stale
        layoutWorker.removeEventListener('message', handler)
        applyPositions(e.data.positions)
      }
      layoutWorker.addEventListener('message', handler)
      layoutWorker.postMessage({ requestId, nodes: workerNodes, edges: workerEdges })
    } else {
      // Main-thread fallback (dagre imported dynamically)
      import('dagre').then(({ default: dagre }) => {
        const g = new dagre.graphlib.Graph()
        g.setDefaultEdgeLabel(() => ({}))
        g.setGraph({ rankdir: 'LR', nodesep: 25, ranksep: 40, marginx: 25, marginy: 25 })
        const SUBSYSTEM_ORDER_LOCAL = ['ingestion','feature','signal','filtering','strategy','parameter','backtest','metrics','risk','execution']
        workerNodes.forEach(n => {
          const ri = SUBSYSTEM_ORDER_LOCAL.indexOf(n.subsystem)
          const rank = n.subsystem === 'parameter' ? SUBSYSTEM_ORDER_LOCAL.indexOf('strategy') : (ri >= 0 ? ri : SUBSYSTEM_ORDER_LOCAL.length)
          g.setNode(n.id, { width: 500, height: 320, rank })
        })
        workerEdges.forEach(e => g.setEdge(e.source, e.target))
        dagre.layout(g)
        const positions = {}
        workerNodes.forEach(n => {
          const pos = g.node(n.id)
          positions[n.id] = { x: pos.x - 250, y: pos.y - 160 }
        })
        applyPositions(positions)
      })
    }
  }, [flowNodes, flowEdges, setNodes, setEdges])

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

  const panelOpen = selectedNode || chatOpen || recentActivityOpen

  return (
    <div className={`h-[calc(100vh-8rem)] flex ${tvMode ? 'pipeline-tv-mode' : ''} relative`} data-complexity-tier={complexityTier}>
      {/* Subsystem legend moved to Layout Header — only collapse controls remain here */}

      {/* React Flow Canvas */}
      <div className={`flex-1 relative transition-all duration-300 ${panelOpen ? 'mr-[400px]' : ''}`}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeClick={onNodeClick}
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

          {/* Toolbar */}
          <PipelineErrorBoundary name="PipelineToolbar">
            <PipelineToolbar
              topologyData={topologyData}
              graph={graph}
              onExperimentOverlay={handleExperimentOverlay}
              onCompositionOverlay={handleCompositionOverlay}
            />
          </PipelineErrorBoundary>

          {/* Chat toggle + TV mode + Fit View + Recent Activity */}
          <Panel position="top-right">
            <div className="flex gap-2">
              <button
                onClick={panelAwareFitView}
                className="bg-terminal-800/90 backdrop-blur border border-terminal-600 rounded-lg px-3 py-2 text-xs text-terminal-300 hover:text-terminal-100 hover:bg-terminal-700 transition-colors pipeline-touch-target flex items-center justify-center"
                title="Fit graph to view (accounts for open panels)"
              >
                Fit
              </button>
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
