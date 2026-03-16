import { getFeatureFlag } from './governed'

/**
 * Pipeline Feature Flags — thin re-export from governance kernel.
 *
 * Flags resolve from backend bootstrap config via governed.js.
 * The FEATURE_KEY_MAP in governed.js translates these camelCase names
 * to backend snake_case keys. Defaults apply if backend hasn't delivered config.
 *
 * Usage unchanged: PIPELINE_FEATURES.criticalPath (boolean)
 */
export const PIPELINE_FEATURES = {
  // Wave 1: Graph Readability
  get lodTiers() { return getFeatureFlag('lodTiers') },
  get keyboardShortcuts() { return getFeatureFlag('keyboardShortcuts') },
  get tvTextScaling() { return getFeatureFlag('tvTextScaling') },
  get webWorkerLayout() { return getFeatureFlag('webWorkerLayout') },
  get collapsibleGroups() { return getFeatureFlag('collapsibleGroups') },
  get panelAwareFitView() { return getFeatureFlag('panelAwareFitView') },
  get complexityCheck() { return getFeatureFlag('complexityCheck') },
  get edgeCulling() { return getFeatureFlag('edgeCulling') },
  get urlState() { return getFeatureFlag('urlState') },

  // Wave 2: Timeline & Biography
  get eventTreeView() { return getFeatureFlag('eventTreeView') },
  get progressiveDisclosure() { return getFeatureFlag('progressiveDisclosure') },
  get criticalPath() { return getFeatureFlag('criticalPath') },
  get eventAdjusters() { return getFeatureFlag('eventAdjusters') },
  get timeGapViz() { return getFeatureFlag('timeGapViz') },
  get tableTreeToggle() { return getFeatureFlag('tableTreeToggle') },
  get temporalSlider() { return getFeatureFlag('temporalSlider') },
  get beforeAfterState() { return getFeatureFlag('beforeAfterState') },
  get configurableFacets() { return getFeatureFlag('configurableFacets') },
  get pipelineSlicing() { return getFeatureFlag('pipelineSlicing') },
  get recentActivity() { return getFeatureFlag('recentActivity') },

  // Wave 3: Dependency Hardening
  get threeStateStaleness() { return getFeatureFlag('threeStateStaleness') },
  get outputHash() { return getFeatureFlag('outputHash') },
  get driftDetection() { return getFeatureFlag('driftDetection') },
  get diffVisualization() { return getFeatureFlag('diffVisualization') },
  get tieredConfirmation() { return getFeatureFlag('tieredConfirmation') },

  // Wave 4: Advanced Interaction
  get lineageDepthSlider() { return getFeatureFlag('lineageDepthSlider') },
  get searchFilter() { return getFeatureFlag('searchFilter') },
  get focusMode() { return getFeatureFlag('focusMode') },
  get splitPanelSidebar() { return getFeatureFlag('splitPanelSidebar') },
  get compositionDraftUI() { return getFeatureFlag('compositionDraftUI') },

  // Wave 5: Interoperability
  get openLineageAdapter() { return getFeatureFlag('openLineageAdapter') },
  get provenanceSearch() { return getFeatureFlag('provenanceSearch') },
}
