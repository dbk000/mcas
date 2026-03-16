import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { AlertTriangle } from 'lucide-react'
import { PIPELINE_FEATURES } from '../../config/pipelineFeatures'

const STALENESS_ICONS = {
  clean: { icon: '\u{2713}', color: 'text-green-400', bg: 'bg-green-500/20', label: 'Clean' },
  potentially_stale: { icon: '?', color: 'text-amber-400', bg: 'bg-amber-500/20', label: 'Potentially stale' },
  confirmed_stale: { icon: '!', color: 'text-red-400', bg: 'bg-red-500/20', label: 'Stale' },
}

const HEALTH_DOTS = {
  healthy: '#22c55e',
  degraded: '#f59e0b',
  unhealthy: '#ef4444',
  unknown: '#64748b',
}

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

// Visual state style configuration
const VISUAL_STATE_STYLES = {
  default: {
    borderColor: (subsystemColor) => subsystemColor + '80',
    opacity: 1,
    extraClasses: '',
  },
  selected: {
    borderColor: () => '#f59e0b',
    opacity: 1,
    extraClasses: 'shadow-lg shadow-amber-500/20 scale-105',
  },
  highlighted: {
    borderColor: (subsystemColor) => subsystemColor,
    opacity: 1,
    extraClasses: 'animate-pulse-subtle',
  },
  dimmed: {
    borderColor: (subsystemColor) => subsystemColor + '30',
    opacity: 0.35,
    extraClasses: '',
  },
  alert: {
    borderColor: () => '#ef4444',
    opacity: 1,
    extraClasses: 'shadow-lg shadow-red-500/30 animate-pulse-subtle',
  },
}

// Default facet visibility (Wave 2.12: configurable node facets)
const DEFAULT_FACETS = {
  pf: true,
  winRate: true,
  trades: true,
  status: true,
  owner: true,
  description: true,
  connections: true,
}

