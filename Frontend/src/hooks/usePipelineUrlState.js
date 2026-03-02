import { useEffect, useRef } from 'react'

/**
 * usePipelineUrlState — Syncs pipeline graph state to/from URL search params.
 * Enables deep linking and browser back/forward navigation.
 *
 * Encoded params:
 *   ?node=<nodeId>                     — selected node
 *   ?overlay=<type>&overlayId=<id>     — active overlay
 *   ?chat=1                            — chat panel open
 *
 * Camera position intentionally NOT in URL (volatile, not shareable).
 */
export default function usePipelineUrlState(graph, topologyData, chatOpen, setChatOpen) {
  const initializedRef = useRef(false)
  const suppressPushRef = useRef(false)

  // On mount: read URL params and apply to graph state
  useEffect(() => {
    if (!topologyData || initializedRef.current) return
    initializedRef.current = true

    const params = new URLSearchParams(window.location.search)
    const nodeId = params.get('node')
    const overlayType = params.get('overlay')
    const overlayId = params.get('overlayId')
    const chat = params.get('chat')

    suppressPushRef.current = true

    if (nodeId && topologyData.nodes?.some(n => n.id === nodeId)) {
      graph.selectNode(nodeId)
    }

    if (overlayType === 'experiment' && overlayId) {
      graph.setExperimentOverlay(overlayId, [])
    } else if (overlayType === 'composition' && overlayId) {
      graph.setCompositionOverlay(overlayId, {})
    }

    if (chat === '1') {
      setChatOpen(true)
    }

    // Allow pushes after a tick
    requestAnimationFrame(() => { suppressPushRef.current = false })
  }, [topologyData, graph, setChatOpen])

  // On state change: push to URL (replaceState to avoid polluting history)
  useEffect(() => {
    if (!initializedRef.current || suppressPushRef.current) return

    const params = new URLSearchParams()

    if (graph.selectedNodeId) {
      params.set('node', graph.selectedNodeId)
    }

    if (graph.activeOverlay) {
      params.set('overlay', graph.activeOverlay.type)
      params.set('overlayId', graph.activeOverlay.id)
    }

    if (chatOpen) {
      params.set('chat', '1')
    }

    const search = params.toString()
    const newUrl = search
      ? `${window.location.pathname}?${search}`
      : window.location.pathname

    if (newUrl !== window.location.pathname + window.location.search) {
      window.history.replaceState(null, '', newUrl)
    }
  }, [graph.selectedNodeId, graph.activeOverlay, chatOpen])

  // Handle browser back/forward
  useEffect(() => {
    function handlePopState() {
      const params = new URLSearchParams(window.location.search)
      const nodeId = params.get('node')

      suppressPushRef.current = true

      if (nodeId) {
        graph.selectNode(nodeId)
      } else {
        graph.clearSelection()
      }

      requestAnimationFrame(() => { suppressPushRef.current = false })
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [graph])
}
