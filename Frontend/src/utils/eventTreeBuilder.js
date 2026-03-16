/**
 * Event Tree Builder (Wave 2.1)
 *
 * Converts a flat list of version events into a tree structure
 * using caused_by_event_id as the parent link.
 *
 * Two-pass algorithm (inspired by Jaeger's get_trace_topology.go):
 *   Pass 1: Index events by event_id
 *   Pass 2: Connect children to parents. Orphans (events whose parent
 *           isn't in the fetched set) go to a separate list.
 *
 * Returns { roots: TreeNode[], orphans: TreeNode[] }
 * where TreeNode = { event, children: TreeNode[], depth: number }
 */

/**
 * Build a tree from flat events array.
 * @param {Array} events - Flat array of event objects with event_id and caused_by_event_id
 * @param {number} maxDepth - Maximum tree depth to build (default: 20)
 * @returns {{ roots: TreeNode[], orphans: TreeNode[] }}
 */
export function buildEventTree(events, maxDepth = 20) {
  if (!events || events.length === 0) {
    return { roots: [], orphans: [] }
  }

  // Pass 1: Index all events by ID
  const nodeMap = new Map()
  for (const evt of events) {
    nodeMap.set(evt.event_id, {
      event: evt,
      children: [],
      depth: 0,
      _childCount: 0, // total descendants (for truncation display)
    })
  }

  // Pass 2: Connect children to parents
  const roots = []
  const orphans = []

  for (const evt of events) {
    const node = nodeMap.get(evt.event_id)

    if (!evt.caused_by_event_id) {
      // Root event — no parent
      roots.push(node)
    } else {
      const parent = nodeMap.get(evt.caused_by_event_id)
      if (parent) {
        parent.children.push(node)
      } else {
        // Orphan — parent not in fetched set
        orphans.push(node)
      }
    }
  }

  // Pass 3: Compute depths and child counts
  function computeDepths(node, depth) {
    node.depth = depth
    let count = node.children.length
    if (depth < maxDepth) {
      for (const child of node.children) {
        computeDepths(child, depth + 1)
        count += child._childCount
      }
    }
    node._childCount = count
  }

  for (const root of roots) {
    computeDepths(root, 0)
  }
  for (const orphan of orphans) {
    computeDepths(orphan, 0)
  }

  return { roots, orphans }
}

/**
 * Flatten a tree back to a list with depth info, respecting maxDepth.
 * Used for rendering in a flat list with indentation.
 *
 * @param {{ roots: TreeNode[], orphans: TreeNode[] }} tree
 * @param {number} expandedDepth - How deep to expand (default: 2)
 * @param {Set<string>} expandedIds - Event IDs that are manually expanded beyond expandedDepth
 * @returns {Array<{ event, depth, hasChildren, childCount, isOrphan, isExpanded }>}
 */
export function flattenTree(tree, expandedDepth = 2, expandedIds = new Set()) {
  const result = []

  function walk(node, isOrphan = false) {
    const isExpanded = node.depth < expandedDepth || expandedIds.has(node.event.event_id)
    result.push({
      event: node.event,
      depth: node.depth,
      hasChildren: node.children.length > 0,
      childCount: node._childCount,
      isOrphan,
      isExpanded,
    })

    if (isExpanded) {
      for (const child of node.children) {
        walk(child, isOrphan)
      }
    }
  }

  for (const root of tree.roots) {
    walk(root, false)
  }

  if (tree.orphans.length > 0) {
    for (const orphan of tree.orphans) {
      walk(orphan, true)
    }
  }

  return result
}
