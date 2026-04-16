# A1 — Reproduce snapshot cold

## Prereqs
- `backend-postgres-1` container running (`docker ps | grep backend-postgres-1`).
- Working directory: `/home/darren/Desktop/MCAS/`.
- Destination disk has ≥ 5 GB free (dump is ~3.5 GB).

## Commands (copy-paste)

```bash
cd /home/darren/Desktop/MCAS

# 1. Create timestamped snapshot dir
UTC_TS=$(date -u +%Y%m%d_%H%M%S)
SNAP_DIR="Backend/scripts/output/snapshots/equities_pre_backfill_${UTC_TS}"
mkdir -p "$SNAP_DIR"

# 2. Run pg_dump — plain SQL, no owner/ACL
docker exec backend-postgres-1 pg_dump -U trading -d trading \
  -t 'equities_*' \
  -t 'ahc_*' \
  -t 'config_active_strategies' \
  -t 'config_fee_schedule' \
  --no-owner --no-acl \
  > "${SNAP_DIR}/equities_tables.sql" \
  2> "${SNAP_DIR}/pg_dump_stderr_${UTC_TS}.log"

# 3. Verify (all three must pass)
wc -l "${SNAP_DIR}/equities_tables.sql"                           # expect >= 10000
du -h  "${SNAP_DIR}/equities_tables.sql"                          # expect > 100 MB (actual ~3.5G)
grep -c "CREATE TABLE.*equities_" "${SNAP_DIR}/equities_tables.sql"  # expect >= 14
grep -c "^CREATE TABLE" "${SNAP_DIR}/equities_tables.sql"            # expect 20 (14 equities_ + 4 ahc_ + 2 config_)

# 4. Confirm stderr is empty
test ! -s "${SNAP_DIR}/pg_dump_stderr_${UTC_TS}.log" && echo STDERR_EMPTY_OK || echo STDERR_NOT_EMPTY
```

## Rollback (restore from snapshot)

```bash
# Restore plain-format SQL dump back into the same cluster
cat "${SNAP_DIR}/equities_tables.sql" | docker exec -i backend-postgres-1 psql -U trading -d trading
```

Note: the dump contains `CREATE TABLE` + `COPY ... FROM stdin` statements. If tables already exist, you'll see "relation already exists" errors and COPY will append/conflict. For a clean restore, drop the tables first (destructive — coordinate with ops).

## Delete (revert snapshot)

```bash
rm -rf "${SNAP_DIR}"
```
