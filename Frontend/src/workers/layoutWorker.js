/**
 * Web Worker for dagre layout computation.
 * Offloads graph layout from main thread to prevent jank on large graphs.
 *
 * Contract:
 *   Input:  { requestId, nodes: [{id, subsystem}], edges: [{source, target}], options? }
 *   Output: { requestId, positions: { [nodeId]: {x, y} }, meta: { algorithm, elapsed_ms } }
 *
 * Consumer must check requestId matches latest request — discard stale results.
 * Cache key: topologyHash:algorithm:version
 */
import dagre from 'dagre'

const SUBSYSTEM_ORDER = [
  'ingestion', 'feature', 'signal', 'filtering', 'strategy',
  'parameter', 'backtest', 'metrics', 'risk', 'execution',
]

const NODE_WIDTH = 500
const NODE_HEIGHT = 320

// Simple layout cache keyed by topology hash
const layoutCache = new Map()
const MAX_CACHE_SIZE = 20

function computeTopologyHash(nodes, edges) {
  // Fast hash from sorted node+edge IDs
  const nodeStr = nodes.map(n => n.id).sort().join(',')
  const edgeStr = edges.map(e => `${e.source}->${e.target}`).sort().join(',')
  return `${nodeStr}|${edgeStr}`
}

function computeLayout(nodes, edges, options = {}) {
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({
    rankdir: options.rankdir || 'LR',
    nodesep: options.nodesep || 25,
    ranksep: options.ranksep || 40,
    marginx: 25,
    marginy: 25,
  })

  for (const node of nodes) {
    const subsystem = node.subsystem || ''
    const rankIndex = SUBSYSTEM_ORDER.indexOf(subsystem)
    const rank = subsystem === 'parameter'
      ? SUBSYSTEM_ORDER.indexOf('strategy')
      : (rankIndex >= 0 ? rankIndex : SUBSYSTEM_ORDER.length)
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT, rank })
  }

  for (const edge of edges) {
    g.setEdge(edge.source, edge.target)
  }

  dagre.layout(g)

  const positions = {}
  for (const node of nodes) {
    const pos = g.node(node.id)
    positions[node.id] = {
      x: pos.x - NODE_WIDTH / 2,
      y: pos.y - NODE_HEIGHT / 2,
    }
  }

  return positions
}

self.onmessage = function (e) {
  const { requestId, nodes, edges, options } = e.data
  const start = performance.now()

  // Check cache
  const hash = computeTopologyHash(nodes, edges)
  const cacheKey = `${hash}:${options?.rankdir || 'LR'}:1`

  if (layoutCache.has(cacheKey)) {
    self.postMessage({
      requestId,
      positions: layoutCache.get(cacheKey),
      meta: { algorithm: 'dagre', elapsed_ms: 0, cached: true },
    })
    return
  }

  const positions = computeLayout(nodes, edges, options)
  const elapsed = performance.now() - start

  // Cache result (evict oldest if full)
  if (layoutCache.size >= MAX_CACHE_SIZE) {
    const firstKey = layoutCache.keys().next().value
    layoutCache.delete(firstKey)
  }
  layoutCache.set(cacheKey, positions)

  self.postMessage({
    requestId,
    positions,
    meta: { algorithm: 'dagre', elapsed_ms: Math.round(elapsed), cached: false },
  })
}
