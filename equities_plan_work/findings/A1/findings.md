# A1 — Snapshot equity tables (findings)

- Executed by: sub-agent W1
- UTC timestamp label: 20260416_213832
- Dump path: `Backend/scripts/output/snapshots/equities_pre_backfill_20260416_213832/equities_tables.sql`
- Exit code: 0; stderr: empty (0 bytes)
- Verdict: PASS

## Decisions made

- **Snapshot directory label:** `equities_pre_backfill_20260416_213832` — second-precision UTC timestamp (`date -u +%Y%m%d_%H%M%S`) to avoid same-day collision if A1 is ever re-run. Matches the brief's literal template.
- **Table filters on `pg_dump`:** `-t 'equities_*'` + `-t 'ahc_*'` + `-t 'config_active_strategies'` + `-t 'config_fee_schedule'` — the literal four filters specified in the brief. All 18 equity tables in `postgres_audit.md` match one of the first two patterns; the two `config_*` tables carry the cross-asset metadata (`asset_class`, `eq_default` fee row) that the equity migrations in Batch 2 will touch.
- **`--no-owner --no-acl` flags kept as specified.** Dump is for rollback into the same cluster; ownership/ACL statements only complicate restores into different environments and can leak role names. Matches standard MCAS snapshot convention.
- **Plain-format output (default), not custom/archive.** Brief's verification steps assume a readable SQL file (`wc -l`, `grep -c "CREATE TABLE"`). A custom-format archive would require `pg_restore --list` which is unavailable on the host (postgresql-client not installed) — plain-format SQL is self-validating via grep and directly re-runnable with `psql`.
- **Did NOT include `ohlcv`, `opportunities`, or `v_unified_opportunity_pnl`.** The audit flags equity OHLCV rows mixed into the shared `ohlcv` table (341,893 rows keyed by `source`) and an FK from `equities_opportunities.legacy_opportunity_id` to the main `opportunities.id`. The brief explicitly lists only 4 table-glob filters — scoping beyond that is out of scope for A1 and would change the rollback surface.

## Data sampled

```
wc -l equities_tables.sql
1464960 equities_tables.sql

du -h equities_tables.sql
3.5G    equities_tables.sql

du -b equities_tables.sql
3748716823 equities_tables.sql   (≈ 3.57 GB)

grep -c "CREATE TABLE.*equities_" equities_tables.sql
14

grep -c "CREATE TABLE.*ahc_" equities_tables.sql
4

grep -c "CREATE TABLE.*config_" equities_tables.sql
2

grep -c "^CREATE TABLE" equities_tables.sql
20
```

All 20 CREATE TABLE statements (sorted):
```
CREATE TABLE public.ahc_author_history (
CREATE TABLE public.ahc_daily_universe (
CREATE TABLE public.ahc_overnight_features (
CREATE TABLE public.ahc_targets (
CREATE TABLE public.config_active_strategies (
CREATE TABLE public.config_fee_schedule (
CREATE TABLE public.equities_bar_meta (
CREATE TABLE public.equities_calibration_alerts (
CREATE TABLE public.equities_earnings_calendar (
CREATE TABLE public.equities_events (
CREATE TABLE public.equities_feature_events (
CREATE TABLE public.equities_model_evaluations (
CREATE TABLE public.equities_opportunities (
CREATE TABLE public.equities_opportunity_outcomes (
CREATE TABLE public.equities_pipeline_events (
CREATE TABLE public.equities_predictions (
CREATE TABLE public.equities_raw_mentions (
CREATE TABLE public.equities_selection_policies (
CREATE TABLE public.equities_source_cache (
CREATE TABLE public.equities_symbol_registry (
```

stderr: `pg_dump_stderr_20260416_213832.log` is 0 bytes (empty).

## Cross-checks

- **All 18 audit-listed tables present in dump:** every table name in `postgres_audit.md` §Discovery (14 `equities_*` + 4 `ahc_*`) matches a `^CREATE TABLE public.<name> (` line in the dump. Verified by sorting both lists and comparing.
- **`equities_symbol_registry` CREATE TABLE present:** `grep -c "CREATE TABLE public.equities_symbol_registry" equities_tables.sql` → 1. This is the primary keyed registry A2 will depend on; confirming it is in the snapshot means the rollback path is intact for the 70-symbol universe.
- **`config_active_strategies` + `config_fee_schedule` captured:** both the strategy registry (8 equity setups: EQFP, EQ_ATC, EQ_VPD, EQ_REV1A, EQ_REV1B, EQ_TSMOM, EQ_XSMOM, EQ_NVDA_MACRO) and the fee schedule (`eq_default` row) are in the dump. These are the two cross-cutting tables Batch 2 will alter with new equity strategies.
- **Plain-format parseability:** `pg_restore --list` not available on this host (postgresql-client not installed; `Warning: No existing cluster is suitable...`) — expected for a plain-format SQL dump. Used `grep "^CREATE TABLE" | wc -l` → 20 as the table-count check per brief §3 step 4. Plain SQL is directly re-runnable via `cat equities_tables.sql | docker exec -i backend-postgres-1 psql -U trading -d trading` if restore is ever needed.
- **Self-validation thresholds:** dump lines `1,464,960` ≥ `10,000` (brief min), grep count `14` ≥ `14` (brief min), size `3.5 GB` > `100 MB` (brief min). All three green.
- **stderr is empty:** 0 bytes. No permission warnings, no table-missing errors, no serialization errors.

