-- Migration: Pipeline Introspection Layer — meta schema
-- Date: 2026-03-01
--
-- =============================================================================
-- FK DEPENDENCY CHAIN (creation order):
-- =============================================================================
-- 1.  meta schema
-- 2.  meta.metric_registry (no FKs)
-- 3.  meta.parameter_registry (no FKs)
-- 4.  meta.node (no FKs)
-- 5.  meta.port (FK → node)
-- 6.  meta.edge (FK → port)
-- 7.  meta.experiment (FK → runs)
-- 8.  meta.node_version (FK → node, node_version, runs)
-- 9.  meta.experiment_version (FK → experiment, node_version)
-- 10. meta.pipeline (no FKs)
-- 11. meta.pipeline_node (FK → pipeline, node)
-- 12. meta.composition (FK → experiment)
-- 13. meta.composition_version (FK → composition, node, node_version)
-- 14. meta.snapshot (no FKs)
-- 15. meta.version_diff (FK → node_version, node)
-- 16. meta.composition_edge (FK → composition, node_version)
-- 17. meta.introspection_event (no FKs)
-- 18. meta.correction (no FKs)
-- 19. meta.episode (no FKs)
-- 20. meta.sweep_summary (FK → experiment, node, node_version)
-- 21. Views: research_velocity, composition_pareto
-- 22. Triggers: updated_at, cycle prevention, validation gate, promoted metrics, NOTIFY
-- =============================================================================

SELECT pg_advisory_lock(2026030101);

-- =============================================================================
-- 1. CREATE SCHEMA
-- =============================================================================
CREATE SCHEMA IF NOT EXISTS meta;
COMMENT ON SCHEMA meta IS 'Pipeline introspection layer — self-describing, version-composed, episodic-memory-enabled metadata for MCAS.';

-- Helper: generate UUIDs
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- =============================================================================
-- 2. meta.metric_registry — canonical metric key definitions
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.metric_registry (
    metric_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    data_type TEXT NOT NULL CHECK (data_type IN ('float', 'integer', 'boolean', 'text', 'json')),
    unit TEXT,
    description TEXT,
    aliases TEXT[] DEFAULT '{}'
);
COMMENT ON TABLE meta.metric_registry IS 'Canonical definitions for all metric keys used in MCAS. Incoming JSONB keys validated against this.';

-- Pre-populate with known metrics
INSERT INTO meta.metric_registry (metric_key, display_name, data_type, unit, description, aliases)
VALUES
    ('agg_pf', 'Aggregate Profit Factor', 'float', 'ratio', 'Aggregate PF across all trading days', ARRAY['aggPF', 'aggregate_pf', 'profit_factor']),
    ('avg_daily_pf', 'Avg Daily Profit Factor', 'float', 'ratio', 'Mean PF computed per trading day then averaged — the primary success gate', ARRAY['avg_daily_profit_factor']),
    ('net_pnl', 'Net PnL', 'float', 'USD', 'Net profit/loss after fees and slippage', ARRAY['pnl_net', 'total_pnl']),
    ('win_rate', 'Win Rate', 'float', 'pct', 'Percentage of trades that are profitable', ARRAY['winrate', 'win_pct']),
    ('max_drawdown', 'Max Drawdown', 'float', 'pct', 'Maximum peak-to-trough drawdown', ARRAY['max_dd', 'drawdown']),
    ('trade_count', 'Trade Count', 'integer', 'trades', 'Total number of trades in the evaluation period', ARRAY['n_trades', 'num_trades']),
    ('sharpe_ratio', 'Sharpe Ratio', 'float', 'ratio', 'Risk-adjusted return ratio', ARRAY['sharpe', 'sr']),
    ('sortino_ratio', 'Sortino Ratio', 'float', 'ratio', 'Downside risk-adjusted return', ARRAY['sortino']),
    ('r_expectancy', 'R-Expectancy', 'float', 'R', 'Expected value per trade in R-multiples', ARRAY['expectancy', 'avg_r']),
    ('avg_bars_held', 'Avg Bars Held', 'float', 'bars', 'Average number of bars a position is held', ARRAY['avg_holding_period']),
    ('ff_rate', 'Fail-Fast Rate', 'float', 'pct', 'Percentage of trades that hit fail-fast exit', ARRAY['fail_fast_rate']),
    ('tp1_rate', 'TP1 Hit Rate', 'float', 'pct', 'Percentage of trades reaching first take-profit', ARRAY['tp1_hit_rate']),
    ('stop_rate', 'Stop-Loss Rate', 'float', 'pct', 'Percentage of trades hitting stop-loss', ARRAY['sl_rate', 'stop_loss_rate']),
    ('time_stop_rate', 'Time-Stop Rate', 'float', 'pct', 'Percentage of trades hitting time-based exit', ARRAY['ts_rate']),
    ('dsr', 'Deflated Sharpe Ratio', 'float', 'ratio', 'Sharpe ratio corrected for multiple testing (Harvey et al. 2016)', ARRAY['deflated_sharpe'])
