/**
 * resolveVisualState — Pure function that computes the visual presentation
 * of a pipeline node from its current state dimensions.
 *
 * This is the single source of truth for "how should this node look?"
 * All visual state decisions are made here, not scattered across components.
 *
 * @param {Object} input
 * @param {boolean} input.isSelected - Node is the currently selected node
 * @param {boolean} input.isHighlighted - Node is in the highlight set (upstream/downstream of selection)
 * @param {boolean} input.isDimmed - Node is outside the highlight set
 * @param {boolean} input.isAlert - Node has a validation failure or staleness alert
 * @param {boolean} input.isSearchMatch - Node matches current search query
 * @param {boolean} input.pressureWaveActive - Pressure wave animation is hitting this node
 * @param {string|null} input.stalenessState - 'clean' | 'potentially_stale' | 'confirmed_stale' | null
 * @param {string|null} input.healthStatus - 'healthy' | 'degraded' | 'failing' | null
 * @param {boolean} input.inComposition - Node is part of the active composition overlay
 * @param {boolean} input.inExperiment - Node is part of the active experiment overlay
 * @param {boolean} input.inDraft - Node is in the active composition draft
 *
 * @returns {Object} { primary: string, badges: string[] }
 *   primary: exactly one of 'selected' | 'alert' | 'search-match' | 'highlighted' | 'dimmed' | 'default'
 *   badges: zero or more of 'staleness-warning' | 'staleness-critical' | 'health-degraded' |
 *           'health-failing' | 'composition-member' | 'experiment-member' | 'draft-member' | 'pressure-wave'
 */
export function resolveVisualState({
  isSelected = false,
  isHighlighted = false,
  isDimmed = false,
  isAlert = false,
  isSearchMatch = false,
  pressureWaveActive = false,
  stalenessState = null,
  healthStatus = null,
  inComposition = false,
  inExperiment = false,
  inDraft = false,
} = {}) {
  // --- Primary state: priority stack (highest wins) ---
  let primary = 'default'

  if (isDimmed) primary = 'dimmed'
  if (isHighlighted) primary = 'highlighted'
  if (isSearchMatch) primary = 'search-match'
  if (isAlert) primary = 'alert'
  if (isSelected) primary = 'selected'

  // --- Secondary badges: independent, accumulate ---
  const badges = []

  if (stalenessState === 'confirmed_stale') badges.push('staleness-critical')
  else if (stalenessState === 'potentially_stale') badges.push('staleness-warning')

  if (healthStatus === 'failing') badges.push('health-failing')
  else if (healthStatus === 'degraded') badges.push('health-degraded')

  if (inComposition) badges.push('composition-member')
  if (inExperiment) badges.push('experiment-member')
  if (inDraft) badges.push('draft-member')
  if (pressureWaveActive) badges.push('pressure-wave')

  return { primary, badges }
}

/**
 * PRIMARY_STYLES — CSS/style config for each primary visual state.
 * Used by PipelineNode.jsx to translate primary state into visual appearance.
 */
export const PRIMARY_STYLES = {
  selected: {
    borderColor: () => '#f59e0b',
    opacity: 1,
    extraClasses: 'shadow-lg shadow-amber-500/20 scale-105',
  },
  alert: {
    borderColor: () => '#ef4444',
    opacity: 1,
    extraClasses: 'shadow-lg shadow-red-500/30 animate-pulse-subtle',
  },
  'search-match': {
    borderColor: () => '#3b82f6',
    opacity: 1,
    extraClasses: 'shadow-md shadow-blue-500/20 ring-2 ring-blue-500/40',
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
  default: {
    borderColor: (subsystemColor) => subsystemColor + '80',
    opacity: 1,
    extraClasses: '',
  },
}

/**
 * BADGE_CONFIG — Visual config for each badge type.
 * Used by PipelineNode.jsx to render badge icons/indicators.
 */
export const BADGE_CONFIG = {
  'staleness-critical': { icon: '!', color: '#ef4444', position: 'top-right', tooltip: 'Confirmed stale' },
  'staleness-warning': { icon: '?', color: '#f59e0b', position: 'top-right', tooltip: 'Potentially stale' },
  'health-failing': { icon: '\u00D7', color: '#ef4444', position: 'top-right-2', tooltip: 'Health: failing' },
  'health-degraded': { icon: '\u2013', color: '#f59e0b', position: 'top-right-2', tooltip: 'Health: degraded' },
  'composition-member': { icon: null, color: null, position: 'bottom-bar', tooltip: 'In composition' },
  'experiment-member': { icon: '\u{1F9EA}', color: '#8b5cf6', position: 'top-left', tooltip: 'In experiment' },
  'draft-member': { icon: '\u270E', color: '#3b82f6', position: 'top-left', tooltip: 'In draft' },
  'pressure-wave': { icon: null, color: null, position: 'animation', tooltip: null },
}
