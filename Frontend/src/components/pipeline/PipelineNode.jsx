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

const STATUS_LABELS = {
  deployed: 'Deployed',
  promising: 'Promising',
  tested: 'Tested',
}

function PipelineNode({ data, selected }) {
  const {
    label, nodeType, subsystem, subsystemColor, statusColor,
    versionCount, aggPf, currentVersion, description, owner,
  } = data

  const icon = NODE_TYPE_ICONS[nodeType] || '\u{2726}'
  const statusLabel = STATUS_LABELS[currentVersion?.status] || currentVersion?.status || 'No version'

  return (
    <div
      className={`
        relative bg-terminal-800 border-[3px] rounded-xl px-6 py-5 min-w-[400px] min-h-[200px]
        transition-all duration-200 cursor-pointer flex flex-col
        ${selected ? 'shadow-lg shadow-amber-500/20 scale-105' : 'hover:shadow-md hover:shadow-white/5'}
      `}
      style={{
        borderColor: selected ? '#f59e0b' : subsystemColor + '80',
      }}
    >
      {/* Top accent bar */}
      <div
        className="absolute top-0 left-0 right-0 h-2.5 rounded-t-lg"
        style={{ backgroundColor: subsystemColor }}
      />

      {/* Input handle */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-4 !h-4 !bg-terminal-500 !border-2 !border-terminal-700"
      />

      {/* Header row */}
      <div className="flex items-center justify-between mb-3 mt-1">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-2xl flex-shrink-0">{icon}</span>
          <span className="text-xl font-bold text-white truncate">{label}</span>
        </div>
        {/* Status indicator */}
        <div className="flex items-center gap-2 flex-shrink-0 ml-3">
          <span className="text-xs font-medium text-terminal-400">{statusLabel}</span>
          <div
            className="w-4 h-4 rounded-full"
            style={{ backgroundColor: statusColor }}
          />
        </div>
      </div>

      {/* Subsystem + version tags */}
      <div className="flex items-center gap-2.5 mb-3">
        <span
          className="text-sm px-2.5 py-1 rounded-full font-semibold capitalize"
          style={{
            backgroundColor: subsystemColor + '20',
            color: subsystemColor,
          }}
        >
          {subsystem}
        </span>
        {versionCount > 0 && (
          <span className="text-sm px-2.5 py-1 rounded-full bg-terminal-700 text-terminal-300">
            v{currentVersion?.version_number || '?'} ({versionCount})
          </span>
        )}
      </div>

      {/* Description */}
      {description && (
        <p className="text-sm text-terminal-400 mb-3 line-clamp-2 leading-relaxed">
          {description}
        </p>
      )}

      {/* Spacer to push metrics to bottom */}
      <div className="flex-1" />

      {/* Bottom metrics row */}
      <div className="flex items-center justify-between">
        {/* Profit Factor */}
        {aggPf != null ? (
          <div className="text-xl font-mono font-bold">
            <span className="text-terminal-500">PF </span>
            <span className={aggPf >= 1.0 ? 'text-green-400' : 'text-red-400'}>
              {aggPf.toFixed(2)}
            </span>
          </div>
        ) : (
          <div />
        )}

        {/* Owner */}
        {owner && (
          <span className="text-xs text-terminal-500 font-mono">
            {owner}
          </span>
        )}
      </div>

      {/* Output handle */}
      <Handle
        type="source"
        position={Position.Right}
        className="!w-4 !h-4 !bg-terminal-500 !border-2 !border-terminal-700"
      />
    </div>
  )
}

export default memo(PipelineNode)