ON CONFLICT (metric_key) DO NOTHING;

-- =============================================================================
-- 3. meta.parameter_registry — canonical parameter key definitions
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.parameter_registry (
    param_key TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    data_type TEXT NOT NULL CHECK (data_type IN ('float', 'integer', 'boolean', 'text', 'json')),
    unit TEXT,
    description TEXT,
    aliases TEXT[] DEFAULT '{}'
);
COMMENT ON TABLE meta.parameter_registry IS 'Canonical definitions for all parameter keys. Incoming JSONB keys validated against this.';

INSERT INTO meta.parameter_registry (param_key, display_name, data_type, unit, description, aliases)
VALUES
    ('volume_z_threshold', 'Volume Z Threshold', 'float', 'sigma', 'Minimum volume z-score for signal qualification', ARRAY['vol_z', 'min_vol_z']),
    ('bb_width_pctile', 'BB Width Percentile', 'float', 'pctile', 'Bollinger Band width percentile threshold', ARRAY['bb_width']),
    ('tp1_r', 'TP1 R-Multiple', 'float', 'R', 'First take-profit target in R-multiples', ARRAY['tp1']),
    ('sl_r', 'Stop-Loss R-Multiple', 'float', 'R', 'Stop-loss distance in R-multiples', ARRAY['stop_loss', 'sl']),
    ('trail_activation_r', 'Trail Activation R', 'float', 'R', 'R-multiple at which trailing stop activates', ARRAY['trail_start']),
    ('trail_offset_r', 'Trail Offset R', 'float', 'R', 'Trailing stop distance behind price in R', ARRAY['trail_distance']),
    ('time_stop_bars', 'Time-Stop Bars', 'integer', 'bars', 'Maximum bars before time-based exit', ARRAY['max_bars', 'time_stop']),
    ('fail_fast_bars', 'Fail-Fast Bars', 'integer', 'bars', 'Bars before fail-fast exit triggers', ARRAY['ff_bars']),
    ('fail_fast_threshold', 'Fail-Fast Threshold', 'float', 'R', 'Minimum progress required by fail-fast check', ARRAY['ff_threshold']),
    ('slippage_bps', 'Slippage (bps)', 'float', 'bps', 'Assumed slippage in basis points', ARRAY['slippage']),
    ('fee_bps', 'Fee (bps)', 'float', 'bps', 'Trading fee in basis points', ARRAY['taker_fee']),
    ('lookback_days', 'Lookback Days', 'integer', 'days', 'Historical data lookback period', ARRAY['lookback']),
    ('min_atr', 'Minimum ATR', 'float', 'price', 'Minimum ATR threshold for signal qualification', ARRAY[]),
    ('reversion_boost', 'Reversion Boost', 'float', 'multiplier', 'Score multiplier for mean-reversion conditions', ARRAY[])
ON CONFLICT (param_key) DO NOTHING;

-- =============================================================================
-- 4. meta.node — atomic pipeline unit, single bounded responsibility
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.node (
    node_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    node_type TEXT NOT NULL CHECK (node_type IN (
        'data_source', 'feature_engine', 'signal_generator', 'signal_filter',
        'strategy', 'parameter', 'backtest_engine', 'metric_aggregator',
        'risk_manager', 'execution_engine', 'composition', 'custom'
    )),
    subsystem TEXT NOT NULL CHECK (subsystem IN (
        'ingestion', 'feature', 'signal', 'filtering', 'strategy',
        'parameter', 'backtest', 'metrics', 'risk', 'execution', 'meta'
    )),
    description TEXT,
    source_file TEXT,
    owner TEXT,
    config JSONB DEFAULT '{}',
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE meta.node IS 'Atomic pipeline unit with single bounded responsibility. Never hard-deleted — retired_at marks decommission.';
COMMENT ON COLUMN meta.node.owner IS 'Agent subgraph write boundary — which agent owns this node.';
COMMENT ON COLUMN meta.node.source_file IS 'Path to source code file implementing this node.';

CREATE INDEX IF NOT EXISTS idx_meta_node_type ON meta.node(node_type);
CREATE INDEX IF NOT EXISTS idx_meta_node_subsystem ON meta.node(subsystem);
CREATE INDEX IF NOT EXISTS idx_meta_node_active ON meta.node(node_id) WHERE retired_at IS NULL;

-- =============================================================================
-- 5. meta.port — typed I/O contract per node
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.port (
    port_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id UUID NOT NULL REFERENCES meta.node(node_id),
    name TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('input', 'output')),
    data_type TEXT NOT NULL,
    schema_spec JSONB DEFAULT '{}',
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(node_id, name, direction)
);
COMMENT ON TABLE meta.port IS 'Typed I/O contract per node. Ports enforce composability — if ports match, nodes can connect.';
COMMENT ON COLUMN meta.port.schema_spec IS 'Constraint propagation spec: e.g. {"min_vol_z": {"gte": 3.5}} for composition validation.';

