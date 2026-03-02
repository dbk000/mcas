import { Component } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'

/**
 * PipelineErrorBoundary — Catches render errors in pipeline sub-components.
 * Prevents one broken panel from crashing the entire Pipeline surface.
 *
 * Usage:
 *   <PipelineErrorBoundary name="NodeDetailPanel">
 *     <NodeDetailPanel ... />
 *   </PipelineErrorBoundary>
 */
export default class PipelineErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error }
  }

  componentDidCatch(error, errorInfo) {
    console.error(`[PipelineErrorBoundary:${this.props.name}]`, error, errorInfo)
  }

  handleRetry = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      const { name, className } = this.props
      return (
        <div className={`flex flex-col items-center justify-center gap-3 p-6 ${className || ''}`}>
          <AlertTriangle className="w-8 h-8 text-red-400/60" />
          <div className="text-center">
            <p className="text-sm text-terminal-300 font-medium">
              {name || 'This panel'} encountered an error
            </p>
            <p className="text-xs text-terminal-500 mt-1 max-w-[280px]">
              {this.state.error?.message || 'An unexpected error occurred'}
            </p>
          </div>
          <button
            onClick={this.handleRetry}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs text-amber-400 bg-amber-600/10 border border-amber-600/20 rounded hover:bg-amber-600/20 transition-colors"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Retry
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
