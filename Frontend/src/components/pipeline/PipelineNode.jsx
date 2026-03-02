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

function PipelineNode({ data, selected }) {
  const {
    label, nodeType, subsystem, subsystemColor, statusColor,
    versionCount, aggPf, currentVersion,
  } = data

  const icon = NODE_TYPE_ICONS[nodeType] || '\u{2726}'

  return (
    <div
      className={`
        relative bg-terminal-800 border-[3px] rounded-xl px-5 py-4 min-w-[320px]
        transition-all duration-200 cursor-pointer
        ${selected ? 'shadow-lg shadow-amber-500/20 scale-105' : 'hover:shadow-md hover:shadow-white/5'}
      `}
      style={{
        borderColor: selected ? '#f59e0b' : subsystemColor + '80',
      }}
    >
      {/* Top accent bar */}
      <div
        className="absolute top-0 left-0 right-0 h-2 rounded-t-lg"
        style={{ backgroundColor: subsystemColor }}
      />

      {/* Input handle */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-3 !h-3 !bg-terminal-500 !border-2 !border-terminal-700"
      />

      {/* Header row */}
      <div className="flex items-center justify-between mb-2.5 mt-1">
        <div className="flex items-center gap-2.5 min-w-0">
          <span className="text-lg flex-shrink-0">{icon}</span>
          <span className="text-base font-bold text-white truncate">{label}</span>
        </div>
        {/* Status dot */}
        <div
          className="w-3 h-3 rounded-full flex-shrink-0 ml-2"
          style={{ backgroundColor: statusColor }}
          title={currentVersion?.status || 'no version'}
        />
      </div>

      {/* Subsystem tag */}
      <div className="flex items-center gap-2 mb-2">
        <span
          className="text-xs px-2 py-0.5 rounded-full font-medium capitalize"
          style={{
            backgroundColor: subsystemColor + '20',
            color: subsystemColor,
          }}
        >
          {subsystem}
        </span>
        {versionCount > 0 && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-terminal-700 text-terminal-400">
            v{currentVersion?.version_number || '?'} ({versionCount})
          </span>
        )}
      </div>

      {/* Metric */}
      {aggPf != null && (
        <div className="text-base font-mono font-semibold">
          <span className="text-terminal-500">PF </span>
          <span className={aggPf >= 1.0 ? 'text-green-400' : 'text-red-400'}>
            {aggPf.toFixed(2)}
          </span>
        </div>
      )}

      {/* Output handle */}
      <Handle
        type="source"
        position={Position.Right}
        className="!w-3 !h-3 !bg-terminal-500 !border-2 !border-terminal-700"
      />
    </div>
  )
}

export default memo(PipelineNode)
