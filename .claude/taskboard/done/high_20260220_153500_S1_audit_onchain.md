REQUESTED_BY: S1
TASK: Audit all on-chain and derivatives tables in production DB. For each table: row count, column names/types, date range (min/max timestamp), symbol count, granularity, and a sample row. Tables: funding_rates, open_interest, oi_history_extended, liquidations, long_short_ratio, taker_buy_sell, exchange_flows, dex_snapshots, dex_volume, dune_dex_volume, dune_executions, dune_large_transfers, network_activity, holder_distribution, token_holder_stats, whale_transactions, stablecoin_supply, protocol_tvl, derivatives_features_1h, onchain_features_1h. Write structured JSON output.
SCOPE: /srv/mcas/results/
OUTPUT_TO: /srv/mcas/results/data_audit_onchain.json
PRIORITY: high
CONSTRAINT: Run queries via docker exec backend-postgres-1. Keep each query light — no full table scans. Use COUNT(*), MIN/MAX on timestamp columns, COUNT(DISTINCT symbol).
AGENT_TYPE: compute
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20 15:35
COMPLETED_AT: 2026-02-20 15:43
RESULT: 13/20 tables have data. Best: Santiment daily (network/whale/exchange 300+ days) but 30d stale. Derivatives <2 days. Feature tables 3% complete.
