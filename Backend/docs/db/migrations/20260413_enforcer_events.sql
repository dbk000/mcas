-- =============================================================================
-- Migration: 20260413_enforcer_events.sql
-- Audit trail for regime_state transitions written by the regime enforcer.
--
-- Phase A of ENFORCER_FIRST_DEPLOYMENT_PLAN_20260412.md.
--
-- Writers (per Phase A scope):
--   - 'telegram_override'   — operator command /regime_shock <reason>
--   - 'composite_trigger'   — Backend/workers/regime_detector.py
--   - 'manual_api'          — internal/admin API call (future)
--
-- Idempotent via IF NOT EXISTS + pg_advisory_lock per CLAUDE.md §2.
-- DO NOT EXECUTE during scaffolding. Run on dev (mcas-dev-postgres-1)
-- first, verify, then production (backend-postgres-1) per CLAUDE.md §1.5.
-- =============================================================================

BEGIN;

SELECT pg_advisory_lock(20260413);

CREATE TABLE IF NOT EXISTS enforcer_events (
    id                    BIGSERIAL PRIMARY KEY,
    event_ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    old_state             TEXT        NOT NULL,
    new_state             TEXT        NOT NULL,
    reason                TEXT        NOT NULL,
    source                TEXT        NOT NULL,
        -- 'telegram_override' | 'composite_trigger' | 'manual_api'
    operator_chat_id      BIGINT      NULL,
    signal_count_affected INTEGER     NULL
);

CREATE INDEX IF NOT EXISTS idx_enforcer_events_ts
    ON enforcer_events (event_ts);

SELECT pg_advisory_unlock(20260413);

COMMIT;
