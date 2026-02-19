-- Migration: Add social pipeline tables (social_posts, social_features_1h, social_signals, symbol_aliases)
-- Date: 2026-02-19
-- Advisory lock for idempotency

DO $$ BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('add_social_tables'));
END $$;

-- ═══════════════════════════════════════════════════════════
-- RAW SOCIAL POSTS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS social_posts (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT,
    channel TEXT,
    author TEXT,
    title TEXT,
    content TEXT,
    post_ts TIMESTAMPTZ NOT NULL,
    ingested_ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    engagement_score INT DEFAULT 0,
    symbols TEXT[],
    batch_id TEXT,
    UNIQUE(source, source_id)
);

CREATE INDEX IF NOT EXISTS idx_social_posts_ts ON social_posts(post_ts DESC);
CREATE INDEX IF NOT EXISTS idx_social_posts_symbols ON social_posts USING GIN(symbols);
CREATE INDEX IF NOT EXISTS idx_social_posts_source ON social_posts(source, channel, post_ts DESC);

-- ═══════════════════════════════════════════════════════════
-- BRAIN 1: HOURLY AGGREGATED FEATURES
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS social_features_1h (
    symbol TEXT NOT NULL,
    bar_start_ts TIMESTAMPTZ NOT NULL,
    mention_count INT NOT NULL DEFAULT 0,
    unique_authors INT NOT NULL DEFAULT 0,
    platform_count INT NOT NULL DEFAULT 0,
    engagement_sum INT NOT NULL DEFAULT 0,
    mention_velocity DOUBLE PRECISION,
    mention_acceleration DOUBLE PRECISION,
    author_novelty_ratio DOUBLE PRECISION,
    first_mention BOOLEAN NOT NULL DEFAULT FALSE,
    mention_volume_ratio DOUBLE PRECISION,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, bar_start_ts)
);

-- ═══════════════════════════════════════════════════════════
-- BRAIN 2: LLM SIGNAL VECTORS
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS social_signals (
    id BIGSERIAL PRIMARY KEY,
    symbol TEXT NOT NULL,
    trigger_ts TIMESTAMPTZ NOT NULL,
    trigger_type TEXT NOT NULL,
    catalyst_detected BOOLEAN,
    catalyst_type TEXT,
    catalyst_timeline TEXT,
    catalyst_specificity DOUBLE PRECISION,
    catalyst_verifiability DOUBLE PRECISION,
    narrative_category TEXT,
    narrative_novelty DOUBLE PRECISION,
    cascade_stage TEXT,
    discussion_quality DOUBLE PRECISION,
    author_credibility DOUBLE PRECISION,
    source_diversity DOUBLE PRECISION,
    coordination_probability DOUBLE PRECISION,
    controversy_level DOUBLE PRECISION,
    confidence DOUBLE PRECISION,
    raw_summary TEXT,
    model_used TEXT NOT NULL DEFAULT 'claude-haiku-4-5-20251001',
    input_token_count INT,
    output_token_count INT,
    input_posts JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_social_signals_symbol_ts ON social_signals(symbol, trigger_ts DESC);
CREATE INDEX IF NOT EXISTS idx_social_signals_cascade ON social_signals(cascade_stage, trigger_ts DESC);

-- ═══════════════════════════════════════════════════════════
-- SYMBOL ALIASES
-- ═══════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS symbol_aliases (
    symbol TEXT NOT NULL,
    alias TEXT NOT NULL,
    alias_type TEXT NOT NULL,
    is_ambiguous BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (symbol, alias)
);

CREATE INDEX IF NOT EXISTS idx_symbol_aliases_alias ON symbol_aliases(LOWER(alias));

-- Track migration
INSERT INTO schema_versions (filename, checksum)
VALUES ('20260219_add_social_tables.sql', 'social_posts+social_features_1h+social_signals+symbol_aliases')
ON CONFLICT (filename) DO NOTHING;
