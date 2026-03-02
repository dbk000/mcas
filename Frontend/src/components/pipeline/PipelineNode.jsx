import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'

const NODE_TYPE_ICONS = {
  data_source: '\u{1F4E1}',
  feature_engine: '\u{2699}',
  signal_generator: '\u{26A1}',
  signal_filter: '\u{1F50D}',
  strategy: '\u{1F3AF}',
  parameter: '\u{1F4CA}',
  backtest_engine: '\u{1F9EA}',
  metric_aggregator: '\u{1F4C8}',
  risk_manager: '\u{1F6E1}',
  execution_engine: '\u{1F680}',
  composition: '\u{1F9E9}',
  custom: '\u{2726}',
}

// Human-readable type labels (non-quant friendly)
const NODE_TYPE_LABELS = {
  data_source: 'Data Feed',
  feature_engine: 'Calculator',
  signal_generator: 'Signal',
  signal_filter: 'Filter',
  strategy: 'Strategy',
  parameter: 'Config',
  backtest_engine: 'Backtest',
  metric_aggregator: 'Metrics',
  risk_manager: 'Risk',
  execution_engine: 'Execution',
  composition: 'Assembly',
  custom: 'Custom',
}

const STATUS_LABELS = {
  deployed: 'Live',
  promising: 'Promising',
  tested: 'Tested',
}

const STATUS_BG = {
  deployed: 'bg-green-500/20 text-green-400',
  promising: 'bg-amber-500/20 text-amber-400',
  tested: 'bg-terminal-600 text-terminal-400',
}

function PipelineNode({ data, selected }) {
  const {
    label, nodeType, subsystem, subsystemColor, statusColor,
    versionCount, aggPf, currentVersion, description, owner,
    sourceFile, ports,
  } = data

  const icon = NODE_TYPE_ICONS[nodeType] || '\u{2726}'
  const typeLabel = NODE_TYPE_LABELS[nodeType] || nodeType
  const statusLabel = STATUS_LABELS[currentVersion?.status] || currentVersion?.status || ''
  const statusBg = STATUS_BG[currentVersion?.status] || STATUS_BG.tested

  // Port counts
  const inputCount = ports?.inputs?.length || 0
  const outputCount = ports?.outputs?.length || 0

  // Shorten source file
  const shortFile = sourceFile
    ? sourceFile.replace('worker/app/', '').replace('.py', '')
    : null

  return (
    <div
      className={`
        relative bg-terminal-800 border-[3px] rounded-xl px-6 py-4 w-[460px] h-[300px]
        transition-all duration-200 cursor-pointer flex flex-col overflow-hidden
        ${selected ? 'shadow-lg shadow-amber-500/20 scale-105' : 'hover:shadow-md hover:shadow-white/5'}
      `}
      style={{
        borderColor: selected ? '#f59e0b' : subsystemColor + '80',
      }}
    >
      {/* Top accent bar */}
      <div
        className="absolute top-0 left-0 right-0 h-3 rounded-t-lg"
        style={{ backgroundColor: subsystemColor }}
      />

      {/* Input handle */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-5 !h-5 !bg-terminal-500 !border-2 !border-terminal-700"
      />

      {/* Row 1: Icon + Name + Status badge */}
      <div className="flex items-center justify-between mt-1.5">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-3xl flex-shrink-0">{icon}</span>
          <div className="min-w-0">
            <div className="text-xl font-bold text-white truncate leading-tight">{label}</div>
            <div className="text-xs text-terminal-500 mt-0.5">{typeLabel}</div>
          </div>
        </div>
        {/* Status badge */}
        {statusLabel && (
          <span className={`text-sm font-semibold px-3 py-1 rounded-full flex-shrink-0 ml-2 ${statusBg}`}>
            {statusLabel}
          </span>
        )}
      </div>

      {/* Row 2: Subsystem + version + connections */}
      <div className="flex items-center gap-2 mt-2.5">
        <span
          className="text-sm px-2.5 py-0.5 rounded-full font-semibold capitalize"
          style={{
            backgroundColor: subsystemColor + '20',
            color: subsystemColor,
          }}
        >
          {subsystem}
        </span>
        {versionCount > 0 && (
          <span className="text-sm px-2.5 py-0.5 rounded-full bg-terminal-700 text-terminal-300 font-mono">
            v{currentVersion?.version_number || '?'}
          </span>
        )}
        {(inputCount > 0 || outputCount > 0) && (
          <span className="text-xs text-terminal-500 ml-auto">
            {inputCount > 0 && `${inputCount} in`}{inputCount > 0 && outputCount > 0 && ' / '}{outputCount > 0 && `${outputCount} out`}
          </span>
        )}
      </div>

      {/* Row 3: Description */}
      {description && (
        <p className="text-sm text-terminal-400 mt-2.5 line-clamp-3 leading-snug">
          {description}
        </p>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Row 4: Metrics bar (when available) */}
      {(aggPf != null || currentVersion?.win_rate != null || currentVersion?.trade_count != null) && (
        <div className="flex items-center gap-4 mt-2 py-2 px-3 -mx-1 bg-terminal-900/60 rounded-lg">
          {aggPf != null && (
            <div className="text-lg font-mono font-bold">
              <span className="text-terminal-500 text-sm">PF </span>
              <span className={aggPf >= 1.0 ? 'text-green-400' : 'text-red-400'}>
                {aggPf.toFixed(2)}
              </span>
            </div>
          )}
          {currentVersion?.win_rate != null && (
            <div className="text-lg font-mono font-bold">
              <span className="text-terminal-500 text-sm">Win </span>
              <span className="text-terminal-200">
                {(currentVersion.win_rate * 100).toFixed(0)}%
              </span>
            </div>
          )}
          {currentVersion?.trade_count != null && (
            <div className="text-lg font-mono font-bold">
              <span className="text-terminal-500 text-sm">Trades </span>
              <span className="text-terminal-200">
                {currentVersion.trade_count}
              </span>
            </div>
          )}
        </div>
      )}

      {/* Row 5: Footer — source file + owner */}
      <div className="flex items-center justify-between mt-2">
        {shortFile && (
          <span className="text-xs text-terminal-600 font-mono truncate max-w-[60%]" title={sourceFile}>
            {shortFile}
          </span>
        )}
        {owner && (
          <span className="text-xs text-terminal-500 font-mono ml-auto">
            {owner}
          </span>
        )}
        {!shortFile && !owner && <div />}
      </div>

      {/* Output handle */}
      <Handle
        type="source"
        position={Position.Right}
        className="!w-5 !h-5 !bg-terminal-500 !border-2 !border-terminal-700"
      />
    </div>
  )
}

export default memo(PipelineNode)
