-- Canon §8 anti-lookahead invariant at the DB level.
--
-- is_final=TRUE may only be set on a bar whose end time has already passed.
-- REST ingestor historically wrote the current (forming) hour bar with
-- is_final=TRUE at ~H:06 every hour (see rest_ingestor_is_final_fix_plan.md).
-- This migration:
--   1. Installs an IMMUTABLE helper tf_seconds(tf) for timeframe → seconds
--   2. UPDATEs any existing forming-bar rows to is_final=FALSE so the
--      VALIDATE step below doesn't explode
--   3. Adds CHECK constraint ohlcv_is_final_canon (NOT VALID + VALIDATE)
--      that makes the bug structurally impossible going forward
--
-- Idempotent. Safe to re-run — the function uses CREATE OR REPLACE and
-- the constraint is guarded by IF NOT EXISTS logic via the DO block.

BEGIN;
SELECT pg_advisory_lock(20260409001);

-- ---------------------------------------------------------------------------
-- 1) Timeframe → seconds lookup (IMMUTABLE so the CHECK can reference it)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 2) Clean up currently-wrong rows BEFORE we VALIDATE
-- ---------------------------------------------------------------------------
-- Any row that currently claims is_final=TRUE but whose bar end time is in
-- the future is a Canon §8 violation. Flip it to is_final=FALSE. The WS
-- persister (which correctly writes the ws-sourced closed bar at bar close)
-- will continue to write its own row for the same (symbol, timeframe,
-- bar_start_ts) under source='ws' via ON CONFLICT DO NOTHING semantics.
UPDATE ohlcv
SET is_final = FALSE
WHERE is_final = TRUE
  AND tf_seconds(timeframe) IS NOT NULL
  AND bar_start_ts + (tf_seconds(timeframe) || ' seconds')::interval > NOW();

-- ---------------------------------------------------------------------------
-- 3) CHECK constraint: is_final=TRUE ⇒ bar end time is in the past
-- ---------------------------------------------------------------------------
-- NOT VALID first so the ADD is instant and doesn't block. Rows with
-- is_final=FALSE are always allowed. Rows with an unknown timeframe
-- (tf_seconds returns NULL) are also allowed — we don't want this
-- constraint to break future timeframes we haven't added yet.
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

-- VALIDATE scans the table but doesn't block concurrent reads/writes.
-- Safe to run even on the 102GB prod table.
ALTER TABLE ohlcv VALIDATE CONSTRAINT ohlcv_is_final_canon;

COMMENT ON CONSTRAINT ohlcv_is_final_canon ON ohlcv IS
    'Canon §8 anti-lookahead: is_final=TRUE may only be set on a closed bar. '
    'See rest_ingestor_is_final_fix_plan.md.';

SELECT pg_advisory_unlock(20260409001);
COMMIT;
