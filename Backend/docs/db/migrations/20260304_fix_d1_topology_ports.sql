-- Migration: Fix D1 topology — ports, edges, pipeline_node, versions, biography
-- Date: 2026-03-04
-- Advisory lock: 2026030402
--
-- The original 20260303_add_d1_topology.sql inserted nodes successfully but
-- the DO $$ block failed due to column name mismatches:
--   - node_file_mapping: used 'description' (doesn't exist), should be 'mapping_type'
--   - node_version: used 'version_tag/config_snapshot/change_summary' (don't exist),
--     should be 'version_number/parameters/change_reason'
--   - version_event: used 'node_version_id/summary/detail/created_ts' (don't exist),
--     should be 'version_id/event_detail/created_at'
-- Since the entire DO block was transactional, ports/edges/pipeline_node also rolled back.
-- This migration re-creates everything using the correct column names.

DO $$ BEGIN
    PERFORM pg_advisory_lock(2026030402);
END $$;

DO $$
DECLARE
    v_d1_node_id         UUID;
    v_dormancy_node_id   UUID;
    v_accum_node_id      UUID;
    v_indicator_node_id  UUID;
    v_ohlcv_node_id      UUID;
    v_pipeline_id        UUID;
    -- D1 ports
    v_d1_features_in     UUID;
    v_d1_dormancy_in     UUID;
    v_d1_accum_in        UUID;
    v_d1_signals_out     UUID;
    -- Dormancy Score ports
    v_dormancy_ohlcv_in  UUID;
    v_dormancy_score_out UUID;
    -- Accum Divergence ports
    v_accum_ohlcv_in     UUID;
    v_accum_signal_out   UUID;
    -- Existing ports
    v_indicator_features_out UUID;
    v_ohlcv_data_out     UUID;
    -- Version tracking
    v_d1_version_id      UUID;
    v_dormancy_version_id UUID;
    v_accum_version_id   UUID;
BEGIN
    -- Look up node IDs
    SELECT node_id INTO v_d1_node_id FROM meta.node WHERE name = 'D1 Dormancy-to-Ignition';
    SELECT node_id INTO v_dormancy_node_id FROM meta.node WHERE name = 'Dormancy Score';
    SELECT node_id INTO v_accum_node_id FROM meta.node WHERE name = 'Accumulation Divergence';
    SELECT node_id INTO v_indicator_node_id FROM meta.node WHERE name = 'Indicator Engine';
    SELECT node_id INTO v_ohlcv_node_id FROM meta.node WHERE name = 'OHLCV Data Feed';
    SELECT pipeline_id INTO v_pipeline_id FROM meta.pipeline WHERE name = 'MCAS Crypto Pipeline v1';

    IF v_d1_node_id IS NULL OR v_dormancy_node_id IS NULL OR v_accum_node_id IS NULL THEN
        RAISE EXCEPTION 'Missing D1 nodes. Run 20260303_add_d1_topology.sql first.';
    END IF;
    -- Indicator Engine / OHLCV may not exist on dev DB (fewer nodes) — edges are optional

    -- =========================================================================
    -- D1 PORTS
    -- =========================================================================
    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'features_in', 'input', 'DataFrame', 'Indicator features from Feature Factory')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_features_in;
    IF v_d1_features_in IS NULL THEN
        SELECT port_id INTO v_d1_features_in FROM meta.port WHERE node_id = v_d1_node_id AND name = 'features_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'dormancy_in', 'input', 'DataFrame', 'Dormancy score features')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_dormancy_in;
    IF v_d1_dormancy_in IS NULL THEN
        SELECT port_id INTO v_d1_dormancy_in FROM meta.port WHERE node_id = v_d1_node_id AND name = 'dormancy_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'accum_div_in', 'input', 'DataFrame', 'Accumulation divergence features')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_accum_in;
    IF v_d1_accum_in IS NULL THEN
        SELECT port_id INTO v_d1_accum_in FROM meta.port WHERE node_id = v_d1_node_id AND name = 'accum_div_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'signals', 'output', 'Signal[]', 'Qualified D1 dormancy-to-ignition signals')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_signals_out;
    IF v_d1_signals_out IS NULL THEN
        SELECT port_id INTO v_d1_signals_out FROM meta.port WHERE node_id = v_d1_node_id AND name = 'signals' AND direction = 'output';
    END IF;

    -- =========================================================================
    -- DORMANCY SCORE PORTS
    -- =========================================================================
    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_dormancy_node_id, 'ohlcv_in', 'input', 'DataFrame', 'Raw OHLCV bars for dormancy computation')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_dormancy_ohlcv_in;
    IF v_dormancy_ohlcv_in IS NULL THEN
        SELECT port_id INTO v_dormancy_ohlcv_in FROM meta.port WHERE node_id = v_dormancy_node_id AND name = 'ohlcv_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_dormancy_node_id, 'dormancy_score', 'output', 'DataFrame', 'Computed dormancy scores per symbol')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_dormancy_score_out;
    IF v_dormancy_score_out IS NULL THEN
        SELECT port_id INTO v_dormancy_score_out FROM meta.port WHERE node_id = v_dormancy_node_id AND name = 'dormancy_score' AND direction = 'output';
    END IF;

    -- =========================================================================
    -- ACCUMULATION DIVERGENCE PORTS
    -- =========================================================================
    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_accum_node_id, 'ohlcv_in', 'input', 'DataFrame', 'Raw OHLCV bars for accumulation analysis')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_accum_ohlcv_in;
    IF v_accum_ohlcv_in IS NULL THEN
        SELECT port_id INTO v_accum_ohlcv_in FROM meta.port WHERE node_id = v_accum_node_id AND name = 'ohlcv_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_accum_node_id, 'accum_divergence', 'output', 'DataFrame', 'Accumulation divergence signals per symbol')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_accum_signal_out;
    IF v_accum_signal_out IS NULL THEN
        SELECT port_id INTO v_accum_signal_out FROM meta.port WHERE node_id = v_accum_node_id AND name = 'accum_divergence' AND direction = 'output';
    END IF;

    -- =========================================================================
    -- EDGES (Indicator Engine / OHLCV edges only if those nodes exist)
    -- =========================================================================
    IF v_indicator_node_id IS NOT NULL THEN
        SELECT port_id INTO v_indicator_features_out
        FROM meta.port WHERE node_id = v_indicator_node_id AND name = 'features' AND direction = 'output';
    END IF;

    IF v_ohlcv_node_id IS NOT NULL THEN
        SELECT port_id INTO v_ohlcv_data_out
        FROM meta.port WHERE node_id = v_ohlcv_node_id AND direction = 'output' LIMIT 1;
    END IF;

    -- Indicator Engine → D1 (if available)
    IF v_indicator_features_out IS NOT NULL THEN
        INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
        VALUES (v_indicator_features_out, v_d1_features_in, '{"relationship": "feature_provider"}')
        ON CONFLICT (source_port_id, target_port_id) DO NOTHING;
    END IF;

    -- Dormancy Score → D1
    INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
    VALUES (v_dormancy_score_out, v_d1_dormancy_in, '{"relationship": "dormancy_score_provider"}')
    ON CONFLICT (source_port_id, target_port_id) DO NOTHING;

    -- Accumulation Divergence → D1
    INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
    VALUES (v_accum_signal_out, v_d1_accum_in, '{"relationship": "accum_divergence_provider"}')
    ON CONFLICT (source_port_id, target_port_id) DO NOTHING;

    -- OHLCV → feature nodes (if available)
    IF v_ohlcv_data_out IS NOT NULL THEN
        INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
        VALUES (v_ohlcv_data_out, v_dormancy_ohlcv_in, '{"relationship": "ohlcv_provider"}')
        ON CONFLICT (source_port_id, target_port_id) DO NOTHING;

        INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
        VALUES (v_ohlcv_data_out, v_accum_ohlcv_in, '{"relationship": "ohlcv_provider"}')
        ON CONFLICT (source_port_id, target_port_id) DO NOTHING;
    END IF;

    -- =========================================================================
    -- PIPELINE_NODE registration
    -- =========================================================================
    IF v_pipeline_id IS NOT NULL THEN
        INSERT INTO meta.pipeline_node (pipeline_id, node_id)
        VALUES (v_pipeline_id, v_d1_node_id)
        ON CONFLICT DO NOTHING;

        INSERT INTO meta.pipeline_node (pipeline_id, node_id)
        VALUES (v_pipeline_id, v_dormancy_node_id)
        ON CONFLICT DO NOTHING;

        INSERT INTO meta.pipeline_node (pipeline_id, node_id)
        VALUES (v_pipeline_id, v_accum_node_id)
        ON CONFLICT DO NOTHING;
    END IF;

    -- =========================================================================
    -- NODE_FILE_MAPPING (correct column: mapping_type, not description)
    -- =========================================================================
    INSERT INTO meta.node_file_mapping (node_id, file_pattern, mapping_type)
    VALUES
        (v_d1_node_id, 'worker/app/setups/d1_dormancy_ignition.py', 'source'),
        (v_d1_node_id, 'scripts/backfill_d1_opportunities.py', 'script'),
        (v_d1_node_id, 'scripts/sim_d1_matrix.py', 'script'),
        (v_d1_node_id, 'scripts/d1_dormancy_distribution.py', 'script'),
        (v_d1_node_id, 'scripts/d1_gate_ablation.py', 'script'),
        (v_dormancy_node_id, 'worker/app/indicators.py', 'source'),
        (v_accum_node_id, 'worker/app/indicators.py', 'source')
    ON CONFLICT DO NOTHING;

    -- =========================================================================
    -- NODE_VERSION (correct columns: version_number, parameters, change_reason)
    -- Config D production winner: STOP_PCT = 0.015
    -- =========================================================================
    INSERT INTO meta.node_version (node_id, version_number, status, parameters, change_reason)
    VALUES (
        v_d1_node_id, 2, 'tested',
        '{
            "DORMANCY_RANGE_THRESHOLD": 0.010,
            "DORMANCY_THRESHOLD": 0.75,
            "BB_WIDTH_COMPRESS": 0.05,
            "VOLUME_Z_SPIKE": 2.0,
            "RVOL_MIN": 3.0,
            "ACCUM_DIVERGENCE_MIN": 0.0,
            "STOP_PCT": 0.015,
            "TP1_PCT": 0.10,
            "TP2_PCT": 0.20,
            "TIME_STOP_BARS_1H": 72,
            "COOLDOWN_BARS_1H": 24,
            "MIN_VOLUME_USD": 75000,
            "MAX_VOLUME_USD": 3000000
        }'::jsonb,
        'D1 v1.1 Config D (production winner): STOP_PCT=0.015. Walk-forward 5/5 folds profitable. Daily PF 2.70, WR 43.4%.'
    )
    RETURNING version_id INTO v_d1_version_id;

    INSERT INTO meta.node_version (node_id, version_number, status, parameters, change_reason)
    VALUES (
        v_dormancy_node_id, 2, 'tested',
        '{"n": 48, "range_threshold": 0.010, "excludes_current_bar": true}'::jsonb,
        'Dormancy Score v1.1: range threshold 0.010 (was 0.001). Excludes current bar to prevent self-invalidation.'
    )
    RETURNING version_id INTO v_dormancy_version_id;

    INSERT INTO meta.node_version (node_id, version_number, status, parameters, change_reason)
    VALUES (
        v_accum_node_id, 1, 'tested',
        '{"n": 20, "role": "score_component_only", "not_a_gate": true}'::jsonb,
        'Accumulation Divergence v1.0: ADL slope vs price slope. Score component only — gate killed 58% of signals.'
    )
    RETURNING version_id INTO v_accum_version_id;

    -- =========================================================================
    -- BIOGRAPHY EVENTS (correct columns: version_id, event_type, event_detail)
    -- =========================================================================
    IF v_d1_version_id IS NOT NULL THEN
        INSERT INTO meta.version_event (version_id, event_type, event_detail, actor, created_at)
        VALUES
        (v_d1_version_id, 'research_finding', '{"summary": "D1 concept identified from 14-strategy research sweep", "detail": "Dormancy-to-Ignition ranked highest-value. Research basis: 69.3% of pump events show pre-accumulation (arxiv 2504.15790)."}'::jsonb, 'claude', '2026-03-03 08:00:00+00'),
        (v_d1_version_id, 'research_finding', '{"summary": "Distribution analysis: 200 symbols, 113K obs, 6 months", "detail": "dormancy>=0.80 passes 6.49%, 30% of symbols reach it. BB width largely redundant. Full conjunction → 0.046%."}'::jsonb, 'claude', '2026-03-03 09:00:00+00'),
        (v_d1_version_id, 'research_finding', '{"summary": "Gate ablation: dormancy is primary filter (16.2x)", "detail": "Dropping dormancy → 16.2x more signals. Dropping BB/RVOL → 1.4x. accum_divergence moved from gate to score component."}'::jsonb, 'claude', '2026-03-03 09:30:00+00'),
        (v_d1_version_id, 'bug_fix', '{"summary": "Fixed self-invalidating dormancy score + compression box", "detail": "Dormancy included breakout bar (fix: offset window). Compression box included current high (fix: save prev_compression_box_high)."}'::jsonb, 'claude', '2026-03-03 10:00:00+00'),
        (v_d1_version_id, 'bug_fix', '{"summary": "Stablecoin contamination: range threshold 0.001 → 0.010", "detail": "v1.0 found only pegged assets. Calibration sweep (150 altcoins): 0.010 = 83% qualify (sweet spot)."}'::jsonb, 'claude', '2026-03-03 11:30:00+00'),
        (v_d1_version_id, 'metrics_run', '{"summary": "v1.1 backfill: 955 signals, 274 symbols, Oct 2025 - Feb 2026", "detail": "Config D winner: daily PF 2.70, WR 43.4%, walk-forward 5/5 folds profitable (worst 1.90)."}'::jsonb, 'claude', '2026-03-03 12:05:00+00');
    END IF;

    RAISE NOTICE 'D1 topology fix complete: 8 ports, 5 edges, 3 pipeline registrations, 7 file mappings, 3 versions, 6 biography events';
END $$;

INSERT INTO public.schema_versions (filename)
VALUES ('20260304_fix_d1_topology_ports.sql')
ON CONFLICT (filename) DO NOTHING;

DO $$ BEGIN
    PERFORM pg_advisory_unlock(2026030402);
END $$;
