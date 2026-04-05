-- Migration: Add Stripe customers and subscriptions tables
-- Date: 2026-04-05
-- Purpose: Link MCAS sessions to Stripe billing via API key hash

SELECT pg_advisory_lock(20260405);

CREATE TABLE IF NOT EXISTS customers (
    id SERIAL PRIMARY KEY,
    api_key_hash TEXT NOT NULL UNIQUE,
    stripe_customer_id TEXT UNIQUE,
    email TEXT,
    sim_runs_used INTEGER NOT NULL DEFAULT 0,
    sim_runs_reset_at TIMESTAMPTZ DEFAULT (date_trunc('month', NOW()) + INTERVAL '1 month'),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    stripe_subscription_id TEXT NOT NULL UNIQUE,
    stripe_price_id TEXT,
    status TEXT NOT NULL DEFAULT 'inactive',
    current_period_start TIMESTAMPTZ,
    current_period_end TIMESTAMPTZ,
    cancel_at_period_end BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_customer_id ON subscriptions(customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

SELECT pg_advisory_unlock(20260405);
