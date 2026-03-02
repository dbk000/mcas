import { useState, useCallback, useMemo, useRef, useEffect } from 'react'

/**
 * usePipelineGraph — manages visual state, selection, path-tracing,
 * overlays, composition drafts, and pressure waves for the pipeline canvas.
 */
export default function usePipelineGraph(topologyData) {
  const [selectedNodeId, setSelectedNodeId] = useState(null)
  const [activeOverlay, setActiveOverlay] = useState(null) // { type: 'experiment'|'composition'|'learning', id }
  const [compositionDraft, setCompositionDraft] = useState(new Map()) // nodeId -> versionId
  const [causalArrows, setCausalArrows] = useState([]) // [{ from, to, label }]
  const [manualHighlight, setManualHighlight] = useState(null) // Set<nodeId> from chat
  const [pressureWaveOrigin, setPressureWaveOrigin] = useState(null)
  const pressureWaveTimer = useRef(null)

  // Build adjacency maps from topology edges
  const { upstreamMap, downstreamMap, nodeMap } = useMemo(() => {
    if (!topologyData) return { upstreamMap: {}, downstreamMap: {}, nodeMap: {} }

    const up = {}   // nodeId -> Set<nodeId>
    const down = {} // nodeId -> Set<nodeId>
    const nm = {}

    for (const n of (topologyData.nodes || [])) {
      up[n.id] = new Set()
      down[n.id] = new Set()
      nm[n.id] = n
    }

    for (const e of (topologyData.edges || [])) {
      if (down[e.source]) down[e.source].add(e.target)
      if (up[e.target]) up[e.target].add(e.source)
    }

    return { upstreamMap: up, downstreamMap: down, nodeMap: nm }
  }, [topologyData])

  // BFS traversal helper
  const bfs = useCallback((startId, adjacencyMap) => {
    const visited = new Set()
    const queue = [startId]
    while (queue.length > 0) {
      const current = queue.shift()
      if (visited.has(current)) continue
      visited.add(current)
      const neighbors = adjacencyMap[current]
      if (neighbors) {
        for (const n of neighbors) {
          if (!visited.has(n)) queue.push(n)
        }
      }
    }
    return visited
  }, [])

  const getUpstream = useCallback((nodeId) => {
    const result = bfs(nodeId, upstreamMap)
    result.delete(nodeId) // exclude self
    return result
  }, [bfs, upstreamMap])

  const getDownstream = useCallback((nodeId) => {
    const result = bfs(nodeId, downstreamMap)
    result.delete(nodeId) // exclude self
    return result
  }, [bfs, downstreamMap])

  // Select a node: highlight upstream + downstream, dim rest
  const selectNode = useCallback((nodeId) => {
    setSelectedNodeId(nodeId)
    setManualHighlight(null)
    setCausalArrows([])
  }, [])

  const clearSelection = useCallback(() => {
    setSelectedNodeId(null)
    setManualHighlight(null)
    setCausalArrows([])
  }, [])

  // Compute highlighted node IDs (selected + upstream + downstream)
  const highlightedNodeIds = useMemo(() => {
    if (manualHighlight) return manualHighlight

    if (!selectedNodeId) return new Set()
    const up = getUpstream(selectedNodeId)
    const down = getDownstream(selectedNodeId)
    const all = new Set([selectedNodeId, ...up, ...down])
    return all
  }, [selectedNodeId, getUpstream, getDownstream, manualHighlight])

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

    if (highlightedNodeIds.size === 0 && !activeOverlay) {
      // No selection, no overlay — everything default
      for (const n of allNodes) {
        states.set(n.id, 'default')
      }
      return states
    }

    for (const n of allNodes) {
      if (n.id === selectedNodeId) {
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
  }, [topologyData, selectedNodeId, highlightedNodeIds, activeOverlay])

  // Overlay methods
  const setExperimentOverlay = useCallback((experimentId, touchedNodeIds) => {
    setActiveOverlay({ type: 'experiment', id: experimentId })
    if (touchedNodeIds) setManualHighlight(new Set(touchedNodeIds))
  }, [])

  const setCompositionOverlay = useCallback((compositionId, nodeVersionMap) => {
    setActiveOverlay({ type: 'composition', id: compositionId })
    if (nodeVersionMap) {
      setManualHighlight(new Set(Object.keys(nodeVersionMap)))
    }
  }, [])

  const clearOverlay = useCallback(() => {
    setActiveOverlay(null)
    setManualHighlight(null)
    setCausalArrows([])
  }, [])

  // Composition draft methods
  const setVersionForNode = useCallback((nodeId, versionId) => {
    setCompositionDraft(prev => {
      const next = new Map(prev)
      next.set(nodeId, versionId)
      return next
    })
  }, [])

  const clearDraft = useCallback(() => {
    setCompositionDraft(new Map())
  }, [])

  // Cleanup pressure wave timer on unmount
  useEffect(() => {
    return () => {
      if (pressureWaveTimer.current) clearTimeout(pressureWaveTimer.current)
    }
  }, [])

  // Pressure wave: staggered CSS pulse downstream
  const triggerPressureWave = useCallback((nodeId) => {
    setPressureWaveOrigin(nodeId)
    if (pressureWaveTimer.current) clearTimeout(pressureWaveTimer.current)
    // Auto-clear after animation completes (~2s)
    pressureWaveTimer.current = setTimeout(() => {
      setPressureWaveOrigin(null)
    }, 2000)
  }, [])

  // Compute pressure wave distances for staggered animation
  const pressureWaveDistances = useMemo(() => {
    if (!pressureWaveOrigin) return new Map()
    const distances = new Map()
    const queue = [[pressureWaveOrigin, 0]]
    const visited = new Set()
    while (queue.length > 0) {
      const [current, dist] = queue.shift()
      if (visited.has(current)) continue
      visited.add(current)
      distances.set(current, dist)
      const neighbors = downstreamMap[current]
      if (neighbors) {
        for (const n of neighbors) {
          if (!visited.has(n)) queue.push([n, dist + 1])
        }
      }
    }
    return distances
  }, [pressureWaveOrigin, downstreamMap])

  // Causal arrows
  const showCausalArrow = useCallback((fromNodeId, toNodeId, label) => {
    setCausalArrows(prev => [...prev, { from: fromNodeId, to: toNodeId, label }])
  }, [])

  const clearCausalArrows = useCallback(() => {
    setCausalArrows([])
  }, [])

  // Chat integration: highlight arbitrary nodes
  const highlightNodes = useCallback((nodeIds) => {
    setSelectedNodeId(null)
    setManualHighlight(new Set(nodeIds))
  }, [])

  // Navigate to node (select + scroll into view handled by caller)
  const navigateToNode = useCallback((nodeId) => {
    selectNode(nodeId)
  }, [selectNode])

  return {
    selectedNodeId,
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
    activeOverlay,
    // Composition draft
    compositionDraft,
    setVersionForNode,
    clearDraft,
    // Pressure wave
    triggerPressureWave,
    pressureWaveOrigin,
    pressureWaveDistances,
    // Causal arrows
    causalArrows,
    showCausalArrow,
    clearCausalArrows,
    // Chat integration
    highlightNodes,
    navigateToNode,
    // Data
    nodeMap,
    upstreamMap,
    downstreamMap,
  }
}
