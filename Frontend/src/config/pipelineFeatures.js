/**
 * Pipeline Feature Flags
 *
 * Toggle individual features per wave without reverting entire deployments.
 * All flags default to false — enable as features are implemented and verified.
 *
 * Usage in components:
 *   import { PIPELINE_FEATURES } from '../config/pipelineFeatures'
 *   if (PIPELINE_FEATURES.criticalPath) { ... }
 */

export const PIPELINE_FEATURES = {
  // Wave 1: Graph Readability
  lodTiers: false,              // 1.2: Multi-scale rendering (3 LOD tiers)
  keyboardShortcuts: false,     // 1.3: Keyboard shortcuts + TV remote nav
  tvTextScaling: false,         // 1.4: TV text scaling system
  webWorkerLayout: false,       // 1.5: Web Worker for dagre layout
  collapsibleGroups: false,     // 1.6: Collapsible subsystem groups
  panelAwareFitView: false,     // 1.7: Chart size accounts for open panels
  complexityCheck: false,       // 1.8: Graph preflight complexity check
  edgeCulling: false,           // 1.9: Edge viewport culling
  urlState: false,              // 1.1: URL-driven state / deep linking

  // Wave 2: Timeline & Biography
  eventTreeView: true,          // 2.1: Flat-to-tree builder with orphan handling
  progressiveDisclosure: true,  // 2.2: Depth-limited progressive disclosure
  criticalPath: true,           // 2.3: Critical path computation
  eventAdjusters: true,         // 2.4: Event adjuster pipeline
  timeGapViz: true,             // 2.5: Time gap visualization
  tableTreeToggle: true,        // 2.6: Table/tree view toggle
  temporalSlider: true,         // 2.7: Temporal lineage slider
  beforeAfterState: true,       // 2.8: Before/after state on events
  configurableFacets: true,     // 2.12: Configurable node facets
  pipelineSlicing: true,        // 2.13: Pipeline slicing
  recentActivity: true,         // 2.14: Recent Activity summary

  // Wave 3: Dependency Hardening
  threeStateStaleness: true,    // 3.1: Three-state staleness
  outputHash: true,             // 3.2: Output hash on node_version
  driftDetection: true,         // 3.4: Drift detection
  diffVisualization: true,      // 3.5: Diff visualization
  tieredConfirmation: true,     // 3.6: Tiered confirmation gates

  // Wave 4: Advanced Interaction
  lineageDepthSlider: false,    // 4.1: Lineage depth slider
  searchFilter: false,          // 4.2: Search/filter over graph nodes
  focusMode: false,             // 4.3: Focus mode with breadcrumb
  splitPanelSidebar: false,     // 4.4: Split panel sidebar
  compositionDraftUI: false,    // 4.9: Composition draft lifecycle

  // Wave 5: Interoperability
  openLineageAdapter: false,    // 5.1: OpenLineage event adapter
  provenanceSearch: false,      // 5.2: Provenance search API
}
