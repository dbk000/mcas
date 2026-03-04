-- Ensure opportunity price-window lookups stay index-backed.
-- Covers predicates: WHERE symbol = ? AND timeframe = '5m' AND bar_start_ts BETWEEN ? AND ?

SELECT pg_advisory_lock(2026030402);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ohlcv_symbol_tf_bar_start_asc
    ON ohlcv(symbol, timeframe, bar_start_ts);

INSERT INTO public.schema_versions (filename)
VALUES ('20260304_add_ohlcv_symbol_tf_bar_start_idx.sql')
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026030402);
