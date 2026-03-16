import { useReducer, useCallback, useMemo, useRef, useEffect } from 'react'

/**
 * usePipelineGraph — manages visual state, selection, path-tracing,
 * overlays, composition drafts, and pressure waves for the pipeline canvas.
 *
 * RENDERER-AGNOSTIC INVARIANT:
 *   This hook MUST NOT import from @xyflow/react or any renderer-specific library.
 *   It provides abstract graph state. The renderer (Pipeline.jsx for React Flow,
 *   or a future Pipeline3D.jsx for Three.js) maps this state to visual elements.
 *   No viewport, camera, position, or input-device state belongs here.
 */

// --- Reducer ---

const initialState = {
  selectedNodeId: null,
  activeOverlay: null,       // { type: 'experiment'|'composition'|'learning', id }
  compositionDraft: new Map(), // nodeId -> versionId
  causalArrows: [],          // [{ from, to, label }]
  manualHighlight: null,     // Set<nodeId> from chat
  pressureWaveOrigin: null,
  // Pipeline slicing (Wave 2.13)
  sliceFrom: null,           // nodeId — first endpoint
  sliceTo: null,             // nodeId — second endpoint
  sliceNodeIds: null,        // Set<nodeId> — computed path between from/to
  // Wave 4.1: Lineage depth slider — null = unlimited
  lineageDepth: null,
  // Wave 4.3: Focus mode — off | node | subsystem
  focusMode: 'off',          // 'off' | 'node' | 'subsystem'
  focusTarget: null,         // nodeId (when mode=node) or subsystem string (when mode=subsystem)
}

function graphReducer(state, action) {
  switch (action.type) {
    case 'SELECT_NODE':
      return {
        ...state,
        selectedNodeId: action.nodeId,
        manualHighlight: null,
        causalArrows: [],
      }

    case 'CLEAR_SELECTION':
      return {
        ...state,
        selectedNodeId: null,
        manualHighlight: null,
        causalArrows: [],
      }

    case 'SET_EXPERIMENT_OVERLAY':
      return {
        ...state,
        activeOverlay: { type: 'experiment', id: action.experimentId },
        manualHighlight: action.touchedNodeIds ? new Set(action.touchedNodeIds) : state.manualHighlight,
      }

    case 'SET_COMPOSITION_OVERLAY':
      return {
        ...state,
        activeOverlay: { type: 'composition', id: action.compositionId },
        manualHighlight: action.nodeVersionMap ? new Set(Object.keys(action.nodeVersionMap)) : state.manualHighlight,
      }

    case 'CLEAR_OVERLAY':
      return {
        ...state,
        activeOverlay: null,
        manualHighlight: null,
        causalArrows: [],
      }

    case 'SET_VERSION_FOR_NODE': {
      const next = new Map(state.compositionDraft)
      next.set(action.nodeId, action.versionId)
      return { ...state, compositionDraft: next }
    }

    case 'CLEAR_DRAFT':
      return { ...state, compositionDraft: new Map() }

    case 'SET_PRESSURE_WAVE':
      return { ...state, pressureWaveOrigin: action.nodeId }

    case 'CLEAR_PRESSURE_WAVE':
      return { ...state, pressureWaveOrigin: null }

    case 'SHOW_CAUSAL_ARROW':
      return {
        ...state,
        causalArrows: [...state.causalArrows, { from: action.from, to: action.to, label: action.label }],
      }

    case 'CLEAR_CAUSAL_ARROWS':
      return { ...state, causalArrows: [] }

    case 'HIGHLIGHT_NODES':
      return {
        ...state,
        selectedNodeId: null,
        manualHighlight: new Set(action.nodeIds),
      }

    // Pipeline slicing (Wave 2.13)
    case 'START_SLICE':
      return {
        ...state,
        sliceFrom: action.nodeId,
        sliceTo: null,
        sliceNodeIds: null,
      }

    case 'COMPLETE_SLICE':
      return {
        ...state,
        sliceTo: action.nodeId,
        sliceNodeIds: action.pathNodeIds, // Set<nodeId>
        manualHighlight: action.pathNodeIds,
      }

    case 'CLEAR_SLICE':
      return {
        ...state,
        sliceFrom: null,
        sliceTo: null,
        sliceNodeIds: null,
        manualHighlight: null,
      }

    // Wave 4.1: Lineage depth slider
    case 'SET_LINEAGE_DEPTH':
      return { ...state, lineageDepth: action.depth }

    // Wave 4.3: Focus mode
    case 'SET_FOCUS_MODE':
      return {
        ...state,
        focusMode: action.mode,    // 'node' | 'subsystem'
        focusTarget: action.target, // nodeId or subsystem string
      }

    case 'CLEAR_FOCUS_MODE':
      return { ...state, focusMode: 'off', focusTarget: null }

    default:
      return state
  }
}