## Gotchas

- **Dump ran in <60 s despite 3.5 GB output.** Fast local UNIX-socket throughput between `backend-postgres-1` and the host; a naive assumption of long runtime for future tables might budget too generously. Still, A1's dump volume is dominated by `equities_feature_events` (1054 MB), `equities_source_cache` (2363 MB), and `equities_events` (182 MB) per the audit. If any of these scales by 10× before a future A1 re-run, the dump will approach 35 GB — ensure destination disk space before running.
- **`ahc_*` tables are NOT in `Backend/docs/db/migrations/`.** Per the audit, the four `ahc_*` schemas were applied via hand-written DDL and are only captured now via this snapshot. If a future recovery relies on recreating them from migrations alone, that step will silently miss `ahc_daily_universe`, `ahc_overnight_features`, `ahc_targets`, `ahc_author_history`. The snapshot is the ONLY governed source of their schema today.
- **`config_active_strategies` and `config_fee_schedule` are cross-asset tables.** The dump captures ALL rows in them, including the crypto strategies and crypto fee rows — not just equity. If a rollback needs to selectively restore only equity rows, the consumer must filter by `asset_class = 'equities'` when replaying. Documented here so future-A1-consumer knows what's in the can.
- **Dump contains table DDL + all row DATA (COPY statements) by default.** At 1.46M lines, the vast majority is `COPY public.<table> ... FROM stdin;` blocks. The dump is functionally a full bulk-export, not a schema-only snapshot. This is the intended A1 behavior (rollback insurance) but means the file is large and not human-readable in one sitting.
- **Brief's step 4 `pg_restore --list` check is not runnable on this host.** postgresql-client package absent; only `docker exec` into `backend-postgres-1` has pg tooling. Used the `grep "^CREATE TABLE" | wc -l` alternative that the brief itself pre-approved. Not a blocker; documented so re-runners aren't surprised when `pg_restore` errors out on a plain-format file anyway.

## Stress-test targets

Not yet exercised — would break on re-run:

- **New table added between A1 snapshots.** If between now and the next A1-equivalent run, a new `equities_X` or `ahc_X` table lands, the current `-t` globs will pick it up automatically — good. But if a NEW table lands outside both globs (e.g. `stocks_fundamentals`, `corporate_actions`, `halts`, `trading_calendar`), A1's hardcoded glob list silently misses it. Re-runners must re-verify the postgres_audit table-discovery step before trusting the dump as complete.
- **Schema drift between dev and prod.** Dump is taken from prod ONLY. Dev (`mcas-dev-postgres-1`) is not snapshotted. If dev drifts ahead of prod (e.g. a future migration applies to dev first per §1.5), a rollback of A1 onto prod could re-introduce an older schema that doesn't match what dev expects. A1 does not attempt a dev snapshot — out of scope per brief.
- **Foreign-key integrity with `opportunities` base table.** `equities_opportunities.legacy_opportunity_id` FKs into `opportunities.id`. The snapshot captures `equities_opportunities` rows with those FK values but NOT the matching `opportunities` rows. A naive full-drop-and-restore of equity tables from this dump could leave dangling FKs pointing at `opportunities.id` values that have since been deleted. Rollback consumer must either (a) restore in-place with `ON CONFLICT DO NOTHING` (preserves existing crypto `opportunities`), or (b) coordinate with a paired `opportunities` snapshot.
- **Equity OHLCV rows in the shared `ohlcv` table are NOT in this dump.** 341,893 equity OHLCV rows live in `public.ohlcv` keyed by `source IN ('equities_yahoo', 'yahoo_chart')`. A1's table-glob filters exclude `ohlcv`. If a rollback scenario requires equity OHLCV, a separate `pg_dump -t ohlcv --data-only --where="source IN ('equities_yahoo','yahoo_chart')"` would be needed. Out of scope for A1 as written; flagging for the plan owner.

## Artifacts referenced by this findings.md

- `pg_dump_stdout_20260416_213832.log` — note about stdout being the dump file itself; no separate stdout stream.
- `pg_dump_stderr_20260416_213832.log` — 0 bytes, empty (copied from snapshot dir).
- `verification_queries_20260416_213832.txt` — full captured output of all `wc -l`, `du -h`, `du -b`, `grep -c`, and CREATE TABLE listing runs.
- `reproduction.md` — copy-pasteable commands to re-run the snapshot cold.
- Snapshot itself: `Backend/scripts/output/snapshots/equities_pre_backfill_20260416_213832/equities_tables.sql` (3.5 GB).
