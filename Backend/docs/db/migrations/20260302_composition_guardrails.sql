-- Migration: Composition Guardrails — Statistical Context + Explosion Management
-- Adds: pareto_status on node_version, composition_constraint table,
--        updates validation gate trigger to include n_trials_corrected
-- Advisory lock: 2026030203

BEGIN;
SELECT pg_advisory_lock(2026030203);

-- 1. Add pareto_status to node_version for Pareto frontier management
ALTER TABLE meta.node_version ADD COLUMN IF NOT EXISTS pareto_status TEXT DEFAULT 'active';
COMMENT ON COLUMN meta.node_version.pareto_status IS 'Pareto frontier status: active (on frontier), dominated (strictly better version exists), historical (excluded from composition search).';

-- 2. Create composition_constraint table for semantic constraint propagation
CREATE TABLE IF NOT EXISTS meta.composition_constraint (
    constraint_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    node_a_id       UUID NOT NULL REFERENCES meta.node(node_id),
    node_b_id       UUID NOT NULL REFERENCES meta.node(node_id),
    constraint_type TEXT NOT NULL,
    constraint_rule JSONB NOT NULL,
    description     TEXT NOT NULL,
    created_at      TIMESTAMPTZ DEFAULT now()
);

COMMENT ON TABLE meta.composition_constraint IS 'Semantic constraints between node pairs for composition assembly. Types: requires_compatible_timeframe, requires_compatible_universe, excludes.';

CREATE INDEX IF NOT EXISTS idx_cc_node_a ON meta.composition_constraint(node_a_id);
CREATE INDEX IF NOT EXISTS idx_cc_node_b ON meta.composition_constraint(node_b_id);

-- 3. Update validation gate trigger to include n_trials_corrected
CREATE OR REPLACE FUNCTION meta.enforce_validation_gate()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.status IN ('validated', 'deployed') AND (
        NOT (NEW.validation_gate->>'walk_forward_pass')::BOOLEAN IS TRUE
        OR NOT (NEW.validation_gate->>'holdout_pass')::BOOLEAN IS TRUE
        OR NOT (NEW.validation_gate->>'dsr_positive')::BOOLEAN IS TRUE
        OR NOT (NEW.validation_gate->>'slippage_1_5x_pass')::BOOLEAN IS TRUE
        OR NOT (NEW.validation_gate->>'n_trials_corrected')::BOOLEAN IS TRUE
    ) THEN
        RAISE EXCEPTION 'Cannot set composition status to %: all 5 validation_gate fields must be true (walk_forward_pass, holdout_pass, dsr_positive, slippage_1_5x_pass, n_trials_corrected). Got: %',
            NEW.status, NEW.validation_gate;
    END IF;

    -- Enforce statistical_context when source is sweep_result and promoting to validated/deployed
    IF NEW.status IN ('validated', 'deployed') AND NEW.source = 'sweep_result' THEN
        IF NOT (NEW.statistical_context ? 'n_compositions_tested')
           OR NOT (NEW.statistical_context ? 'deflated_sharpe_ratio') THEN
            RAISE EXCEPTION 'Sweep-sourced compositions require statistical_context with n_compositions_tested and deflated_sharpe_ratio when advancing to %.', NEW.status;
        END IF;
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Re-create trigger (DROP IF EXISTS + CREATE ensures idempotency)
DROP TRIGGER IF EXISTS trg_comp_validation_gate ON meta.composition;
CREATE TRIGGER trg_comp_validation_gate
    BEFORE INSERT OR UPDATE ON meta.composition
    FOR EACH ROW EXECUTE FUNCTION meta.enforce_validation_gate();

-- 4. Index for Pareto frontier queries
CREATE INDEX IF NOT EXISTS idx_meta_nv_pareto ON meta.node_version(node_id, pareto_status)
    WHERE pareto_status = 'active';

-- Track migration
INSERT INTO schema_versions (filename)
VALUES ('20260302_composition_guardrails.sql')
ON CONFLICT DO NOTHING;

SELECT pg_advisory_unlock(2026030203);
COMMIT;
