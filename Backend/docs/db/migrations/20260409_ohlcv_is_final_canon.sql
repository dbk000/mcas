-- Canon §8 anti-lookahead invariant at the DB level.
--
-- is_final=TRUE may only be set on a bar whose end time has already passed.
-- REST ingestor historically wrote the current (forming) hour bar with
-- is_final=TRUE at ~H:06 every hour (see rest_ingestor_is_final_fix_plan.md).
-- This migration:
--   1. Installs an IMMUTABLE helper tf_seconds(tf) for timeframe → seconds
--   2. UPDATEs any existing forming-bar rows to is_final=FALSE
--   3. Adds CHECK constraint ohlcv_is_final_canon (NOT VALID + VALIDATE)
--      that makes the bug structurally impossible going forward
--
-- Structure: split into 3 phases across separate transactions so that the
-- initial UPDATE commits before we take the table lock for constraint
-- validation. Packing UPDATE + ADD CONSTRAINT + VALIDATE into one
-- transaction races with concurrent writers (backfill_recent_gaps,
-- _ingest_known_ohlcv) that insert forming-bar rows between the UPDATE and
-- the VALIDATE — those new rows are NOT covered by the pre-committed UPDATE
-- and cause VALIDATE to fail. Phase 3 takes LOCK TABLE ... IN SHARE ROW
-- EXCLUSIVE MODE for its brief window so writers are frozen during the
-- add-and-validate critical section.
--
-- Idempotent. Safe to re-run. Each phase independently.

-- ---------------------------------------------------------------------------
-- Phase 1 — Install the tf_seconds helper.
-- ---------------------------------------------------------------------------
BEGIN;
SELECT pg_advisory_lock(20260409001);

CREATE OR REPLACE FUNCTION tf_seconds(tf TEXT) RETURNS INTEGER AS $$
  SELECT CASE tf
    WHEN '1m'  THEN 60
    WHEN '5m'  THEN 300
    WHEN '15m' THEN 900
    WHEN '30m' THEN 1800
    WHEN '1h'  THEN 3600
    WHEN '4h'  THEN 14400
    WHEN '1d'  THEN 86400
    ELSE NULL
  END
$$ LANGUAGE SQL IMMUTABLE;

SELECT pg_advisory_unlock(20260409001);
COMMIT;

-- ---------------------------------------------------------------------------
-- Phase 2 — Bulk clean-up of currently-wrong rows (separate txn so the
-- fix commits before we take the table lock for Phase 3).
-- ---------------------------------------------------------------------------
BEGIN;
UPDATE ohlcv
SET is_final = FALSE
WHERE is_final = TRUE
  AND tf_seconds(timeframe) IS NOT NULL
  AND bar_start_ts + (tf_seconds(timeframe) || ' seconds')::interval > NOW();
COMMIT;

-- ---------------------------------------------------------------------------
-- Phase 3 — Add + validate the CHECK constraint with a brief table lock.
--
-- SHARE ROW EXCLUSIVE blocks all writes (INSERT/UPDATE/DELETE) but still
-- allows concurrent SELECTs. We hold it only for the few statements below
-- so the ingestor/backfill-worker pause is short.
-- ---------------------------------------------------------------------------
BEGIN;
SELECT pg_advisory_lock(20260409001);

LOCK TABLE ohlcv IN SHARE ROW EXCLUSIVE MODE;

-- Sweep any stragglers that arrived between Phase 2 and the table lock.
UPDATE ohlcv
SET is_final = FALSE
WHERE is_final = TRUE
  AND tf_seconds(timeframe) IS NOT NULL
  AND bar_start_ts + (tf_seconds(timeframe) || ' seconds')::interval > NOW();

-- Add the constraint. NOT VALID = instant metadata-only change.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ohlcv_is_final_canon'
    ) THEN
        ALTER TABLE ohlcv
          ADD CONSTRAINT ohlcv_is_final_canon
          CHECK (
            is_final = FALSE
            OR tf_seconds(timeframe) IS NULL
            OR bar_start_ts + (tf_seconds(timeframe) || ' seconds')::interval <= NOW()
          )
          NOT VALID;
    END IF;
END $$;

-- Now validate. With the table lock held, no concurrent writer can insert a
-- forming-bar row between this and COMMIT.
ALTER TABLE ohlcv VALIDATE CONSTRAINT ohlcv_is_final_canon;

COMMENT ON CONSTRAINT ohlcv_is_final_canon ON ohlcv IS
    'Canon §8 anti-lookahead: is_final=TRUE may only be set on a closed bar. '
    'See rest_ingestor_is_final_fix_plan.md.';

SELECT pg_advisory_unlock(20260409001);
COMMIT;
