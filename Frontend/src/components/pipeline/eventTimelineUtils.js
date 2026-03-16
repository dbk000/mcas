export const EVENT_TYPE_ICONS = {
  code_change: '\u{1F527}',
  backtest: '\u{1F4CA}',
  script_run: '\u{25B6}',
  composition_included: '\u{1F3AF}',
  composition_removed: '\u{274C}',
  decision_made: '\u{1F4A1}',
  hypothesis_formed: '\u{1F9EA}',
  conversation_note: '\u{1F4AC}',
  manual_observation: '\u{1F441}',
  optimisation_started: '\u{2699}',
  optimisation_referenced: '\u{1F517}',
  referenced_in_experiment: '\u{1F52C}',
  abandoned: '\u{1F6D1}',
  revived: '\u{267B}',
  revalidated: '\u{2705}',
  downstream_triggered: '\u{27A1}',
  parameter_informed_by: '\u{1F4D0}',
  pushed_to_live: '\u{1F680}',
  node_added: '\u{2795}',
  node_retired: '\u{1F5D1}',
  edge_added: '\u{1F517}',
  edge_removed: '\u{26D4}',
}

export function extractEventsFromResponse(response) {
  return response?.data?.events || response?.events || []
}
