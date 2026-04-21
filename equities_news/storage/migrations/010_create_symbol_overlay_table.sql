-- 010_create_symbol_overlay_table.sql
-- Append-only per-symbol overlay rows written by Phase B sector agents.
-- One row per (ticker, snapshot_id) — i.e., each registry snapshot gets a fresh
-- set of overlay rows per symbol. This gives us a time-series audit trail of
-- which IR URL, X handle, transcript provider, etc. we had at each moment.
--
-- The overlay columns in bible_v1_enriched.xlsx are a materialised view of the
-- most recent row per ticker.

BEGIN;

SELECT pg_advisory_xact_lock(hashtext('equities_news.010_symbol_overlay'));

CREATE TABLE IF NOT EXISTS equities_news_symbol_overlay (
    overlay_id                     BIGSERIAL PRIMARY KEY,

    snapshot_id                    BIGINT NOT NULL
        REFERENCES equities_news_source_registry_snapshot(snapshot_id),

    ticker                         TEXT NOT NULL,
    exchange                       TEXT NOT NULL,
    country_code                   CHAR(2) NOT NULL,
    sector_slug                    TEXT NOT NULL,

    -- Overlay columns per Phase 1.b spec.
    ir_url                         TEXT,
    press_release_feed_url         TEXT,
    official_x_handle              TEXT,           -- without '@' prefix
    official_linkedin_url          TEXT,
    earnings_transcript_provider   TEXT,           -- 'quartr' / 'seekingalpha' / 'motley_fool' / 'company_ir' / 'none'
    local_regulatory_filings_url   TEXT,
    sub_industry_override          TEXT,           -- sub_industry.{sector}.{slug} or NULL

    -- Fill metadata — how we arrived at this overlay.
    fill_method                    TEXT,           -- 'manual' / 'heuristic' / 'api_lookup' / 'briefing_default'
    fill_confidence                NUMERIC(3,2)
        CHECK (fill_confidence IS NULL OR (fill_confidence >= 0 AND fill_confidence <= 1)),

    authored_by                    TEXT NOT NULL,
    authored_at                    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT earnings_transcript_provider_enum CHECK (
        earnings_transcript_provider IS NULL OR
        earnings_transcript_provider IN ('quartr','seekingalpha','motley_fool','company_ir','none')
    )
);

-- One row per (ticker, snapshot).
CREATE UNIQUE INDEX IF NOT EXISTS equities_news_symbol_overlay_ticker_snapshot_idx
    ON equities_news_symbol_overlay (ticker, snapshot_id);

CREATE INDEX IF NOT EXISTS equities_news_symbol_overlay_sector_idx
    ON equities_news_symbol_overlay (sector_slug);

CREATE INDEX IF NOT EXISTS equities_news_symbol_overlay_country_idx
    ON equities_news_symbol_overlay (country_code);

-- Append-only trigger.
CREATE OR REPLACE FUNCTION equities_news_overlay_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'equities_news_symbol_overlay is append-only';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS equities_news_symbol_overlay_append_only
    ON equities_news_symbol_overlay;

CREATE TRIGGER equities_news_symbol_overlay_append_only
    BEFORE UPDATE OR DELETE ON equities_news_symbol_overlay
    FOR EACH ROW EXECUTE FUNCTION equities_news_overlay_append_only();

-- Materialised latest-per-ticker view (downstream reads this, not the base table).
CREATE OR REPLACE VIEW equities_news_symbol_overlay_latest AS
SELECT DISTINCT ON (ticker)
    ticker, exchange, country_code, sector_slug,
    ir_url, press_release_feed_url, official_x_handle, official_linkedin_url,
    earnings_transcript_provider, local_regulatory_filings_url, sub_industry_override,
    fill_method, fill_confidence, authored_by, authored_at, snapshot_id
FROM equities_news_symbol_overlay
ORDER BY ticker, authored_at DESC;

COMMENT ON TABLE equities_news_symbol_overlay IS
    'Append-only per-symbol overlay from Phase B. View ..._latest returns most-recent per ticker.';

COMMIT;
