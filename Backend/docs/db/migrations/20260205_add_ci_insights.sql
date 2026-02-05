-- =============================================================================
-- MIGRATION: add_ci_insights
-- =============================================================================
-- CI Insights Log - Structured backing store for correlation-driven decisions
--
-- Each entry captures the full decision chain:
-- observation → hypothesis → change → result → decision
--
-- Apply via: psql -U trading -d trading -f <this_file>
-- =============================================================================

SELECT pg_advisory_lock(202602052);

CREATE TABLE IF NOT EXISTS ci_insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- Timing
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Scope
    setup_id TEXT NOT NULL,
    timeframes TEXT[],
    symbols TEXT[],
    run_window_start TIMESTAMPTZ,
    run_window_end TIMESTAMPTZ,

    -- Evidence links (for reproducibility)
    analysis_id UUID REFERENCES correlation_analysis(id),
    run_ids UUID[],

    -- Observation (what the data showed)
    top_loser_signatures JSONB NOT NULL,  -- [{feature, cohens_d, loser_mean, winner_mean, insight}]
    observation_summary TEXT NOT NULL,     -- Natural language summary

    -- Hypothesis (why this implies a tweak)
    hypothesis TEXT NOT NULL,

    -- Change made (null if observation-only entry)
    change_type TEXT CHECK (change_type IN ('GATE', 'THRESHOLD', 'FILTER', 'SCORE_WEIGHT', 'MODULE', 'NONE')),
    change_target TEXT,                    -- e.g., "S1.entry.clv_threshold"
    change_before TEXT,                    -- Previous value/state
    change_after TEXT,                     -- New value/state
    implementation_ref TEXT,               -- commit hash or config identifier
    config_hash TEXT,                      -- SHA256 of config at time of change

    -- Expected effect
    expected_effect TEXT,                  -- What bucket/metric should move
    expected_direction TEXT CHECK (expected_direction IN ('INCREASE', 'DECREASE', 'SHIFT')),

    -- Result (populated after next run)
    result_analysis_id UUID REFERENCES correlation_analysis(id),
    result_summary TEXT,
    effect_observed BOOLEAN,
    side_effects TEXT,

    -- Decision
    decision TEXT CHECK (decision IN ('PENDING', 'KEEP', 'REVERT', 'ITERATE')),
    decision_rationale TEXT,

    -- Metadata
    author TEXT DEFAULT 'system',
    supersedes_id UUID REFERENCES ci_insights(id)  -- For corrections (new entry supersedes old)
);

CREATE INDEX IF NOT EXISTS idx_ci_insights_setup ON ci_insights(setup_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ci_insights_analysis ON ci_insights(analysis_id);
CREATE INDEX IF NOT EXISTS idx_ci_insights_decision ON ci_insights(decision) WHERE decision = 'PENDING';

COMMENT ON TABLE ci_insights IS
    'Append-only log of correlation-driven insights and parameter changes. Each entry captures the full decision chain for reproducibility.';

SELECT pg_advisory_unlock(202602052);

-- =============================================================================
-- END OF MIGRATION
-- =============================================================================
