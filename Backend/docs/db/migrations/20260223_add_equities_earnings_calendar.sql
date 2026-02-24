-- Migration: Add equities earnings calendar table
-- Date: 2026-02-23

SELECT pg_advisory_lock(2026022301);

CREATE TABLE IF NOT EXISTS equities_earnings_calendar (
    symbol TEXT NOT NULL,
    fiscal_quarter TEXT NOT NULL,
    report_date DATE,
    report_time TEXT CHECK (report_time IN ('BMO', 'AMC', 'TBD') OR report_time IS NULL),
    confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (symbol, fiscal_quarter)
);

CREATE INDEX IF NOT EXISTS idx_equities_earnings_calendar_report_date
    ON equities_earnings_calendar (report_date DESC);

INSERT INTO schema_versions (filename, checksum)
VALUES (
    '20260223_add_equities_earnings_calendar.sql',
    'equities_earnings_calendar_table_v1'
)
ON CONFLICT (filename) DO NOTHING;

SELECT pg_advisory_unlock(2026022301);
