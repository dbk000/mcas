-- OHLCV Retention Indexes for WS candle pruning
-- ================================================
--
-- RUN MANUALLY: CREATE INDEX CONCURRENTLY cannot be inside a transaction.
-- Execute with: psql -U trading -d trading -f this_file.sql
--
-- These partial indexes speed up the daily retention DELETE queries
-- in ohlcv_persister.py for WS-sourced intraday candles.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ohlcv_retention_1m
    ON ohlcv (bar_start_ts) WHERE source = 'ws' AND timeframe = '1m';

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ohlcv_retention_5m15m
    ON ohlcv (bar_start_ts) WHERE source = 'ws' AND timeframe IN ('5m', '15m');

-- Track migration
INSERT INTO schema_versions (filename, applied_at) VALUES
    ('20260210_ohlcv_retention_indexes.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
