/**
 * Test fixtures for pipeline graph tests.
 * Provides a minimal but representative topology for testing
 * usePipelineGraph, visual state resolution, and BFS traversal.
 */

/**
 * Minimal 5-node linear topology:
 *   data_source → feature_engine → signal_generator → strategy → execution_engine
 */
export const LINEAR_TOPOLOGY = {
  nodes: [
    { id: 'n1', name: 'OHLCV Feed', type: 'data_source', subsystem: 'ingestion', description: 'Binance OHLCV', version_count: 3, current_version: { version_number: 3, status: 'deployed', agg_pf: null }, ports: { inputs: [], outputs: [{ id: 'p1', name: 'ohlcv', data_type: 'ohlcv_bar' }] } },
    { id: 'n2', name: 'RSI Calculator', type: 'feature_engine', subsystem: 'feature', description: 'RSI 14-period', version_count: 5, current_version: { version_number: 5, status: 'deployed', agg_pf: null }, ports: { inputs: [{ id: 'p2', name: 'ohlcv', data_type: 'ohlcv_bar' }], outputs: [{ id: 'p3', name: 'features', data_type: 'indicator_set' }] } },
    { id: 'n3', name: 'S1 RSI Bounce', type: 'signal_generator', subsystem: 'signal', description: 'BB squeeze + RSI', version_count: 8, current_version: { version_number: 8, status: 'promising', agg_pf: 1.15, win_rate: 0.52, trade_count: 245 }, ports: { inputs: [{ id: 'p4', name: 'features', data_type: 'indicator_set' }], outputs: [{ id: 'p5', name: 'signals', data_type: 'signal_event' }] } },
    { id: 'n4', name: 'Risk Filter', type: 'strategy', subsystem: 'strategy', description: 'Position sizing + risk', version_count: 2, current_version: { version_number: 2, status: 'tested', agg_pf: null }, ports: { inputs: [{ id: 'p6', name: 'signals', data_type: 'signal_event' }], outputs: [{ id: 'p7', name: 'orders', data_type: 'order_event' }] } },
    { id: 'n5', name: 'Paper Engine', type: 'execution_engine', subsystem: 'execution', description: 'Paper trading', version_count: 1, current_version: { version_number: 1, status: 'deployed', agg_pf: null }, ports: { inputs: [{ id: 'p8', name: 'orders', data_type: 'order_event' }], outputs: [] } },
  ],
  edges: [
    { id: 'e1', source: 'n1', target: 'n2' },
    { id: 'e2', source: 'n2', target: 'n3' },
    { id: 'e3', source: 'n3', target: 'n4' },
    { id: 'e4', source: 'n4', target: 'n5' },
  ],
}

/**
 * Diamond topology (tests multi-path BFS):
 *   n1 → n2 → n4
 *   n1 → n3 → n4
 */
export const DIAMOND_TOPOLOGY = {
  nodes: [
    { id: 'n1', name: 'Source', type: 'data_source', subsystem: 'ingestion', version_count: 1, current_version: { version_number: 1, status: 'deployed' }, ports: { inputs: [], outputs: [] } },
    { id: 'n2', name: 'Path A', type: 'feature_engine', subsystem: 'feature', version_count: 1, current_version: { version_number: 1, status: 'deployed' }, ports: { inputs: [], outputs: [] } },
    { id: 'n3', name: 'Path B', type: 'feature_engine', subsystem: 'feature', version_count: 1, current_version: { version_number: 1, status: 'deployed' }, ports: { inputs: [], outputs: [] } },
    { id: 'n4', name: 'Merge', type: 'signal_generator', subsystem: 'signal', version_count: 1, current_version: { version_number: 1, status: 'deployed' }, ports: { inputs: [], outputs: [] } },
  ],
  edges: [
    { id: 'e1', source: 'n1', target: 'n2' },
    { id: 'e2', source: 'n1', target: 'n3' },
    { id: 'e3', source: 'n2', target: 'n4' },
    { id: 'e4', source: 'n3', target: 'n4' },
  ],
}

/**
 * Empty topology (edge case)
 */
export const EMPTY_TOPOLOGY = {
  nodes: [],
  edges: [],
}
