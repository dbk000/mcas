-- 009_create_source_registry_snapshot.sql
-- Append-only snapshot of the news-source registry state at each commit.
-- Used by downstream ingestor + audit tooling to pin a known registry state
-- and by governance to prove "this run used registry version N".
--
-- Idempotent via advisory lock + IF NOT EXISTS.

BEGIN;

SELECT pg_advisory_xact_lock(hashtext('equities_news.009_source_registry_snapshot'));

CREATE TABLE IF NOT EXISTS equities_news_source_registry_snapshot (
    snapshot_id       BIGSERIAL PRIMARY KEY,

    -- Git commit that produced this snapshot. Immutable anchor for lineage.
    git_commit_sha    TEXT NOT NULL,
    git_branch        TEXT NOT NULL,

    -- Phase tag: 'phase_a' / 'phase_b_sector_<slug>' / 'phase_b_merge'.
    phase             TEXT NOT NULL,

    -- Full registry fingerprint (SHA-256 hex, 64 chars) over all YAML contents
    -- in deterministic iteration order.
    registry_hash     TEXT NOT NULL,

    -- Aggregate statistics.
    n_sources_total   INT  NOT NULL,
    n_universal       INT  NOT NULL,
    n_geo             INT  NOT NULL,
    n_sector          INT  NOT NULL,
    n_sub_industry    INT  NOT NULL,

    -- Per-namespace counts as JSONB for flexibility.
    namespace_counts  JSONB NOT NULL,

    -- Format-validation result summary at snapshot time.
    n_urls_checked    INT,
    n_urls_ok         INT,
    n_urls_broken     INT,

    -- Author of snapshot (agent id or human email).
    authored_by       TEXT NOT NULL,
    authored_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Free-form notes (release tag, audit finding reference, etc.).
    notes             TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS equities_news_source_registry_snapshot_commit_phase_idx
    ON equities_news_source_registry_snapshot (git_commit_sha, phase);

CREATE INDEX IF NOT EXISTS equities_news_source_registry_snapshot_authored_at_idx
    ON equities_news_source_registry_snapshot (authored_at DESC);

-- Enforce append-only: no UPDATE/DELETE from application. (Superuser can still fix.)
DROP TRIGGER IF EXISTS equities_news_source_registry_snapshot_append_only
    ON equities_news_source_registry_snapshot;

CREATE OR REPLACE FUNCTION equities_news_snapshot_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'equities_news_source_registry_snapshot is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER equities_news_source_registry_snapshot_append_only
    BEFORE UPDATE OR DELETE ON equities_news_source_registry_snapshot
    FOR EACH ROW EXECUTE FUNCTION equities_news_snapshot_append_only();

COMMENT ON TABLE equities_news_source_registry_snapshot IS
    'Append-only snapshot of registry state at each commit. Phase 1.b governance.';

COMMIT;
