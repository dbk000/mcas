# Full Codebase Doctrine Audit — 2026-03-14
## Post-fix status. Each item marked FIXED or REMAINING.

---

## CRITICAL: Hardcoded Setup IDs (Second Authorities)

### run_executor.py — FIXED
- **Lines 752-1019**: 270-line chain → evaluators now keyed by `evaluator.metadata.id`, filtered against registry-derived `active_setup_ids`. No hardcoded string dispatch.
- **Lines 88-115**: if/elif chain → replaced with generic fallback (S1/S2 branches deleted).
- **Line 234**: Frozen default list → derived from `StrategyRegistry.as_api_dict()`.
- **Line 425**: Same → derived from registry catalog.
- **Line 475**: Counter dict → derived from `active_setup_ids`.
- **Lines 1608, 1692**: `set(setup_ids) == {"D1"}` → uses `_d1_ids` derived from registry catalog.

### signal_worker.py — FIXED
- **SETUP_SCORE_MULTIPLIERS**: Deleted. Callers use `StrategyRegistry.get_multiplier()`.
- **NODE_NAMES**: Derived from `StrategyRegistry.as_api_dict()`.
- **evaluators list**: Derived from `StrategyRegistry._strategies`.
- **_BOOTSTRAP_METADATA**: Deleted. Each evaluator provides `get_bootstrap_metadata()`.
- **D1-only hot reload**: Generic loop over all registered strategies with `update_config`.
- **Hardcoded iteration lists**: Use `StrategyRegistry.active_setup_ids()`.

### opportunity_simulator.py — FIXED
- **SETUP_EXIT_CONFIGS**: Marked `Final` (configuration constant per doctrine).
- **D1 conditional**: Now governed by `config.d1_causal_gate_enabled` flag, not string comparison.

### signal_engine.py — FIXED
- **_SETUP_NAMES**: Replaced with `_get_setup_name()` deriving from `StrategyRegistry.as_api_dict()`.
- **isinstance chain**: Retained (structural dispatch on genuinely different signal types — not ID-based).
- **Hardcoded scan list**: Replaced with `StrategyRegistry.active_setup_ids()`.

### stop_policy_registry.py — FIXED
- **KNOWN_SETUP_IDS**: Derived from `StrategyRegistry.as_api_dict().keys()`.

### paper_trading.py — FIXED
- **S2 conditional**: Removed. Now reads `time_stop_bars` from signal payload.

### api/app/routers/control.py — FIXED
- **3 Pydantic models**: Defaults changed to `None`, resolved to `StrategyRegistry.active_setup_ids()` in handlers.

### api/app/main.py — FIXED
- **SignalRequest.setup_id**: Changed from `"S1"` default to required field.

---

## CRITICAL: Import Direction / core_logic Purity Violations

### core_logic/d1_dormancy_ignition.py — FIXED
- Governance import removed from `update_config()`. Method now works purely with core_logic state. Registry sync moved to `governance/signal_worker.py:_sync_typed_config_registry()`.

### core_logic/microstructure.py — FIXED
- HTTP I/O (`requests`, `ThreadPoolExecutor`, `fetch_1s_klines`, `fetch_1s_klines_batch`) moved to `governance/microstructure_fetcher.py`. core_logic retains only pure math.

### core_logic/opportunity_simulator.py — FIXED
- `import psycopg` removed (was unused — all DB access via injected `_pool_get_conn`).

---

## CRITICAL: Root Shim Violations

### registry.py — FIXED
- `ConfigRegistry` class moved to `governance/config_registry.py`. Root file is 2-line re-export shim.

### dxy_regime.py — FIXED
- Logic moved to `governance/dxy_regime.py`. `DXY_REGIME_ENABLED` added to `WorkerTypedConfig`. Root file is 3-line re-export shim.

### config_reader.py — FIXED
- Dead code removed. Now 1-line empty shim.

### db_pool.py — FIXED
- Function definition replaced with attribute binding. Now 4-line re-export shim.