CREATE INDEX IF NOT EXISTS idx_meta_port_node ON meta.port(node_id);

-- =============================================================================
-- 6. meta.edge — directed DAG connection between output port and input port
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.edge (
    edge_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_port_id UUID NOT NULL REFERENCES meta.port(port_id),
    target_port_id UUID NOT NULL REFERENCES meta.port(port_id),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(source_port_id, target_port_id)
);
COMMENT ON TABLE meta.edge IS 'Directed DAG connection. Source must be output port, target must be input port. Cycles are rejected by trigger.';

CREATE INDEX IF NOT EXISTS idx_meta_edge_source ON meta.edge(source_port_id);
CREATE INDEX IF NOT EXISTS idx_meta_edge_target ON meta.edge(target_port_id);

-- =============================================================================
-- 7. meta.experiment — context wrapper grouping related version changes
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.experiment (
    experiment_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_code TEXT UNIQUE,
    name TEXT NOT NULL,
    hypothesis TEXT,
    status TEXT NOT NULL DEFAULT 'planned' CHECK (status IN ('planned', 'running', 'completed', 'abandoned')),
    outcome_class TEXT CHECK (outcome_class IS NULL OR outcome_class IN (
        'breakthrough', 'incremental', 'null', 'dead_end', 'invalidated_prior'
    )),
    failure_mode TEXT CHECK (failure_mode IS NULL OR failure_mode IN (
        'overfitting', 'regime_dependent', 'cost_model_failure',
        'insufficient_sample', 'confounded', 'implementation_bug'
    )),
    key_insight TEXT,
    methodological_notes JSONB DEFAULT '{}',
    baseline_run_id UUID REFERENCES runs(id),
    conclusion TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE meta.experiment IS 'Context wrapper grouping related version changes. Tracks hypothesis → outcome lifecycle.';
COMMENT ON COLUMN meta.experiment.experiment_code IS 'Structured code: {strategy}:{phase}:{sequence}:{slug} e.g. U1:phase2:047:volume_z_sweep';
COMMENT ON COLUMN meta.experiment.outcome_class IS 'Classification of experiment result: breakthrough, incremental, null, dead_end, invalidated_prior';
COMMENT ON COLUMN meta.experiment.failure_mode IS 'Why the experiment failed (if it did): overfitting, regime_dependent, cost_model_failure, etc.';

CREATE INDEX IF NOT EXISTS idx_meta_experiment_status ON meta.experiment(status);
CREATE INDEX IF NOT EXISTS idx_meta_experiment_code ON meta.experiment(experiment_code);

-- =============================================================================
-- 8. meta.node_version — THE HEART. The cutlery drawer.
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.node_version (
    version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_id UUID NOT NULL REFERENCES meta.node(node_id),
    version_number INTEGER NOT NULL,
    parent_version UUID REFERENCES meta.node_version(version_id),
    derived_from JSONB,
    status TEXT NOT NULL DEFAULT 'tested' CHECK (status IN (
        'tested', 'promising', 'deployed', 'retired', 'baseline'
    )),
    parameters JSONB DEFAULT '{}',
    metrics JSONB DEFAULT '{}',
    -- Promoted core metrics (populated via trigger/write-through from metrics JSONB)
    agg_pf FLOAT,
    net_pnl FLOAT,
    win_rate FLOAT,
    max_drawdown FLOAT,
    trade_count INTEGER,
    -- Context
    metrics_context JSONB NOT NULL DEFAULT '{}',
    source_hash TEXT,
    significance FLOAT DEFAULT 0,
    superseded_by UUID REFERENCES meta.node_version(version_id),
    metrics_run_id UUID REFERENCES runs(id),
    change_reason TEXT,
    -- Causal event chaining (WP-4): link version to its motivating event
    caused_by TEXT DEFAULT NULL,
    caused_by_type TEXT DEFAULT NULL CHECK (caused_by_type IS NULL OR caused_by_type IN (
        'commit', 'experiment', 'manual', 'config_change'
    )),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(node_id, version_number)
);
COMMENT ON TABLE meta.node_version IS 'THE HEART. Every meaningful state a node has ever held. The cutlery drawer — every version simultaneously accessible.';
COMMENT ON COLUMN meta.node_version.caused_by IS 'Reference to commit hash, experiment ID, or event that motivated this version.';
COMMENT ON COLUMN meta.node_version.caused_by_type IS 'Classification: commit, experiment, manual, config_change.';
COMMENT ON COLUMN meta.node_version.derived_from IS 'Multi-parent ancestry for compositions: {"tp1_node": "version_7_id", "stop_node": "version_3_id"}. parent_version for simple linear lineage.';
COMMENT ON COLUMN meta.node_version.metrics_context IS 'Mandatory context: date_range, symbols_tested, timeframe, trade_count, slippage_model, data_source.';
COMMENT ON COLUMN meta.node_version.source_hash IS 'Git SHA or content hash of source code that produced this version. Detects code changes that dont alter parameters.';
COMMENT ON COLUMN meta.node_version.significance IS 'Computed score = |delta_metrics_from_parent| x downstream_composition_count x status_weight. For timeline scrubber.';
COMMENT ON COLUMN meta.node_version.superseded_by IS 'When another version is strictly better on all metrics in same context. Enables Pareto frontier queries.';

CREATE INDEX IF NOT EXISTS idx_meta_nv_node ON meta.node_version(node_id, version_number DESC);
CREATE INDEX IF NOT EXISTS idx_meta_nv_status ON meta.node_version(status);
CREATE INDEX IF NOT EXISTS idx_meta_nv_agg_pf ON meta.node_version(agg_pf) WHERE agg_pf IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_meta_nv_net_pnl ON meta.node_version(net_pnl) WHERE net_pnl IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_meta_nv_win_rate ON meta.node_version(win_rate) WHERE win_rate IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_meta_nv_max_dd ON meta.node_version(max_drawdown) WHERE max_drawdown IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_meta_nv_trade_count ON meta.node_version(trade_count) WHERE trade_count IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_meta_nv_parent ON meta.node_version(parent_version);
CREATE INDEX IF NOT EXISTS idx_meta_nv_created ON meta.node_version(created_at DESC);

-- =============================================================================
-- 9. meta.experiment_version — junction: experiment ↔ node_version
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.experiment_version (
    experiment_id UUID NOT NULL REFERENCES meta.experiment(experiment_id) ON DELETE CASCADE,
    version_id UUID NOT NULL REFERENCES meta.node_version(version_id) ON DELETE CASCADE,
    added_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (experiment_id, version_id)
);
COMMENT ON TABLE meta.experiment_version IS 'Junction table linking experiments to the node versions they produced.';

CREATE INDEX IF NOT EXISTS idx_meta_ev_version ON meta.experiment_version(version_id);

-- =============================================================================
-- 10. meta.pipeline — named DAG configuration / reusable template
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.pipeline (
    pipeline_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    config JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE meta.pipeline IS 'Named DAG configuration / reusable template. The skeleton that compositions populate with specific versions.';

-- =============================================================================
-- 11. meta.pipeline_node — junction: pipeline ↔ node (with ordering)
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.pipeline_node (
    pipeline_id UUID NOT NULL REFERENCES meta.pipeline(pipeline_id) ON DELETE CASCADE,
    node_id UUID NOT NULL REFERENCES meta.node(node_id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pipeline_id, node_id)
);
COMMENT ON TABLE meta.pipeline_node IS 'Junction linking pipelines to their constituent nodes with display ordering.';

-- =============================================================================
-- 12. meta.composition — cherry-picked assembly of node versions
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.composition (
    composition_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT,
    description TEXT,
    node_versions JSONB NOT NULL DEFAULT '{}',
    config_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN (
        'proposed', 'backtested', 'validated', 'deployed', 'pruned_early', 'dominated'
    )),
    source TEXT CHECK (source IS NULL OR source IN ('manual', 'sweep_result', 'auto_composed')),
    experiment_id UUID REFERENCES meta.experiment(experiment_id),
    validation_gate JSONB DEFAULT '{}',
    statistical_context JSONB DEFAULT '{}',
    assembled_at TIMESTAMPTZ,
    integrity_hash TEXT,
    backtest_version_pin JSONB,
    metrics JSONB DEFAULT '{}',
    -- Promoted metrics
    agg_pf FLOAT,
    net_pnl FLOAT,
    win_rate FLOAT,
    max_drawdown FLOAT,
    trade_count INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    -- NOTE: Monthly range partitioning on created_at is a future optimisation if scale warrants it.
);
COMMENT ON TABLE meta.composition IS 'Cherry-picked assembly of node versions. Content-addressable via config_hash (SHA256 of sorted node_versions).';
COMMENT ON COLUMN meta.composition.config_hash IS 'SHA256(sorted(node_versions))[:16]. Content-addressable dedup + fast lookup.';
COMMENT ON COLUMN meta.composition.validation_gate IS 'Structured: walk_forward_pass, holdout_pass, dsr_positive, slippage_1_5x_pass, n_trials_corrected booleans.';
COMMENT ON COLUMN meta.composition.statistical_context IS 'Required when source=sweep_result: n_compositions_tested, expected_max_null, deflated_sharpe_ratio.';
COMMENT ON COLUMN meta.composition.assembled_at IS 'Temporal consistency: all included node_versions must predate this.';
COMMENT ON COLUMN meta.composition.integrity_hash IS 'Hash of all version_ids + source_hashes + parameters at backtest time. Recompute before recording results.';
COMMENT ON COLUMN meta.composition.backtest_version_pin IS 'Exact version_ids used when backtest began. Results attributed to pinned versions, not current.';

CREATE INDEX IF NOT EXISTS idx_meta_comp_status ON meta.composition(status);
CREATE INDEX IF NOT EXISTS idx_meta_comp_experiment ON meta.composition(experiment_id);
CREATE INDEX IF NOT EXISTS idx_meta_comp_agg_pf ON meta.composition(agg_pf) WHERE agg_pf IS NOT NULL;

-- =============================================================================
-- 13. meta.composition_version — junction: composition ↔ node versions
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.composition_version (
    composition_id UUID NOT NULL REFERENCES meta.composition(composition_id) ON DELETE CASCADE,
    node_id UUID NOT NULL REFERENCES meta.node(node_id),
    version_id UUID NOT NULL REFERENCES meta.node_version(version_id),
    PRIMARY KEY (composition_id, node_id)
);
COMMENT ON TABLE meta.composition_version IS 'Junction for reverse lookups: find all compositions using a specific version.';

CREATE INDEX IF NOT EXISTS idx_meta_cv_version ON meta.composition_version(version_id);

-- =============================================================================
-- 14. meta.snapshot — frozen full pipeline state at a moment
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.snapshot (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    active_versions JSONB NOT NULL,
    topology JSONB NOT NULL,
    trigger_event TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (pg_column_size(topology) < 1048576)
);
COMMENT ON TABLE meta.snapshot IS 'Frozen full pipeline state at a moment. active_versions is the canonical version map; topology is the structural DAG (nodes+edges).';
COMMENT ON COLUMN meta.snapshot.active_versions IS 'Canonical: {node_id: version_id} map of which version was deployed at snapshot time.';
COMMENT ON COLUMN meta.snapshot.topology IS 'Structural DAG: nodes and edges, excluding version selections. Separate from active_versions for clarity.';

-- =============================================================================
-- 15. meta.version_diff — materialised diffs between node versions
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.version_diff (
    diff_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    version_a UUID NOT NULL REFERENCES meta.node_version(version_id),
    version_b UUID NOT NULL REFERENCES meta.node_version(version_id),
    node_id UUID NOT NULL REFERENCES meta.node(node_id),
    changed_params JSONB NOT NULL DEFAULT '{}',
    unchanged_params JSONB NOT NULL DEFAULT '{}',
    metric_delta JSONB NOT NULL DEFAULT '{}',
    context_match BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(version_a, version_b)
);
COMMENT ON TABLE meta.version_diff IS 'Materialised diffs between node versions. When context_match=true and one param changed, you have isolated causal observation.';

CREATE INDEX IF NOT EXISTS idx_meta_vdiff_node ON meta.version_diff(node_id);
-- Index on single-param changes for causal analysis
CREATE INDEX IF NOT EXISTS idx_meta_vdiff_single_param ON meta.version_diff(node_id, context_match)
    WHERE context_match = TRUE;

-- =============================================================================
-- 16. meta.composition_edge — single-substitution graph over compositions
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.composition_edge (
    comp_edge_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_comp_id UUID NOT NULL REFERENCES meta.composition(composition_id),
    child_comp_id UUID NOT NULL REFERENCES meta.composition(composition_id),
    diff_node_id UUID NOT NULL REFERENCES meta.node(node_id),
    parent_version UUID NOT NULL REFERENCES meta.node_version(version_id),
    child_version UUID NOT NULL REFERENCES meta.node_version(version_id),
    metric_delta JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(parent_comp_id, child_comp_id)
);
COMMENT ON TABLE meta.composition_edge IS 'Single-substitution graph: walking the composition space one version change at a time. "One step away from a known winner."';

CREATE INDEX IF NOT EXISTS idx_meta_ce_parent ON meta.composition_edge(parent_comp_id);
CREATE INDEX IF NOT EXISTS idx_meta_ce_child ON meta.composition_edge(child_comp_id);

-- =============================================================================
-- 17. meta.introspection_event — operational event log
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.introspection_event (
    event_id BIGSERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id UUID,
    actor TEXT,
    payload JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE meta.introspection_event IS 'Operational event log for all meta schema mutations. Audit trail for pipeline introspection activity.';

CREATE INDEX IF NOT EXISTS idx_meta_ie_type ON meta.introspection_event(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_meta_ie_entity ON meta.introspection_event(entity_type, entity_id);

-- =============================================================================
-- 18. meta.correction — negative transfer learning / lessons learned
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.correction (
    correction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger_event TEXT,
    rule TEXT NOT NULL,
    scope TEXT[] DEFAULT '{}',
    related_experiments UUID[] DEFAULT '{}',
    violation_count INTEGER NOT NULL DEFAULT 0,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB DEFAULT '{}'
);
COMMENT ON TABLE meta.correction IS 'Negative transfer learning: hard-won lessons. Each entry is a rule derived from a past mistake. Maps to CLAUDE.md §5 Corrections Log.';

CREATE INDEX IF NOT EXISTS idx_meta_correction_active ON meta.correction(active) WHERE active = TRUE;

-- =============================================================================
-- 19. meta.episode — session-level cognitive context
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.episode (
    episode_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_date DATE NOT NULL DEFAULT CURRENT_DATE,
    agent_id TEXT,
    experiment_ids UUID[] DEFAULT '{}',
    narrative TEXT,
    decisions JSONB DEFAULT '{}',
    surprises JSONB DEFAULT '{}',
    dead_ends JSONB DEFAULT '{}',
    next_actions TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE meta.episode IS 'Session-level cognitive context. The structured equivalent of "what was I doing and why did I stop."';

CREATE INDEX IF NOT EXISTS idx_meta_episode_date ON meta.episode(session_date DESC);

-- =============================================================================
-- 20. meta.sweep_summary — lightweight record per sweep run
-- =============================================================================
CREATE TABLE IF NOT EXISTS meta.sweep_summary (
    sweep_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    experiment_id UUID REFERENCES meta.experiment(experiment_id),
    node_id UUID NOT NULL REFERENCES meta.node(node_id),
    swept_parameter TEXT NOT NULL,
    value_range JSONB NOT NULL,
    n_configs INTEGER NOT NULL,
    n_above_threshold INTEGER DEFAULT 0,
    best_version_id UUID REFERENCES meta.node_version(version_id),
    summary_stats JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
COMMENT ON TABLE meta.sweep_summary IS 'Lightweight record per sweep. Prevents composition table from being sole record of sweep activity. Enables GC of dominated compositions while preserving aggregate findings.';

CREATE INDEX IF NOT EXISTS idx_meta_sweep_node ON meta.sweep_summary(node_id);
CREATE INDEX IF NOT EXISTS idx_meta_sweep_experiment ON meta.sweep_summary(experiment_id);

-- =============================================================================
-- 21. VIEWS
-- =============================================================================

-- 21a. meta.research_velocity — weekly research cadence metrics
CREATE OR REPLACE VIEW meta.research_velocity AS
SELECT
    date_trunc('week', e.created_at)::DATE AS week_start,
    COALESCE(
        (SELECT DISTINCT unnest(string_to_array(split_part(e.experiment_code, ':', 1), ','))
         WHERE e.experiment_code IS NOT NULL),
        'unknown'
    ) AS strategy,
    COUNT(*) AS experiments_started,
    COUNT(*) FILTER (WHERE e.outcome_class = 'breakthrough') AS breakthroughs,
    COUNT(*) FILTER (WHERE e.outcome_class = 'dead_end') AS dead_ends,
    COUNT(*) FILTER (WHERE e.outcome_class = 'null') AS null_results,
    COUNT(*) FILTER (WHERE e.status = 'completed') AS completed,
    COUNT(*) FILTER (WHERE e.status = 'abandoned') AS abandoned,
    AVG(EXTRACT(EPOCH FROM (e.updated_at - e.created_at)) / 3600)
        FILTER (WHERE e.status IN ('completed', 'abandoned')) AS avg_hours_to_conclusion
FROM meta.experiment e
GROUP BY date_trunc('week', e.created_at)::DATE,
         COALESCE(
             (SELECT DISTINCT unnest(string_to_array(split_part(e.experiment_code, ':', 1), ','))
              WHERE e.experiment_code IS NOT NULL),
             'unknown'
         );
COMMENT ON VIEW meta.research_velocity IS 'Weekly research cadence: experiments started, breakthroughs, dead ends, avg hours to conclusion. Grouped by week and strategy.';

-- 21b. meta.composition_pareto — Pareto frontier materialized view
-- Multi-objective frontier across agg_pf, max_drawdown, trade_count
CREATE MATERIALIZED VIEW IF NOT EXISTS meta.composition_pareto AS
SELECT c.*
FROM meta.composition c
WHERE c.status NOT IN ('pruned_early', 'dominated')
  AND c.agg_pf IS NOT NULL
  AND c.max_drawdown IS NOT NULL
  AND c.trade_count IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM meta.composition d
      WHERE d.composition_id != c.composition_id
        AND d.status NOT IN ('pruned_early', 'dominated')
        AND d.agg_pf IS NOT NULL
        AND d.max_drawdown IS NOT NULL
        AND d.trade_count IS NOT NULL
        AND d.agg_pf >= c.agg_pf
        AND d.max_drawdown <= c.max_drawdown
        AND d.trade_count >= c.trade_count
        AND (d.agg_pf > c.agg_pf OR d.max_drawdown < c.max_drawdown OR d.trade_count > c.trade_count)
  )
WITH NO DATA;
COMMENT ON MATERIALIZED VIEW meta.composition_pareto IS 'Pareto frontier: compositions not dominated on all three axes (agg_pf, max_drawdown, trade_count).';

-- =============================================================================
-- 22. TRIGGERS & FUNCTIONS
-- =============================================================================

-- 22a. Generic updated_at trigger function
CREATE OR REPLACE FUNCTION meta.set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply updated_at triggers
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOR tbl IN SELECT unnest(ARRAY['node', 'experiment', 'pipeline', 'composition'])
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS trg_%s_updated_at ON meta.%I; '
            'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON meta.%I '
            'FOR EACH ROW EXECUTE FUNCTION meta.set_updated_at();',
            tbl, tbl, tbl, tbl
        );
    END LOOP;
END;
$$;

-- 22b. Edge direction enforcement: source port must be 'output', target must be 'input'
CREATE OR REPLACE FUNCTION meta.enforce_edge_direction()
RETURNS TRIGGER AS $$
DECLARE
    src_dir TEXT;
    tgt_dir TEXT;
BEGIN
    SELECT direction INTO src_dir FROM meta.port WHERE port_id = NEW.source_port_id;
    SELECT direction INTO tgt_dir FROM meta.port WHERE port_id = NEW.target_port_id;

    IF src_dir != 'output' THEN
        RAISE EXCEPTION 'Source port % is not an output port (direction: %)', NEW.source_port_id, src_dir;
    END IF;
    IF tgt_dir != 'input' THEN
        RAISE EXCEPTION 'Target port % is not an input port (direction: %)', NEW.target_port_id, tgt_dir;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_edge_direction ON meta.edge;
CREATE TRIGGER trg_edge_direction
    BEFORE INSERT OR UPDATE ON meta.edge
    FOR EACH ROW EXECUTE FUNCTION meta.enforce_edge_direction();

-- 22c. Cycle prevention trigger for edges
CREATE OR REPLACE FUNCTION meta.prevent_edge_cycle()
RETURNS TRIGGER AS $$
DECLARE
    src_node UUID;
    tgt_node UUID;
BEGIN
    -- Get the nodes for the source and target ports
    SELECT node_id INTO src_node FROM meta.port WHERE port_id = NEW.source_port_id;
    SELECT node_id INTO tgt_node FROM meta.port WHERE port_id = NEW.target_port_id;

    -- Self-loop check
    IF src_node = tgt_node THEN
        RAISE EXCEPTION 'Self-loop detected: source and target ports belong to the same node %', src_node;
    END IF;

    -- Check if adding this edge creates a cycle using recursive CTE
    -- Walk from the target node forward; if we can reach the source node, it's a cycle
    IF EXISTS (
        WITH RECURSIVE reachable AS (
            -- Start from the target node's output ports
            SELECT DISTINCT p2.node_id
            FROM meta.port p1
            JOIN meta.edge e ON e.source_port_id = p1.port_id
            JOIN meta.port p2 ON p2.port_id = e.target_port_id
            WHERE p1.node_id = tgt_node

            UNION

            SELECT DISTINCT p2.node_id
            FROM reachable r
            JOIN meta.port p1 ON p1.node_id = r.node_id
            JOIN meta.edge e ON e.source_port_id = p1.port_id
            JOIN meta.port p2 ON p2.port_id = e.target_port_id
        )
        SELECT 1 FROM reachable WHERE node_id = src_node
    ) THEN
        RAISE EXCEPTION 'Cycle detected: adding edge from node % to node % would create a cycle', src_node, tgt_node;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_edge_no_cycle ON meta.edge;
CREATE TRIGGER trg_edge_no_cycle
    BEFORE INSERT OR UPDATE ON meta.edge
    FOR EACH ROW EXECUTE FUNCTION meta.prevent_edge_cycle();

-- 22d. Promoted metrics write-through: copy from metrics JSONB to real columns
CREATE OR REPLACE FUNCTION meta.promote_version_metrics()
RETURNS TRIGGER AS $$
BEGIN
    -- Only overwrite promoted columns if they're not explicitly set and metrics JSONB has the key
    IF NEW.agg_pf IS NULL AND NEW.metrics ? 'agg_pf' THEN
        NEW.agg_pf := (NEW.metrics->>'agg_pf')::FLOAT;
    END IF;
    IF NEW.net_pnl IS NULL AND NEW.metrics ? 'net_pnl' THEN
        NEW.net_pnl := (NEW.metrics->>'net_pnl')::FLOAT;
    END IF;
    IF NEW.win_rate IS NULL AND NEW.metrics ? 'win_rate' THEN
        NEW.win_rate := (NEW.metrics->>'win_rate')::FLOAT;
    END IF;
    IF NEW.max_drawdown IS NULL AND NEW.metrics ? 'max_drawdown' THEN
        NEW.max_drawdown := (NEW.metrics->>'max_drawdown')::FLOAT;
    END IF;
    IF NEW.trade_count IS NULL AND NEW.metrics ? 'trade_count' THEN
        NEW.trade_count := (NEW.metrics->>'trade_count')::INTEGER;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_nv_promote_metrics ON meta.node_version;
CREATE TRIGGER trg_nv_promote_metrics
    BEFORE INSERT OR UPDATE ON meta.node_version
    FOR EACH ROW EXECUTE FUNCTION meta.promote_version_metrics();

-- Same for compositions
CREATE OR REPLACE FUNCTION meta.promote_composition_metrics()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.agg_pf IS NULL AND NEW.metrics ? 'agg_pf' THEN
        NEW.agg_pf := (NEW.metrics->>'agg_pf')::FLOAT;
    END IF;
    IF NEW.net_pnl IS NULL AND NEW.metrics ? 'net_pnl' THEN
        NEW.net_pnl := (NEW.metrics->>'net_pnl')::FLOAT;
    END IF;
    IF NEW.win_rate IS NULL AND NEW.metrics ? 'win_rate' THEN
        NEW.win_rate := (NEW.metrics->>'win_rate')::FLOAT;
    END IF;
    IF NEW.max_drawdown IS NULL AND NEW.metrics ? 'max_drawdown' THEN
        NEW.max_drawdown := (NEW.metrics->>'max_drawdown')::FLOAT;
    END IF;
    IF NEW.trade_count IS NULL AND NEW.metrics ? 'trade_count' THEN
        NEW.trade_count := (NEW.metrics->>'trade_count')::INTEGER;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_comp_promote_metrics ON meta.composition;
CREATE TRIGGER trg_comp_promote_metrics
    BEFORE INSERT OR UPDATE ON meta.composition
    FOR EACH ROW EXECUTE FUNCTION meta.promote_composition_metrics();

-- 22e. Composition validation gate enforcement:
-- Status cannot advance to 'validated' unless all validation_gate fields are true
CREATE OR REPLACE FUNCTION meta.enforce_validation_gate()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status = 'validated' AND (
        NOT (NEW.validation_gate->>'walk_forward_pass')::BOOLEAN IS TRUE
        OR NOT (NEW.validation_gate->>'holdout_pass')::BOOLEAN IS TRUE
        OR NOT (NEW.validation_gate->>'dsr_positive')::BOOLEAN IS TRUE
        OR NOT (NEW.validation_gate->>'slippage_1_5x_pass')::BOOLEAN IS TRUE
    ) THEN
        RAISE EXCEPTION 'Cannot set composition status to validated: validation_gate fields must all be true. Got: %', NEW.validation_gate;
    END IF;

    -- Enforce statistical_context when source is sweep_result and promoting to validated
    IF NEW.status = 'validated' AND NEW.source = 'sweep_result' THEN
        IF NOT (NEW.statistical_context ? 'n_compositions_tested')
           OR NOT (NEW.statistical_context ? 'deflated_sharpe_ratio') THEN
            RAISE EXCEPTION 'Sweep-sourced compositions require statistical_context with n_compositions_tested and deflated_sharpe_ratio when validating.';
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_comp_validation_gate ON meta.composition;
CREATE TRIGGER trg_comp_validation_gate
    BEFORE INSERT OR UPDATE ON meta.composition
    FOR EACH ROW EXECUTE FUNCTION meta.enforce_validation_gate();

-- 22f. NOTIFY channels for agent coordination
CREATE OR REPLACE FUNCTION meta.notify_version_created()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify('meta_version_created', json_build_object(
        'version_id', NEW.version_id,
        'node_id', NEW.node_id,
        'version_number', NEW.version_number,
        'status', NEW.status
    )::TEXT);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_version_created ON meta.node_version;
CREATE TRIGGER trg_notify_version_created
    AFTER INSERT ON meta.node_version
    FOR EACH ROW EXECUTE FUNCTION meta.notify_version_created();

CREATE OR REPLACE FUNCTION meta.notify_experiment_status()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM NEW.status THEN
        PERFORM pg_notify('meta_experiment_status', json_build_object(
            'experiment_id', NEW.experiment_id,
            'old_status', OLD.status,
            'new_status', NEW.status,
            'name', NEW.name
        )::TEXT);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_notify_experiment_status ON meta.experiment;
CREATE TRIGGER trg_notify_experiment_status
    AFTER UPDATE ON meta.experiment
    FOR EACH ROW EXECUTE FUNCTION meta.notify_experiment_status();

-- =============================================================================
-- SCHEMA VERSION TRACKING
-- =============================================================================
INSERT INTO schema_versions (filename, checksum)
VALUES (
    '20260301_add_meta_introspection_schema.sql',
    'meta_introspection_schema_v1'
)
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026030101);