function PipelineNode({ data, selected }) {
  const {
    label, nodeType, subsystem, subsystemColor,
    versionCount, aggPf, currentVersion, description, owner,
    sourceFile, ports, visualState, pressureWaveDelay,
    lodTier = 1,
    facets,
  } = data

  // Merge with defaults — facets prop overrides
  const f = facets ? { ...DEFAULT_FACETS, ...facets } : DEFAULT_FACETS

  const icon = NODE_TYPE_ICONS[nodeType] || '\u{2726}'
  const typeLabel = NODE_TYPE_LABELS[nodeType] || nodeType
  const statusLabel = STATUS_LABELS[currentVersion?.status] || currentVersion?.status || ''
  const statusBg = STATUS_BG[currentVersion?.status] || STATUS_BG.tested

  const inputCount = ports?.inputs?.length || 0
  const outputCount = ports?.outputs?.length || 0

  const shortFile = sourceFile
    ? sourceFile.replace('worker/app/', '').replace('.py', '')
    : null

  // Determine visual state styling
  const vs = visualState || (selected ? 'selected' : 'default')
  const stateStyle = VISUAL_STATE_STYLES[vs] || VISUAL_STATE_STYLES.default
  const borderColor = stateStyle.borderColor(subsystemColor)
  const nodeOpacity = stateStyle.opacity

  // Pressure wave animation
  const pressureStyle = pressureWaveDelay != null
    ? { animation: `pressure-wave 600ms ease-out ${pressureWaveDelay}ms` }
    : {}

  // LOD CSS classes: visibility swap (NOT conditional rendering)
  // tier 1 = full, tier 2 = name-only, tier 3 = minimal
  const lodHideDetail = lodTier >= 2 ? 'invisible h-0 overflow-hidden' : ''
  const lodHideName = lodTier >= 3 ? 'invisible h-0 overflow-hidden' : ''

  return (
    <div
      className={`
        relative flex w-[460px] cursor-pointer flex-col overflow-hidden rounded-xl
        border-[3px] bg-terminal-800 px-6 py-4 transition-all duration-200
        ${lodTier >= 3 ? 'h-[80px]' : lodTier >= 2 ? 'h-[120px]' : 'h-[300px]'}
        ${stateStyle.extraClasses}
        ${!visualState && selected ? 'scale-105 shadow-lg shadow-amber-500/20' : ''}
        ${!visualState && !selected ? 'hover:shadow-md hover:shadow-white/5' : ''}
      `}
      style={{
        borderColor,
        opacity: nodeOpacity,
        ...pressureStyle,
      }}
    >
      {/* Top accent bar — always visible at all LOD tiers */}
      <div
        className="absolute inset-x-0 top-0 h-3 rounded-t-lg"
        style={{ backgroundColor: subsystemColor }}
      />

      {/* Staleness badge — top-right corner (Wave 3.1) */}
      {PIPELINE_FEATURES.threeStateStaleness && currentVersion?.staleness_state && currentVersion.staleness_state !== 'clean' && (
        <div
          className={`absolute right-3 top-4 z-10 flex size-6 items-center justify-center rounded-full text-xs font-bold ${STALENESS_ICONS[currentVersion.staleness_state]?.bg || ''} ${STALENESS_ICONS[currentVersion.staleness_state]?.color || ''}`}
          title={STALENESS_ICONS[currentVersion.staleness_state]?.label || currentVersion.staleness_state}
        >
          {STALENESS_ICONS[currentVersion.staleness_state]?.icon || '?'}
        </div>
      )}

      {/* Input handle — always visible */}
      <Handle
        type="target"
        position={Position.Left}
        className="!h-5 !w-5 !border-2 !border-terminal-700 !bg-terminal-500"
      />

      {/* Row 1: Icon + Name + Status badge + Alert — visible at tier 1 & 2 */}
      <div className={`mt-1.5 flex items-center justify-between ${lodHideName}`}>
        <div className="flex min-w-0 items-center gap-3">
          <span className="shrink-0 text-3xl">{icon}</span>
          <div className="min-w-0">
            <div className="pipeline-text-lg truncate font-bold leading-tight text-white">{label}</div>
            <div className={`pipeline-text-xs mt-0.5 text-terminal-500 ${lodHideDetail}`}>{typeLabel}</div>
          </div>
        </div>
        <div className="ml-2 flex shrink-0 items-center gap-2">
          {vs === 'alert' && (
            <span className="text-red-400" title="Stale metrics or failed validation">
              <AlertTriangle className="size-5" />
            </span>
          )}
          {PIPELINE_FEATURES.threeStateStaleness && currentVersion?.health_status && currentVersion.health_status !== 'unknown' && (
            <span
              className="size-3 shrink-0 rounded-full"
              style={{ backgroundColor: HEALTH_DOTS[currentVersion.health_status] || HEALTH_DOTS.unknown }}
              title={`Health: ${currentVersion.health_status}`}
            />
          )}
          {statusLabel && (
            <span className={`pipeline-text-sm rounded-full px-3 py-1 font-semibold ${statusBg}`}>
              {statusLabel}
            </span>
          )}
        </div>
      </div>

      {/* Tier 3 compact: just icon + truncated label inline */}
      {lodTier >= 3 && (
        <div className="mt-2 flex items-center gap-2">
          <span className="shrink-0 text-2xl">{icon}</span>
          <span className="truncate text-base font-bold text-white">{label}</span>
        </div>
      )}

      {/* Row 2: Subsystem + version + connections — tier 1 & 2 */}
      <div className={`mt-2.5 flex items-center gap-2 ${lodHideDetail}`}>
        <span
          className="pipeline-text-sm rounded-full px-2.5 py-0.5 font-semibold capitalize"
          style={{
            backgroundColor: subsystemColor + '20',
            color: subsystemColor,
          }}
        >
          {subsystem}
        </span>
        {versionCount > 0 && (
          <span className="pipeline-text-sm rounded-full bg-terminal-700 px-2.5 py-0.5 font-mono text-terminal-300">
            v{currentVersion?.version_number || '?'}
          </span>
        )}
        {f.connections && (inputCount > 0 || outputCount > 0) && (
          <span className="pipeline-text-xs ml-auto text-terminal-500">
            {inputCount > 0 && `${inputCount} in`}{inputCount > 0 && outputCount > 0 && ' / '}{outputCount > 0 && `${outputCount} out`}
          </span>
        )}
      </div>

      {/* Row 3: Description — tier 1 only, respects facets.description */}
      <div className={lodHideDetail}>
        {f.description && description && (
          <p className="pipeline-text-sm mt-2.5 line-clamp-3 leading-snug text-terminal-400">
            {description}
          </p>
        )}
      </div>

      {/* Spacer — tier 1 only */}
      <div className={`flex-1 ${lodHideDetail}`} />

      {/* Row 4: Metrics bar — tier 1 only, respects facet toggles */}
      <div className={lodHideDetail}>
        {((f.pf && aggPf != null) || (f.winRate && currentVersion?.win_rate != null) || (f.trades && currentVersion?.trade_count != null)) && (
          <div className="-mx-1 mt-2 flex items-center gap-4 rounded-lg bg-terminal-900/60 px-3 py-2">
            {f.pf && aggPf != null && (
              <div className="pipeline-text-base font-mono font-bold">
                <span className="pipeline-text-sm text-terminal-500">PF </span>
                <span className={aggPf >= 1.0 ? 'text-green-400' : 'text-red-400'}>
                  {aggPf.toFixed(2)}
                </span>
              </div>
            )}
            {f.winRate && currentVersion?.win_rate != null && (
              <div className="pipeline-text-base font-mono font-bold">
                <span className="pipeline-text-sm text-terminal-500">Win </span>
                <span className="text-terminal-200">
                  {(currentVersion.win_rate * 100).toFixed(0)}%
                </span>
              </div>
            )}
            {f.trades && currentVersion?.trade_count != null && (
              <div className="pipeline-text-base font-mono font-bold">
                <span className="pipeline-text-sm text-terminal-500">Trades </span>
                <span className="text-terminal-200">
                  {currentVersion.trade_count}
                </span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Row 5: Footer — tier 1 only, respects facets.owner */}
      <div className={`mt-2 flex items-center justify-between ${lodHideDetail}`}>
        {shortFile && (
          <span className="pipeline-text-xs max-w-[60%] truncate font-mono text-terminal-600" title={sourceFile}>
            {shortFile}
          </span>
        )}
        {f.owner && owner && (
          <span className="pipeline-text-xs ml-auto font-mono text-terminal-500">
            {owner}
          </span>
        )}
        {!shortFile && !(f.owner && owner) && <div />}
      </div>

      {/* Output handle — always visible */}
      <Handle
        type="source"
        position={Position.Right}
        className="!h-5 !w-5 !border-2 !border-terminal-700 !bg-terminal-500"
      />
    </div>
  )
}

export default memo(PipelineNode)
