-- Migration: Add raw equities mentions/articles table for backfill-first ingestion
-- Date: 2026-02-22

SELECT pg_advisory_lock(2026022205);

CREATE TABLE IF NOT EXISTS equities_raw_mentions (
    id UUID PRIMARY KEY,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    mention_ts TIMESTAMPTZ NOT NULL,
    available_at_ts TIMESTAMPTZ NOT NULL,
    session_relation TEXT NOT NULL CHECK (
        session_relation IN ('published_preopen', 'intraday', 'postclose', 'weekend', 'unknown')
    ),
    publisher TEXT,
    author TEXT,
    title TEXT,
    url TEXT,
    content TEXT,
    language TEXT,
    region TEXT,
    engagement_score DOUBLE PRECISION,
    dedupe_hash TEXT NOT NULL,
    simhash BIGINT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source, source_item_id, symbol)
);

CREATE INDEX IF NOT EXISTS idx_equities_raw_mentions_symbol_ts
    ON equities_raw_mentions (symbol, mention_ts DESC);
CREATE INDEX IF NOT EXISTS idx_equities_raw_mentions_source_ts
    ON equities_raw_mentions (source, mention_ts DESC);
CREATE INDEX IF NOT EXISTS idx_equities_raw_mentions_available
    ON equities_raw_mentions (available_at_ts DESC);
CREATE INDEX IF NOT EXISTS idx_equities_raw_mentions_dedupe
    ON equities_raw_mentions (dedupe_hash);
CREATE INDEX IF NOT EXISTS idx_equities_raw_mentions_payload_gin
    ON equities_raw_mentions USING GIN(payload);

DROP TRIGGER IF EXISTS trg_equities_raw_mentions_append_only ON equities_raw_mentions;
CREATE TRIGGER trg_equities_raw_mentions_append_only
BEFORE UPDATE OR DELETE ON equities_raw_mentions
FOR EACH ROW EXECUTE FUNCTION equities_append_only_guard();

INSERT INTO schema_versions (filename, checksum)
VALUES (
    '20260222_add_equities_raw_mentions.sql',
    'equities_raw_mentions+append_only'
)
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026022205);
