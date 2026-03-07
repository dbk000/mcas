BEGIN;
SELECT pg_advisory_lock(20260307);

-- Add JSONB trace column (nullable -- existing opps have no trace)
ALTER TABLE opportunities
    ADD COLUMN IF NOT EXISTS trace_json JSONB DEFAULT NULL;

-- Comment for documentation
COMMENT ON COLUMN opportunities.trace_json IS
    'Sealed computation trace: candle window, indicators, gates, score, config at trigger time. Schema v1.0. Includes trace_hash (SHA-256) for tamper detection.';

SELECT pg_advisory_unlock(20260307);
COMMIT;