function traverseBreadthFirst(startId, adjacencyMap, maxDepth = null) {
  const visited = new Set()
  const queue = [[startId, 0]]

  for (let index = 0; index < queue.length; index += 1) {
    const [current, depth] = queue[index]
    if (visited.has(current)) continue

    visited.add(current)
    if (maxDepth != null && depth >= maxDepth) continue

    const neighbors = adjacencyMap[current]
    if (!neighbors) continue

    for (const neighbor of neighbors) {
      if (!visited.has(neighbor)) {
        queue.push([neighbor, depth + 1])
      }
    }
  }

  return visited
}

function findPathBreadthFirst(startId, endId, adjacencyMap) {
  const parents = new Map([[startId, null]])
  const queue = [startId]

  for (let index = 0; index < queue.length; index += 1) {
    const current = queue[index]
    if (current === endId) {
      const path = []
      let cursor = endId

      while (cursor !== null) {
        path.unshift(cursor)
        cursor = parents.get(cursor)
      }

      return new Set(path)
    }

    const neighbors = adjacencyMap[current]
    if (!neighbors) continue

    for (const neighbor of neighbors) {
      if (!parents.has(neighbor)) {
        parents.set(neighbor, current)
        queue.push(neighbor)
      }
    }
  }

  return null
}

// --- Hook ---

