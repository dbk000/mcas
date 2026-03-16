import { useEffect } from 'react'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

export function usePipelineKeyboardShortcuts({
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
}) {
  useEffect(() => {
    function isTextInputFocused() {
      const element = document.activeElement
      if (!element) {
        return false
      }
      const tag = element.tagName.toLowerCase()
      if (tag === 'input' || tag === 'textarea') {
        return true
      }
      return element.contentEditable === 'true'
    }

    function handleKeyDown(event) {
      if (isTextInputFocused()) {
        return
      }

      switch (event.key) {
        case 'Escape':
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
          event.preventDefault()
          break

        case 'ArrowRight':
        case 'ArrowDown': {
          if (!graph.selectedNodeId) {
            break
          }
          const downstream = graph.downstreamMap?.[graph.selectedNodeId]
          if (downstream?.size > 0) {
            const nextId = downstream.values().next().value
            graph.selectNode(nextId)
            const node = nodes.find((candidate) => candidate.id === nextId)
            if (node && reactFlowRef.current) {
              reactFlowRef.current.setCenter(node.position.x + 250, node.position.y + 160, { zoom: undefined, duration: 300 })
            }
            event.preventDefault()
          }
          break
        }

        case 'ArrowLeft':
        case 'ArrowUp': {
          if (!graph.selectedNodeId) {
            break
          }
          const upstream = graph.upstreamMap?.[graph.selectedNodeId]
          if (upstream?.size > 0) {
            const previousId = upstream.values().next().value
            graph.selectNode(previousId)
            const node = nodes.find((candidate) => candidate.id === previousId)
            if (node && reactFlowRef.current) {
              reactFlowRef.current.setCenter(node.position.x + 250, node.position.y + 160, { zoom: undefined, duration: 300 })
            }
            event.preventDefault()
          }
          break
        }

        case '/':
          if (PIPELINE_FEATURES.searchFilter) {
            event.preventDefault()
            const searchInput = document.querySelector('[data-pipeline-search]')
            if (searchInput) {
              searchInput.focus()
            }
          }
          break

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
  }, [chatOpen, graph, nodes, reactFlowRef, recentActivityOpen, searchQuery, setChatOpen, setRecentActivityOpen, setSearchQuery, setSidebarOpen, sidebarOpen])
}