---

## HIGH: SQL Violations

### Half-open range violations — FIXED (all 9 sites)
- `data_layer.py`: 4 sites
- `run_executor.py`: 1 site
- `api/app/main.py`: 1 site
- `expired_opportunity_exit_settler.py`: 1 site
- `equities_predictor/db.py`: 2 sites

### INTERVAL format violations — FIXED (all 4 sites)
- `ohlcv_persister.py`: 2 sites
- `signal_worker.py`: 1 site (also fixed SQL injection risk)
- `run_executor.py`: 1 site

### Per-row SQL loops — REMAINING (6 sites, documented for follow-up)
- `api/app/routers/meta.py`: 4 sites (low cardinality, 5-15 rows typical)
- `scripts/expired_opportunity_exit_settler.py`: 1 site (up to 5000 rows)
- `governance/price_feed.py`: 1 site

---

## HIGH: Functions That Do Nothing — FIXED (all 3)

- `cc/simulator.py:age_pending_signals()` → raises `NotImplementedError`
- `s9_short_reversion.py:compute_state()` → raises `NotImplementedError`
- `s9_short_reversion.py:evaluate_trigger()` → raises `NotImplementedError`

---

## HIGH: Hardcoded Constants Duplicated Across Files

### Binance API URL — FIXED
- bootstrap.py has canonical defaults
- Non-entry-points read from TypedConfigRegistry
- Entry-points read from env vars with BOOTSTRAP_BOUNDARY
- Zero hardcoded URLs remain in governance/ or core_logic/

### XREAD_BLOCK_MS — NO FIX NEEDED
- Constant (5000) across 3 files. Per doctrine: "not from a registry → it's a constant."

### COMPUTE_WORKERS, HTTP timeouts — REMAINING (low priority)
- Duplicated but stable constants. Document for future TypedConfigRegistry migration.

---

## MEDIUM: Dynamic Dispatch Remaining — REMAINING

### Timestamp duck-typing (hasattr) — ~20 sites
Root cause: timestamps are polymorphic (datetime|int|str). Fixing requires canonicalizing timestamp types across the codebase. Tracked for follow-up.

### Signal field access (getattr) — 9 sites
Root cause: no common signal protocol/interface. Would require a SignalProtocol ABC.

---

## MEDIUM: Module-level os.getenv in Non-Entry-Point Files — REMAINING (7 sites)
All marked BOOTSTRAP_BOUNDARY. Full migration to TypedConfigRegistry requires lazy-init pattern across the codebase. Tracked for follow-up.

---

## LOW: indicator_bundles.py — REMAINING
`BUNDLE_REGISTRY` is a declarative specification, not dispatch. Low priority.

## LOW: indicators/__init__.py — REMAINING
131 lines of orchestration logic. Low priority.

---

## FINAL SUMMARY

| Category | Total Sites | Fixed | Remaining |
|----------|-------------|-------|-----------|
| Hardcoded Setup IDs | ~30 | 30 | 0 |
| Import Direction / core_logic purity | 3 | 3 | 0 |
| Root Shim Violations | 4 | 4 | 0 |
| SQL Half-open ranges | 9 | 9 | 0 |
| SQL INTERVAL format | 4 | 4 | 0 |
| SQL Per-row loops | 6 | 0 | 6 |
| Empty functions | 3 | 3 | 0 |
| Binance URL duplication | 8 | 8 | 0 |
| Duplicated constants | ~5 | 1 | 4 |
| Timestamp duck-typing | ~20 | 0 | ~20 |
| Signal getattr | 9 | 0 | 9 |
| os.getenv bypass | 7 | 0 | 7 |
| indicator_bundles/init | 2 | 0 | 2 |
| **TOTAL** | **~110** | **~62** | **~48** |

**All CRITICAL and most HIGH violations are fixed.**
Remaining items are MEDIUM/LOW — structural patterns that require broader refactors (timestamp canonicalization, signal protocol, lazy config init).
