-- Migration: Add D1 Dormancy-to-Ignition topology to meta schema
-- Date: 2026-03-03
-- Advisory lock: 2026030301
--
-- Adds:
--   1. D1 signal generator node + Dormancy Score + Accumulation Divergence feature nodes
--   2. Input/output ports for each new node
--   3. Edges: Indicator Engine → D1, Dormancy Score → D1, Accum Divergence → D1, OHLCV → features
--   4. Pipeline registration (D1 + feature nodes join "MCAS Crypto Pipeline v1")
--   5. Initial node_version v1 with D1Config parameters
--   6. Node file mappings for auto-capture commit hook
--   7. Biography seed events documenting the development journey
--
-- Rollback:
--   DELETE FROM meta.version_event WHERE node_version_id IN (SELECT nv.id FROM meta.node_version nv JOIN meta.node n ON nv.node_id = n.node_id WHERE n.name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence'));
--   DELETE FROM meta.node_version WHERE node_id IN (SELECT node_id FROM meta.node WHERE name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence'));
--   DELETE FROM meta.node_file_mapping WHERE node_id IN (SELECT node_id FROM meta.node WHERE name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence'));
--   DELETE FROM meta.pipeline_node WHERE node_id IN (SELECT node_id FROM meta.node WHERE name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence'));
--   DELETE FROM meta.edge WHERE source_port_id IN (SELECT port_id FROM meta.port WHERE node_id IN (SELECT node_id FROM meta.node WHERE name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence')));
--   DELETE FROM meta.edge WHERE target_port_id IN (SELECT port_id FROM meta.port WHERE node_id IN (SELECT node_id FROM meta.node WHERE name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence')));
--   DELETE FROM meta.port WHERE node_id IN (SELECT node_id FROM meta.node WHERE name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence'));
--   DELETE FROM meta.node WHERE name IN ('D1 Dormancy-to-Ignition', 'Dormancy Score', 'Accumulation Divergence');

DO $$ BEGIN
    PERFORM pg_advisory_lock(2026030301);
END $$;

-- =============================================================================
-- 1. INSERT NODES
-- =============================================================================

-- D1 signal generator
INSERT INTO meta.node (name, node_type, subsystem, description, source_file, owner)
VALUES (
    'D1 Dormancy-to-Ignition',
    'signal_generator',
    'signal',
    'Dormancy-to-Ignition setup: detects assets transitioning from prolonged dormancy (low-volatility consolidation) into accumulation-driven ignition via volume spike + price breakout. 5-phase state machine: SCANNING → DORMANT_CONFIRMED → ACCUMULATING → TRIGGERED → COOLDOWN.',
    'worker/app/setups/d1_dormancy_ignition.py',
    NULL
)
ON CONFLICT DO NOTHING;

-- Dormancy Score feature engine
INSERT INTO meta.node (name, node_type, subsystem, description, source_file, owner)
VALUES (
    'Dormancy Score',
    'feature_engine',
    'feature',
    'Fraction of trailing N bars (excluding current) where (high-low)/close < range_threshold. Range threshold calibrated at 1.0% from 150-altcoin sweep — at 0.1% only pegged assets qualify.',
    'worker/app/indicators.py',
    NULL
)
ON CONFLICT DO NOTHING;

-- Accumulation Divergence feature engine
INSERT INTO meta.node (name, node_type, subsystem, description, source_file, owner)
VALUES (
    'Accumulation Divergence',
    'feature_engine',
    'feature',
    'ADL slope vs price slope divergence over 20-bar window, normalized by avg_price * avg_volume. Positive = accumulation (ADL rising while price flat). Used as score component, not binary gate.',
    'worker/app/indicators.py',
    NULL
)
ON CONFLICT DO NOTHING;

-- =============================================================================
-- 2. INSERT PORTS, EDGES, PIPELINE_NODE, VERSION, FILE MAPPINGS, BIOGRAPHY
-- =============================================================================

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
    -- -------------------------------------------------------------------------
    -- Look up node IDs
    -- -------------------------------------------------------------------------
    SELECT node_id INTO v_d1_node_id
    FROM meta.node WHERE name = 'D1 Dormancy-to-Ignition';

    SELECT node_id INTO v_dormancy_node_id
    FROM meta.node WHERE name = 'Dormancy Score';

    SELECT node_id INTO v_accum_node_id
    FROM meta.node WHERE name = 'Accumulation Divergence';

    SELECT node_id INTO v_indicator_node_id
    FROM meta.node WHERE name = 'Indicator Engine';

    SELECT node_id INTO v_ohlcv_node_id
    FROM meta.node WHERE name = 'OHLCV Data Feed';

    SELECT pipeline_id INTO v_pipeline_id
    FROM meta.pipeline WHERE name = 'MCAS Crypto Pipeline v1';

    IF v_d1_node_id IS NULL OR v_dormancy_node_id IS NULL
       OR v_accum_node_id IS NULL OR v_indicator_node_id IS NULL THEN
        RAISE EXCEPTION 'Missing required node(s). D1=%, Dormancy=%, AccumDiv=%, IndicatorEngine=%',
            v_d1_node_id, v_dormancy_node_id, v_accum_node_id, v_indicator_node_id;
    END IF;

    -- -------------------------------------------------------------------------
    -- D1 ports
    -- -------------------------------------------------------------------------
    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'features_in', 'input', 'DataFrame', 'Indicator features from Feature Factory')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_features_in;
    IF v_d1_features_in IS NULL THEN
        SELECT port_id INTO v_d1_features_in
        FROM meta.port WHERE node_id = v_d1_node_id AND name = 'features_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'dormancy_in', 'input', 'DataFrame', 'Dormancy score features')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_dormancy_in;
    IF v_d1_dormancy_in IS NULL THEN
        SELECT port_id INTO v_d1_dormancy_in
        FROM meta.port WHERE node_id = v_d1_node_id AND name = 'dormancy_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'accum_div_in', 'input', 'DataFrame', 'Accumulation divergence features')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_accum_in;
    IF v_d1_accum_in IS NULL THEN
        SELECT port_id INTO v_d1_accum_in
        FROM meta.port WHERE node_id = v_d1_node_id AND name = 'accum_div_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_d1_node_id, 'signals', 'output', 'Signal[]', 'Qualified D1 dormancy-to-ignition signals')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_d1_signals_out;
    IF v_d1_signals_out IS NULL THEN
        SELECT port_id INTO v_d1_signals_out
        FROM meta.port WHERE node_id = v_d1_node_id AND name = 'signals' AND direction = 'output';
    END IF;

    -- -------------------------------------------------------------------------
    -- Dormancy Score ports
    -- -------------------------------------------------------------------------
    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_dormancy_node_id, 'ohlcv_in', 'input', 'DataFrame', 'Raw OHLCV bars for dormancy computation')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_dormancy_ohlcv_in;
    IF v_dormancy_ohlcv_in IS NULL THEN
        SELECT port_id INTO v_dormancy_ohlcv_in
        FROM meta.port WHERE node_id = v_dormancy_node_id AND name = 'ohlcv_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_dormancy_node_id, 'dormancy_score', 'output', 'DataFrame', 'Computed dormancy scores per symbol')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_dormancy_score_out;
    IF v_dormancy_score_out IS NULL THEN
        SELECT port_id INTO v_dormancy_score_out
        FROM meta.port WHERE node_id = v_dormancy_node_id AND name = 'dormancy_score' AND direction = 'output';
    END IF;

    -- -------------------------------------------------------------------------
    -- Accumulation Divergence ports
    -- -------------------------------------------------------------------------
    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_accum_node_id, 'ohlcv_in', 'input', 'DataFrame', 'Raw OHLCV bars for accumulation analysis')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_accum_ohlcv_in;
    IF v_accum_ohlcv_in IS NULL THEN
        SELECT port_id INTO v_accum_ohlcv_in
        FROM meta.port WHERE node_id = v_accum_node_id AND name = 'ohlcv_in' AND direction = 'input';
    END IF;

    INSERT INTO meta.port (node_id, name, direction, data_type, description)
    VALUES (v_accum_node_id, 'accum_divergence', 'output', 'DataFrame', 'Accumulation divergence signals per symbol')
    ON CONFLICT (node_id, name, direction) DO NOTHING
    RETURNING port_id INTO v_accum_signal_out;
    IF v_accum_signal_out IS NULL THEN
        SELECT port_id INTO v_accum_signal_out
        FROM meta.port WHERE node_id = v_accum_node_id AND name = 'accum_divergence' AND direction = 'output';
    END IF;

    -- -------------------------------------------------------------------------
    -- Look up existing ports
    -- -------------------------------------------------------------------------
    SELECT port_id INTO v_indicator_features_out
    FROM meta.port
    WHERE node_id = v_indicator_node_id AND name = 'features' AND direction = 'output';

    IF v_indicator_features_out IS NULL THEN
        RAISE EXCEPTION 'Indicator Engine output port "features" not found. Has seed_pipeline_topology been run?';
    END IF;

    -- OHLCV Data Feed output port (for feature node edges)
    IF v_ohlcv_node_id IS NOT NULL THEN
        SELECT port_id INTO v_ohlcv_data_out
        FROM meta.port
        WHERE node_id = v_ohlcv_node_id AND direction = 'output' LIMIT 1;
    END IF;

    -- -------------------------------------------------------------------------
    -- EDGES
    -- -------------------------------------------------------------------------
    -- Indicator Engine → D1 (features)
    INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
    VALUES (v_indicator_features_out, v_d1_features_in, '{"relationship": "feature_provider"}')
    ON CONFLICT (source_port_id, target_port_id) DO NOTHING;

    -- Dormancy Score → D1
    INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
    VALUES (v_dormancy_score_out, v_d1_dormancy_in, '{"relationship": "dormancy_score_provider"}')
    ON CONFLICT (source_port_id, target_port_id) DO NOTHING;

    -- Accumulation Divergence → D1
    INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
    VALUES (v_accum_signal_out, v_d1_accum_in, '{"relationship": "accum_divergence_provider"}')
    ON CONFLICT (source_port_id, target_port_id) DO NOTHING;

    -- OHLCV → Dormancy Score, OHLCV → Accumulation Divergence
    IF v_ohlcv_data_out IS NOT NULL THEN
        INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
        VALUES (v_ohlcv_data_out, v_dormancy_ohlcv_in, '{"relationship": "ohlcv_provider"}')
        ON CONFLICT (source_port_id, target_port_id) DO NOTHING;

        INSERT INTO meta.edge (source_port_id, target_port_id, metadata)
        VALUES (v_ohlcv_data_out, v_accum_ohlcv_in, '{"relationship": "ohlcv_provider"}')
        ON CONFLICT (source_port_id, target_port_id) DO NOTHING;
    END IF;

    -- -------------------------------------------------------------------------
    -- PIPELINE_NODE registration (join "MCAS Crypto Pipeline v1")
    -- -------------------------------------------------------------------------
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

    -- -------------------------------------------------------------------------
    -- NODE_FILE_MAPPING for auto-capture commit hook
    -- -------------------------------------------------------------------------
    INSERT INTO meta.node_file_mapping (node_id, file_pattern, description)
    VALUES
        (v_d1_node_id, 'worker/app/setups/d1_dormancy_ignition.py', 'D1 evaluator implementation'),
        (v_d1_node_id, 'scripts/backfill_d1_opportunities.py', 'D1 backfill script'),
        (v_d1_node_id, 'scripts/sim_d1_matrix.py', 'D1 simulation matrix'),
        (v_d1_node_id, 'scripts/d1_dormancy_distribution.py', 'D1 distribution analysis'),
        (v_d1_node_id, 'scripts/d1_gate_ablation.py', 'D1 gate ablation analysis'),
        (v_d1_node_id, 'scripts/d1_gate_diagnostic.py', 'D1 gate diagnostic'),
        (v_dormancy_node_id, 'worker/app/indicators.py', 'Feature Factory (shared — contains dormancy_score)'),
        (v_accum_node_id, 'worker/app/indicators.py', 'Feature Factory (shared — contains accum_divergence)')
    ON CONFLICT DO NOTHING;

    -- -------------------------------------------------------------------------
    -- INITIAL NODE_VERSION v1 for D1
    -- -------------------------------------------------------------------------
    INSERT INTO meta.node_version (node_id, version_tag, status, config_snapshot, change_summary)
    VALUES (
        v_d1_node_id,
        'v1.1',
        'tested',
        '{
            "DORMANCY_RANGE_THRESHOLD": 0.010,
            "DORMANCY_THRESHOLD": 0.75,
            "BB_WIDTH_COMPRESS": 0.05,
            "VOLUME_Z_SPIKE": 2.0,
            "RVOL_MIN": 3.0,
            "ACCUM_DIVERGENCE_MIN": 0.0,
            "STOP_PCT": 0.025,
            "TP1_PCT": 0.10,
            "TP2_PCT": 0.20,
            "TIME_STOP_BARS_1H": 72,
            "COOLDOWN_BARS_1H": 24,
            "MIN_VOLUME_USD": 75000,
            "MAX_VOLUME_USD": 3000000,
            "notes": "v1.0 only found pegged assets (range_thresh 0.001). v1.1 corrected to 0.010 based on 150-altcoin calibration sweep. accum_divergence moved from gate to score-only. Dormancy score excludes current bar (self-invalidation fix)."
        }'::jsonb,
        'D1 v1.1: range threshold corrected from 0.001 to 0.010. 955 signals across 274 symbols (6mo backfill). Previous v1.0 found only 13 signals from 1 pegged asset.'
    )
    RETURNING id INTO v_d1_version_id;

    -- Initial versions for feature nodes
    INSERT INTO meta.node_version (node_id, version_tag, status, config_snapshot, change_summary)
    VALUES (
        v_dormancy_node_id, 'v1.1', 'tested',
        '{"n": 48, "range_threshold": 0.010, "excludes_current_bar": true}'::jsonb,
        'Dormancy Score v1.1: range threshold corrected to 0.010 (was 0.001). Excludes current bar to prevent self-invalidation on breakout.'
    )
    RETURNING id INTO v_dormancy_version_id;

    INSERT INTO meta.node_version (node_id, version_tag, status, config_snapshot, change_summary)
    VALUES (
        v_accum_node_id, 'v1.0', 'tested',
        '{"n": 20, "role": "score_component_only", "not_a_gate": true}'::jsonb,
        'Accumulation Divergence v1.0: ADL slope vs price slope, normalized. Used as score component only — data showed >0 gate killed 58% of signals while median at trigger was -0.035.'
    )
    RETURNING id INTO v_accum_version_id;

    -- -------------------------------------------------------------------------
    -- BIOGRAPHY SEED EVENTS — documenting the development journey
    -- -------------------------------------------------------------------------
    IF v_d1_version_id IS NOT NULL THEN
        -- 1. Research origin
        INSERT INTO meta.version_event (node_version_id, event_type, summary, detail, created_ts)
        VALUES (
            v_d1_version_id, 'research_finding',
            'D1 concept identified from 14-strategy research sweep',
            'Dormancy-to-Ignition ranked highest-value from sweep of 14 open-source microcap crypto strategies. Research basis: 69.3% of pump events show pre-accumulation averaging 2160 minutes (arxiv 2504.15790), dual-condition thresholding achieves 0.84 precision (arxiv 2503.08692). All 14 strategies documented in Backend/docs/strategies/.',
            '2026-03-03 08:00:00+00'
        );

        -- 2. Distribution analysis
        INSERT INTO meta.version_event (node_version_id, event_type, summary, detail, created_ts)
        VALUES (
            v_d1_version_id, 'research_finding',
            'Distribution analysis: 200 symbols, 113K obs, 6 months',
            'Self-contained script (no FeatureFactory dependency) computed 5 indicators across 200 random symbols. Key findings: dormancy>=0.80 passes 6.49%, 30% of symbols reach it. BB width largely redundant with dormancy. Conjunctive gates compress hard: full conjunction → 0.046%. Output: Backend/scripts/output/d1_dormancy_distribution.npz, Backend/scripts/output/d1_dormancy_distribution_results.txt',
            '2026-03-03 09:00:00+00'
        );

        -- 3. Gate ablation
        INSERT INTO meta.version_event (node_version_id, event_type, summary, detail, created_ts)
        VALUES (
            v_d1_version_id, 'research_finding',
            'Gate ablation: dormancy is primary filter (16.2x), BB/RVOL nearly redundant',
            'Full conjunction = 31/112,982 obs. Dropping dormancy → 16.2x more signals (primary filter). Dropping BB width or RVOL → only 1.4x (nearly redundant). Decision: move accum_divergence from gate to score component (killed 58% of signals, median at trigger -0.035). Output: Backend/scripts/output/d1_gate_ablation_results.txt',
            '2026-03-03 09:30:00+00'
        );

        -- 4. Self-invalidation bug fix
        INSERT INTO meta.version_event (node_version_id, event_type, summary, detail, created_ts)
        VALUES (
            v_d1_version_id, 'bug_fix',
            'Fixed self-invalidating indicators: dormancy score + compression box',
            'Bug 1: Dormancy score included breakout bar — score dropped below threshold on exact trigger bar. Fix: offset window by 1 (highs[-(n+1):-1]). Bug 2: Compression box included current bar high — close > donchian_high impossible by definition. Fix: save prev_compression_box_high before update, compare against previous. Root cause: treating regime description and trigger event as simultaneous when they are sequential.',
            '2026-03-03 10:00:00+00'
        );

        -- 5. Stablecoin contamination discovery and fix
        INSERT INTO meta.version_event (node_version_id, event_type, summary, detail, created_ts)
        VALUES (
            v_d1_version_id, 'bug_fix',
            'Fixed stablecoin contamination: range threshold 0.001 → 0.010',
            'v1.0 backfill found 13 signals from 1 symbol (EURIUSDT, EUR-pegged). Gate diagnostic revealed all passing symbols were stablecoins/pegged assets — dormant by design. Range threshold 0.001 (0.1%) was 10x too tight for altcoins. Calibration sweep (150 altcoins, 6mo): at 0.005 only 15% qualify, at 0.010 83% qualify (sweet spot), at 0.015 98% (too loose). v1.1 with 0.010 threshold: 955 signals across 274 symbols. Output: Backend/scripts/d1_gate_diagnostic.py',
            '2026-03-03 11:30:00+00'
        );

        -- 6. v1.1 backfill result
        INSERT INTO meta.version_event (node_version_id, event_type, summary, detail, created_ts)
        VALUES (
            v_d1_version_id, 'metrics_run',
            'v1.1 Balanced backfill: 955 signals, 274 symbols, Oct 2025 - Feb 2026',
            'Backfill config: dormancy>=0.75, vol_z>=2.0, rvol>=3.0, bb<0.05, range_threshold=0.010. 423 eligible symbols (92 excluded). ~8 signals/day. Top symbols: SUNUSDT(19), GASUSDT(13), WINUSDT(12), GLMUSDT(12), IQUSDT(12). Batch ID: backfill_20260303_D1v1_edc7dd7d. Pending: simulation matrix for exit config optimization.',
            '2026-03-03 12:05:00+00'
        );
    END IF;

    RAISE NOTICE 'D1 topology complete: 3 nodes, 8 ports, 5 edges, 3 pipeline registrations, 3 versions, 8 file mappings, 6 biography events';
END $$;

-- =============================================================================
-- 4. RECORD MIGRATION
-- =============================================================================
INSERT INTO public.schema_versions (filename)
VALUES ('20260303_add_d1_topology.sql')
ON CONFLICT (filename) DO NOTHING;

DO $$ BEGIN
    PERFORM pg_advisory_unlock(2026030301);
END $$;
