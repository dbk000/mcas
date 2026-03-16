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
