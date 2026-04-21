-- 011_create_author_log.sql
-- Append-only per-symbol audit trail. Each Phase B agent writes one row per
-- symbol processed, capturing which overlay columns were populated, which
-- failed, and a confidence score.
--
-- Distinct from symbol_overlay: the overlay table stores the *values*; this
-- table stores the *authoring events* that produced those values, with
-- sufficient detail to reconstruct agent behaviour and blame stale data.

BEGIN;

SELECT pg_advisory_xact_lock(hashtext('equities_news.011_author_log'));

CREATE TABLE IF NOT EXISTS equities_news_author_log (
    log_id              BIGSERIAL PRIMARY KEY,

    snapshot_id         BIGINT NOT NULL
        REFERENCES equities_news_source_registry_snapshot(snapshot_id),

    ticker              TEXT NOT NULL,
    sector_slug         TEXT NOT NULL,
    agent_id            TEXT NOT NULL,     -- e.g. 'phase_b_health_care_opus'

    columns_found       TEXT[] NOT NULL,   -- overlay columns populated
    columns_failed      TEXT[] NOT NULL,   -- overlay columns where lookup failed
    columns_skipped     TEXT[],            -- columns intentionally skipped (e.g. no IR page for microcap)

    confidence          NUMERIC(3,2) NOT NULL
        CHECK (confidence >= 0 AND confidence <= 1),

    -- Structured notes for later review (failure reasons, ambiguous matches).
    authoring_notes     JSONB,

    -- Wall-clock cost to author this row — tracks Phase B throughput.
    authored_in_seconds NUMERIC(6,2),

    authored_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS equities_news_author_log_ticker_idx
    ON equities_news_author_log (ticker);

CREATE INDEX IF NOT EXISTS equities_news_author_log_sector_agent_idx
    ON equities_news_author_log (sector_slug, agent_id);

CREATE INDEX IF NOT EXISTS equities_news_author_log_authored_at_idx
    ON equities_news_author_log (authored_at DESC);

CREATE OR REPLACE FUNCTION equities_news_author_log_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'equities_news_author_log is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS equities_news_author_log_append_only
    ON equities_news_author_log;

CREATE TRIGGER equities_news_author_log_append_only
    BEFORE UPDATE OR DELETE ON equities_news_author_log
    FOR EACH ROW EXECUTE FUNCTION equities_news_author_log_append_only();

COMMENT ON TABLE equities_news_author_log IS
    'Append-only per-symbol author events from Phase B. One row per processed ticker per snapshot.';

COMMIT;
