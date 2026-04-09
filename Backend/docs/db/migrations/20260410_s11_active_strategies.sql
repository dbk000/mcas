-- =============================================================================
-- Migration: 20260410_s11_active_strategies.sql
-- Seed S11 (MACD Long Reversion) into config_active_strategies so the
-- unified dashboard scaffold picks it up with a zero-opportunity baseline
-- until live signals start flowing.
--
-- Source of truth: governance/bootstrap.py _populate_strategies() catalog.
-- Validation: s11_config_locked.json under
--   Backend/scripts/output/macd_wide_candidates_20260409_192456/
-- Walk-forward: 4/5 folds PF>1, 4/5 folds dPFmed>0.9
-- Holdout (Mar 20 -> Apr 9): N=2201, PF=1.226, dPFmed=2.061, win%=52.4%
--
-- Idempotent via ON CONFLICT.
-- =============================================================================

SELECT pg_advisory_lock(202604101);

INSERT INTO config_active_strategies (setup_id, asset_class, status) VALUES
    ('S11', 'crypto', 'active')
ON CONFLICT (setup_id) DO UPDATE SET
    asset_class = EXCLUDED.asset_class,
    status      = EXCLUDED.status;

SELECT pg_advisory_unlock(202604101);
