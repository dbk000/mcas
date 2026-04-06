-- Migration: Make api_key_hash nullable on customers table
-- Date: 2026-04-06
-- Purpose: Allow Stripe customers to be created before MCAS authentication.
--          Customer pays first, connects backend later. api_key_hash is set
--          when the user authenticates and claims their subscription.

SELECT pg_advisory_lock(20260406);

ALTER TABLE customers ALTER COLUMN api_key_hash DROP NOT NULL;

SELECT pg_advisory_unlock(20260406);
