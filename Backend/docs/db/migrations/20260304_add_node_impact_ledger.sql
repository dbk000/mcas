-- Node Impact Ledger: per-node quantified contribution to every experiment
-- Enables answering "what happens if I change X?" by scanning past impacts
-- instead of re-running full experiments.
--
-- Usage: every sim/backfill/ablation records each involved node's metrics.
-- Query pattern: SELECT * FROM meta.node_impact WHERE node_id = X
--   → see every experiment where this node participated + its marginal contribution.

DO $$
BEGIN
    PERFORM pg_advisory_lock(20260304);

    -- Node impact ledger
    CREATE TABLE IF NOT EXISTS meta.node_impact (
        impact_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        node_id         UUID NOT NULL REFERENCES meta.node(node_id),
        version_id      UUID REFERENCES meta.node_version(version_id),
        experiment_id   UUID REFERENCES meta.experiment(experiment_id),

        -- What produced this impact record
        experiment_type TEXT NOT NULL,  -- 'sim_matrix', 'backfill', 'gate_ablation', 'distribution_analysis'
        experiment_ref  TEXT,           -- batch_id, run_id, or script output path

        -- Node-specific quantified impact
        metrics         JSONB NOT NULL, -- node-specific: slices, marginal contribution, filter rates, etc.

        -- Provenance
        artifacts       JSONB,          -- file paths to detailed output
        created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    -- Index for the primary query: "show me all experiments involving this node"
    CREATE INDEX IF NOT EXISTS idx_node_impact_node_id ON meta.node_impact(node_id);
    CREATE INDEX IF NOT EXISTS idx_node_impact_experiment_id ON meta.node_impact(experiment_id);
    CREATE INDEX IF NOT EXISTS idx_node_impact_type ON meta.node_impact(experiment_type);

    -- Track migration
    INSERT INTO public.schema_versions (filename) VALUES ('20260304_add_node_impact_ledger.sql')
    ON CONFLICT (filename) DO NOTHING;

    PERFORM pg_advisory_unlock(20260304);
END $$;