export default function usePipelineGraph(topologyData) {
  const [state, dispatch] = useReducer(graphReducer, initialState)
  const pressureWaveTimer = useRef(null)

  // Build adjacency maps from topology edges
  const { upstreamMap, downstreamMap, nodeMap, subsystemNodeIdsMap } = useMemo(() => {
    if (!topologyData) {
      return {
        upstreamMap: {},
        downstreamMap: {},
        nodeMap: {},
        subsystemNodeIdsMap: new Map(),
      }
    }

    const up = {}   // nodeId -> Set<nodeId>
    const down = {} // nodeId -> Set<nodeId>
    const nm = {}
    const subsystemNodes = new Map()

    for (const n of (topologyData.nodes || [])) {
      up[n.id] = new Set()
      down[n.id] = new Set()
      nm[n.id] = n

      if (!subsystemNodes.has(n.subsystem)) {
        subsystemNodes.set(n.subsystem, new Set())
      }
      subsystemNodes.get(n.subsystem).add(n.id)
    }

    for (const e of (topologyData.edges || [])) {
      if (down[e.source]) down[e.source].add(e.target)
      if (up[e.target]) up[e.target].add(e.source)
    }

    return {
      upstreamMap: up,
      downstreamMap: down,
      nodeMap: nm,
      subsystemNodeIdsMap: subsystemNodes,
    }
  }, [topologyData])

  // BFS traversal helper (supports optional depth limit for Wave 4.1)
  const bfs = useCallback((startId, adjacencyMap, maxDepth = null) => {
    return traverseBreadthFirst(startId, adjacencyMap, maxDepth)
  }, [])

  const getUpstream = useCallback((nodeId, depth = null) => {
    const result = bfs(nodeId, upstreamMap, depth)
    result.delete(nodeId) // exclude self
    return result
  }, [bfs, upstreamMap])

  const getDownstream = useCallback((nodeId, depth = null) => {
    const result = bfs(nodeId, downstreamMap, depth)
    result.delete(nodeId) // exclude self
    return result
  }, [bfs, downstreamMap])

  // Select a node: highlight upstream + downstream, dim rest
  const selectNode = useCallback((nodeId) => {
    dispatch({ type: 'SELECT_NODE', nodeId })
  }, [])

  const clearSelection = useCallback(() => {
    dispatch({ type: 'CLEAR_SELECTION' })
  }, [])

  // Compute highlighted node IDs (selected + upstream + downstream)
  // Wave 4.1: Respects lineageDepth when set
  // Wave 4.3: Focus mode overrides selection highlighting
  const highlightedNodeIds = useMemo(() => {
    if (state.manualHighlight) return state.manualHighlight

    // Focus mode: highlight all connected nodes in the focus context
    if (state.focusMode === 'node' && state.focusTarget) {
      const up = getUpstream(state.focusTarget)
      const down = getDownstream(state.focusTarget)
      return new Set([state.focusTarget, ...up, ...down])
    }
    if (state.focusMode === 'subsystem' && state.focusTarget) {
      const subsystemNodes = new Set(subsystemNodeIdsMap.get(state.focusTarget) || [])
      // Also include immediate upstream/downstream of subsystem nodes
      for (const nid of [...subsystemNodes]) {
        const up = getUpstream(nid, 1)
        const down = getDownstream(nid, 1)
        for (const id of up) subsystemNodes.add(id)
        for (const id of down) subsystemNodes.add(id)
      }
      return subsystemNodes
    }

    if (!state.selectedNodeId) return new Set()
    const depth = state.lineageDepth
    const up = getUpstream(state.selectedNodeId, depth)
    const down = getDownstream(state.selectedNodeId, depth)
    const all = new Set([state.selectedNodeId, ...up, ...down])
    return all
  }, [state.selectedNodeId, getUpstream, getDownstream, state.manualHighlight,
      state.lineageDepth, state.focusMode, state.focusTarget, subsystemNodeIdsMap])

  // Compute highlighted edge IDs
  const highlightedEdgeIds = useMemo(() => {
    if (!topologyData || highlightedNodeIds.size === 0) return new Set()
    const edgeIds = new Set()
    for (const e of (topologyData.edges || [])) {
      if (highlightedNodeIds.has(e.source) && highlightedNodeIds.has(e.target)) {
        edgeIds.add(e.id)
      }
    }
    return edgeIds
  }, [topologyData, highlightedNodeIds])

  // Compute visual state per node
  const visualState = useMemo(() => {
    const states = new Map()
    const allNodes = topologyData?.nodes || []

    if (highlightedNodeIds.size === 0 && !state.activeOverlay) {
      // No selection, no overlay — everything default
      for (const n of allNodes) {
        states.set(n.id, 'default')
      }
      return states
    }

    for (const n of allNodes) {
      if (n.id === state.selectedNodeId) {
        states.set(n.id, 'selected')
      } else if (highlightedNodeIds.has(n.id)) {
        states.set(n.id, 'highlighted')
      } else if (highlightedNodeIds.size > 0) {
        states.set(n.id, 'dimmed')
      } else {
        states.set(n.id, 'default')
      }
    }
    return states
  }, [topologyData, state.selectedNodeId, highlightedNodeIds, state.activeOverlay])

  // Overlay methods
  const setExperimentOverlay = useCallback((experimentId, touchedNodeIds) => {
    dispatch({ type: 'SET_EXPERIMENT_OVERLAY', experimentId, touchedNodeIds })
  }, [])

  const setCompositionOverlay = useCallback((compositionId, nodeVersionMap) => {
    dispatch({ type: 'SET_COMPOSITION_OVERLAY', compositionId, nodeVersionMap })
  }, [])

  const clearOverlay = useCallback(() => {
    dispatch({ type: 'CLEAR_OVERLAY' })
  }, [])

  // Composition draft methods
  const setVersionForNode = useCallback((nodeId, versionId) => {
    dispatch({ type: 'SET_VERSION_FOR_NODE', nodeId, versionId })
  }, [])

  const clearDraft = useCallback(() => {
    dispatch({ type: 'CLEAR_DRAFT' })
  }, [])

  // Cleanup pressure wave timer on unmount
  useEffect(() => {
    return () => {
      if (pressureWaveTimer.current) clearTimeout(pressureWaveTimer.current)
    }
  }, [])

  // Pressure wave: staggered CSS pulse downstream
  const triggerPressureWave = useCallback((nodeId) => {
    dispatch({ type: 'SET_PRESSURE_WAVE', nodeId })
    if (pressureWaveTimer.current) clearTimeout(pressureWaveTimer.current)
    // Auto-clear after animation completes (~2s)
    pressureWaveTimer.current = setTimeout(() => {
      dispatch({ type: 'CLEAR_PRESSURE_WAVE' })
    }, 2000)
  }, [])

  // Compute pressure wave distances for staggered animation
  const pressureWaveDistances = useMemo(() => {
    if (!state.pressureWaveOrigin) return new Map()
    const distances = new Map()
    const queue = [[state.pressureWaveOrigin, 0]]
    const visited = new Set()

    for (let index = 0; index < queue.length; index += 1) {
      const [current, dist] = queue[index]
      if (visited.has(current)) continue
      visited.add(current)
      distances.set(current, dist)
      const neighbors = downstreamMap[current]
      if (neighbors) {
        for (const neighbor of neighbors) {
          if (!visited.has(neighbor)) queue.push([neighbor, dist + 1])
        }
      }
    }

    return distances
  }, [state.pressureWaveOrigin, downstreamMap])

  // Causal arrows
  const showCausalArrow = useCallback((fromNodeId, toNodeId, label) => {
    dispatch({ type: 'SHOW_CAUSAL_ARROW', from: fromNodeId, to: toNodeId, label })
  }, [])

  const clearCausalArrows = useCallback(() => {
    dispatch({ type: 'CLEAR_CAUSAL_ARROWS' })
  }, [])

  // Chat integration: highlight arbitrary nodes
  const highlightNodes = useCallback((nodeIds) => {
    dispatch({ type: 'HIGHLIGHT_NODES', nodeIds })
  }, [])

  // Pipeline slicing (Wave 2.13)
  // Two-step: startSlice(from) then completeSlice(to) — BFS finds path between them
  const startSlice = useCallback((nodeId) => {
    dispatch({ type: 'START_SLICE', nodeId })
  }, [])

  const completeSlice = useCallback((nodeId) => {
    if (!state.sliceFrom) return
    // BFS both directions to find path between sliceFrom and nodeId
    // Try downstream first (from -> to), then upstream (to -> from)
    let pathNodeIds = null

    // Try downstream path
    pathNodeIds = findPathBreadthFirst(state.sliceFrom, nodeId, downstreamMap)
    if (!pathNodeIds) {
      // Try upstream path (reversed direction)
      pathNodeIds = findPathBreadthFirst(state.sliceFrom, nodeId, upstreamMap)
    }
    if (!pathNodeIds) {
      // Try bidirectional: combine downstream from both, find intersection
      const downFromA = bfs(state.sliceFrom, downstreamMap)
      const downFromB = bfs(nodeId, downstreamMap)
      const upFromA = bfs(state.sliceFrom, upstreamMap)
      const upFromB = bfs(nodeId, upstreamMap)
      // Union of all reachable nodes from both endpoints
      pathNodeIds = new Set([...downFromA, ...upFromA, ...downFromB, ...upFromB]
        .filter(id => (downFromA.has(id) || upFromA.has(id)) && (downFromB.has(id) || upFromB.has(id)))
      )
      // Always include the endpoints
      pathNodeIds.add(state.sliceFrom)
      pathNodeIds.add(nodeId)
    }

    dispatch({ type: 'COMPLETE_SLICE', nodeId, pathNodeIds })
  }, [state.sliceFrom, downstreamMap, upstreamMap, bfs])

  const clearSlice = useCallback(() => {
    dispatch({ type: 'CLEAR_SLICE' })
  }, [])

  // Wave 4.1: Lineage depth slider
  const setLineageDepth = useCallback((depth) => {
    dispatch({ type: 'SET_LINEAGE_DEPTH', depth })
  }, [])

  // Wave 4.3: Focus mode
  const setFocusMode = useCallback((mode, target) => {
    dispatch({ type: 'SET_FOCUS_MODE', mode, target })
  }, [])

  const clearFocusMode = useCallback(() => {
    dispatch({ type: 'CLEAR_FOCUS_MODE' })
  }, [])

  // Wave 4.3: Focus breadcrumb trail
  const focusBreadcrumb = useMemo(() => {
    if (state.focusMode === 'off') return null
    const crumbs = [{ label: 'Full Graph', action: 'clear' }]
    if (state.focusMode === 'subsystem') {
      crumbs.push({ label: `Subsystem: ${state.focusTarget}`, action: null })
    }
    if (state.focusMode === 'node') {
      const node = nodeMap[state.focusTarget]
      if (node?.subsystem) {
        crumbs.push({ label: `Subsystem: ${node.subsystem}`, action: 'subsystem' })
      }
      crumbs.push({ label: node?.name || state.focusTarget, action: null })
    }
    return crumbs
  }, [state.focusMode, state.focusTarget, nodeMap])

  // Wave 4.6: Version data cache (Map<nodeId, { versions, fetchedAt }>)
  const versionCacheRef = useRef(new Map())

  const getCachedVersions = useCallback((nodeId) => {
    const entry = versionCacheRef.current.get(nodeId)
    if (!entry) return null
    // 5-minute TTL
    if (Date.now() - entry.fetchedAt > 5 * 60 * 1000) {
      versionCacheRef.current.delete(nodeId)
      return null
    }
    return entry.versions
  }, [])

  const setCachedVersions = useCallback((nodeId, versions) => {
    versionCacheRef.current.set(nodeId, { versions, fetchedAt: Date.now() })
  }, [])

  // Navigate to node (select + scroll into view handled by caller)
  const navigateToNode = useCallback((nodeId) => {
    selectNode(nodeId)
  }, [selectNode])

  return {
    selectedNodeId: state.selectedNodeId,
    highlightedNodeIds,
    highlightedEdgeIds,
    visualState,
    selectNode,
    clearSelection,
    getUpstream,
    getDownstream,
    // Overlays
    setExperimentOverlay,
    setCompositionOverlay,
    clearOverlay,
    activeOverlay: state.activeOverlay,
    // Composition draft
    compositionDraft: state.compositionDraft,
    setVersionForNode,
    clearDraft,
    // Pressure wave
    triggerPressureWave,
    pressureWaveOrigin: state.pressureWaveOrigin,
    pressureWaveDistances,
    // Causal arrows
    causalArrows: state.causalArrows,
    showCausalArrow,
    clearCausalArrows,
    // Chat integration
    highlightNodes,
    navigateToNode,
    // Pipeline slicing
    sliceFrom: state.sliceFrom,
    sliceTo: state.sliceTo,
    sliceNodeIds: state.sliceNodeIds,
    startSlice,
    completeSlice,
    clearSlice,
    // Wave 4.1: Lineage depth
    lineageDepth: state.lineageDepth,
    setLineageDepth,
    // Wave 4.3: Focus mode
    focusMode: state.focusMode,
    focusTarget: state.focusTarget,
    setFocusMode,
    clearFocusMode,
    focusBreadcrumb,
    // Wave 4.6: Version cache
    getCachedVersions,
    setCachedVersions,
    // Data
    nodeMap,
    upstreamMap,
    downstreamMap,
  }
}
