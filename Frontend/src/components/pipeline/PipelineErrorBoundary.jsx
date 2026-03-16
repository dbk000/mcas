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
          <AlertTriangle className="size-8 text-red-400/60" />
          <div className="text-center">
            <p className="text-sm font-medium text-terminal-300">
              {name || 'This panel'} encountered an error
            </p>
            <p className="mt-1 max-w-[280px] text-xs text-terminal-500">
              {this.state.error?.message || 'An unexpected error occurred'}
            </p>
          </div>
          <button
            onClick={this.handleRetry}
            className="flex items-center gap-1.5 rounded border border-amber-600/20 bg-amber-600/10 px-3 py-1.5 text-xs text-amber-400 transition-colors hover:bg-amber-600/20"
          >
            <RotateCcw className="size-3.5" />
            Retry
          </button>
        </div>
      )
    }

    return this.props.children
  }
}
