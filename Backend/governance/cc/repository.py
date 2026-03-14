"""
Champion/Challenger Framework - Repository
==========================================

Database access layer for C/C tables.

All database operations are transactional and follow the patterns
established in runs/repository.py.

Schema is governed by migration file, not by this module.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Optional
from contextlib import contextmanager

import psycopg

from governance.persistence import get_conn as _pool_get_conn

from .models import (
    Variant, VariantStatus, VariantModule,
    Module, ModuleKind, DiffType,
    ExperimentRun, ExperimentMode, ExperimentStatus,
    ShadowTrade, ShadowPosition, PendingSignal,
    RunMetrics, AssetMetrics,
    TradeDiagnostic, FailureBucket, WinnerBucket,
    ChangeLedgerEntry,
    Direction,
)

logger = logging.getLogger(__name__)


class CCRepository:
    """
    Handles all database operations for Champion/Challenger framework.

    Usage:
        repo = CCRepository(database_url)

        # Variants
        repo.create_variant(variant)
        repo.get_variant(variant_id)
        repo.update_variant_status(variant_id, status)

        # Experiment runs
        repo.create_experiment_run(run)
        repo.get_experiment_run(run_id)
        repo.close_experiment_run(run_id)

        # Trades
        repo.save_trade(run_id, variant_id, trade)
        repo.get_trades(run_id, variant_id)

        # Change ledger
        repo.record_change(setup_id, variant_id, change_type)
        repo.get_last_change(setup_id)
    """

    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def _conn(self):
        """Context manager for database connections from the pool."""
        with _pool_get_conn() as conn:
            yield conn

    @contextmanager
    def _transaction(self):
        """Context manager for transactions with commit/rollback."""
        with _pool_get_conn() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # =========================================================================
    # MODULES
    # =========================================================================

    def create_module(self, module: Module) -> Module:
        """Create a module definition."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_modules (
                        module_id, module_kind, version,
                        implementation_ref, config_schema, description
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (module_id) DO UPDATE
                    SET module_kind = EXCLUDED.module_kind,
                        version = EXCLUDED.version,
                        implementation_ref = EXCLUDED.implementation_ref,
                        config_schema = EXCLUDED.config_schema,
                        description = EXCLUDED.description
                    RETURNING module_id;
                """, (
                    module.module_id,
                    module.module_kind.value,
                    module.version,
                    module.implementation_ref,
                    json.dumps(module.config_schema) if module.config_schema else None,
                    module.description,
                ))
                cur.fetchone()

        logger.info(f"Created/updated module {module.module_id}")
        return module

    def get_module(self, module_id: str) -> Optional[Module]:
        """Get a module by ID."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT module_id, module_kind, version,
                           implementation_ref, config_schema, description, created_at
                    FROM cc_modules WHERE module_id = %s;
                """, (module_id,))
                row = cur.fetchone()

        if not row:
            return None

        return Module(
            module_id=row[0],
            module_kind=ModuleKind(row[1]),
            version=row[2],
            implementation_ref=row[3],
            config_schema=row[4],
            description=row[5],
            created_at=row[6],
        )

    # =========================================================================
    # VARIANTS
    # =========================================================================

    def create_variant(self, variant: Variant) -> Variant:
        """Create a new variant."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                # Insert variant
                cur.execute("""
                    INSERT INTO cc_variants (
                        variant_id, setup_id, status,
                        parent_variant_id,
                        diff_type, diff_module_kind, diff_key, diff_description,
                        version_major, version_minor, version_patch,
                        created_by, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING variant_id;
                """, (
                    variant.variant_id,
                    variant.setup_id,
                    variant.status.value,
                    variant.parent_variant_id,
                    variant.diff_type.value if variant.diff_type else None,
                    variant.diff_module_kind.value if variant.diff_module_kind else None,
                    variant.diff_key,
                    variant.diff_description,
                    variant.version_major,
                    variant.version_minor,
                    variant.version_patch,
                    variant.created_by,
                    variant.notes,
                ))
                cur.fetchone()

                # Insert module mappings
                for module_kind, vm in variant.modules.items():
                    cur.execute("""
                        INSERT INTO cc_variant_modules (
                            variant_id, module_kind, module_id, module_params
                        ) VALUES (%s, %s, %s, %s);
                    """, (
                        variant.variant_id,
                        vm.module_kind.value,
                        vm.module_id,
                        json.dumps(vm.params),
                    ))

        logger.info(f"Created variant {variant.variant_id}")
        return variant

    def get_variant(self, variant_id: str) -> Optional[Variant]:
        """Get a variant by ID with its module map."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                # Get variant
                cur.execute("""
                    SELECT variant_id, setup_id, status,
                           parent_variant_id,
                           diff_type, diff_module_kind, diff_key, diff_description,
                           version_major, version_minor, version_patch,
                           created_at, created_by, notes
                    FROM cc_variants WHERE variant_id = %s;
                """, (variant_id,))
                row = cur.fetchone()

                if not row:
                    return None

                variant = Variant(
                    variant_id=row[0],
                    setup_id=row[1],
                    status=VariantStatus(row[2]),
                    parent_variant_id=row[3],
                    diff_type=DiffType(row[4]) if row[4] else None,
                    diff_module_kind=ModuleKind(row[5]) if row[5] else None,
                    diff_key=row[6],
                    diff_description=row[7],
                    version_major=row[8],
                    version_minor=row[9],
                    version_patch=row[10],
                    created_at=row[11],
                    created_by=row[12],
                    notes=row[13],
                )

                # Get module mappings
                cur.execute("""
                    SELECT module_kind, module_id, module_params
                    FROM cc_variant_modules WHERE variant_id = %s;
                """, (variant_id,))
                for mod_row in cur.fetchall():
                    vm = VariantModule(
                        module_kind=ModuleKind(mod_row[0]),
                        module_id=mod_row[1],
                        params=mod_row[2] if mod_row[2] else {},
                    )
                    variant.modules[mod_row[0]] = vm

        return variant

    def update_variant_status(self, variant_id: str, status: VariantStatus) -> bool:
        """Update a variant's status."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE cc_variants SET status = %s
                    WHERE variant_id = %s;
                """, (status.value, variant_id))
                updated = cur.rowcount > 0

        if updated:
            logger.info(f"Updated variant {variant_id} status to {status.value}")
        return updated

    def get_champion(self, setup_id: str) -> Optional[Variant]:
        """Get the current champion for a setup."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT variant_id FROM cc_variants
                    WHERE setup_id = %s AND status = 'CHAMPION'
                    ORDER BY created_at DESC LIMIT 1;
                """, (setup_id,))
                row = cur.fetchone()

        if not row:
            return None

        return self.get_variant(row[0])

    def get_baseline(self, setup_id: str) -> Optional[Variant]:
        """Get the baseline for a setup."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT variant_id FROM cc_variants
                    WHERE setup_id = %s AND status = 'BASELINE'
                    ORDER BY created_at DESC LIMIT 1;
                """, (setup_id,))
                row = cur.fetchone()

        if not row:
            return None

        return self.get_variant(row[0])

    def get_active_challenger(self, setup_id: str) -> Optional[Variant]:
        """Get the active challenger for a setup (if any)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT variant_id FROM cc_variants
                    WHERE setup_id = %s AND status = 'CHALLENGER'
                    ORDER BY created_at DESC LIMIT 1;
                """, (setup_id,))
                row = cur.fetchone()

        if not row:
            return None

        return self.get_variant(row[0])

    def list_variants(
        self,
        setup_id: Optional[str] = None,
        status: Optional[VariantStatus] = None,
        limit: int = 50,
    ) -> list[Variant]:
        """List variants with optional filters."""
        conditions = []
        params = []

        if setup_id:
            conditions.append("setup_id = %s")
            params.append(setup_id)
        if status:
            conditions.append("status = %s")
            params.append(status.value)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT variant_id FROM cc_variants
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s;
                """, params + [limit])
                rows = cur.fetchall()

        return [self.get_variant(row[0]) for row in rows if row[0]]

    def get_variant_module_map_snapshot(self, variant_id: str) -> dict:
        """Get a frozen snapshot of a variant's module map."""
        variant = self.get_variant(variant_id)
        if not variant:
            return {}

        return {
            "variant_id": variant.variant_id,
            "setup_id": variant.setup_id,
            "modules": {k: v.to_dict() for k, v in variant.modules.items()},
        }

    # =========================================================================
    # EXPERIMENT RUNS
    # =========================================================================

    def create_experiment_run(self, run: ExperimentRun) -> ExperimentRun:
        """Create a new experiment run."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_experiment_runs (
                        run_id,
                        baseline_variant_id, champion_variant_id, challenger_variant_id,
                        baseline_module_map_snapshot, champion_module_map_snapshot, challenger_module_map_snapshot,
                        mode, split_baseline_pct, split_champion_pct, split_challenger_pct,
                        setup_id, asset_universe_snapshot, timeframes,
                        execution_spec_snapshot,
                        status, start_ts, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING run_id;
                """, (
                    run.run_id,
                    run.baseline_variant_id,
                    run.champion_variant_id,
                    run.challenger_variant_id,
                    json.dumps(run.baseline_module_map_snapshot) if run.baseline_module_map_snapshot else None,
                    json.dumps(run.champion_module_map_snapshot),
                    json.dumps(run.challenger_module_map_snapshot),
                    run.mode.value,
                    run.split_baseline_pct,
                    run.split_champion_pct,
                    run.split_challenger_pct,
                    run.setup_id,
                    json.dumps(run.asset_universe_snapshot) if run.asset_universe_snapshot else None,
                    run.timeframes,
                    json.dumps(run.execution_spec_snapshot),
                    run.status.value,
                    run.start_ts,
                    run.notes,
                ))
                cur.fetchone()

        logger.info(f"Created experiment run {run.run_id}")
        return run

    def get_experiment_run(self, run_id: str) -> Optional[ExperimentRun]:
        """Get an experiment run by ID."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_id,
                           baseline_variant_id, champion_variant_id, challenger_variant_id,
                           baseline_module_map_snapshot, champion_module_map_snapshot, challenger_module_map_snapshot,
                           mode, split_baseline_pct, split_champion_pct, split_challenger_pct,
                           setup_id, asset_universe_snapshot, timeframes,
                           execution_spec_snapshot,
                           status, start_ts, end_ts, notes, created_at
                    FROM cc_experiment_runs WHERE run_id = %s;
                """, (run_id,))
                row = cur.fetchone()

        if not row:
            return None

        return self._row_to_experiment_run(row)

    def _row_to_experiment_run(self, row: tuple) -> ExperimentRun:
        """Convert a database row to an ExperimentRun."""
        return ExperimentRun(
            run_id=str(row[0]),
            baseline_variant_id=row[1],
            champion_variant_id=row[2],
            challenger_variant_id=row[3],
            baseline_module_map_snapshot=row[4],
            champion_module_map_snapshot=row[5],
            challenger_module_map_snapshot=row[6],
            mode=ExperimentMode(row[7]),
            split_baseline_pct=row[8],
            split_champion_pct=row[9],
            split_challenger_pct=row[10],
            setup_id=row[11],
            asset_universe_snapshot=row[12],
            timeframes=row[13] if row[13] else [],
            execution_spec_snapshot=row[14],
            status=ExperimentStatus(row[15]),
            start_ts=row[16],
            end_ts=row[17],
            notes=row[18],
            created_at=row[19],
        )

    def get_active_run(self, setup_id: str) -> Optional[ExperimentRun]:
        """Get the active experiment run for a setup."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_id,
                           baseline_variant_id, champion_variant_id, challenger_variant_id,
                           baseline_module_map_snapshot, champion_module_map_snapshot, challenger_module_map_snapshot,
                           mode, split_baseline_pct, split_champion_pct, split_challenger_pct,
                           setup_id, asset_universe_snapshot, timeframes,
                           execution_spec_snapshot,
                           status, start_ts, end_ts, notes, created_at
                    FROM cc_experiment_runs
                    WHERE setup_id = %s AND status = 'RUNNING'
                    ORDER BY start_ts DESC LIMIT 1;
                """, (setup_id,))
                row = cur.fetchone()

        if not row:
            return None

        return self._row_to_experiment_run(row)

    def list_experiment_runs(
        self,
        status: Optional[ExperimentStatus] = None,
        setup_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[ExperimentRun]:
        """List experiment runs with optional filters."""
        conditions = []
        params = []

        if status:
            conditions.append("status = %s")
            params.append(status.value)
        if setup_id:
            conditions.append("setup_id = %s")
            params.append(setup_id)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT run_id,
                           baseline_variant_id, champion_variant_id, challenger_variant_id,
                           baseline_module_map_snapshot, champion_module_map_snapshot, challenger_module_map_snapshot,
                           mode, split_baseline_pct, split_champion_pct, split_challenger_pct,
                           setup_id, asset_universe_snapshot, timeframes,
                           execution_spec_snapshot,
                           status, start_ts, end_ts, notes, created_at
                    FROM cc_experiment_runs
                    {where_clause}
                    ORDER BY start_ts DESC
                    LIMIT %s;
                """, params + [limit])
                rows = cur.fetchall()

        return [self._row_to_experiment_run(row) for row in rows]

    def update_experiment_run_status(
        self,
        run_id: str,
        status: ExperimentStatus,
        end_ts: Optional[datetime] = None,
    ) -> bool:
        """Update an experiment run's status."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                if end_ts:
                    cur.execute("""
                        UPDATE cc_experiment_runs
                        SET status = %s, end_ts = %s
                        WHERE run_id = %s;
                    """, (status.value, end_ts, run_id))
                else:
                    cur.execute("""
                        UPDATE cc_experiment_runs
                        SET status = %s
                        WHERE run_id = %s;
                    """, (status.value, run_id))
                updated = cur.rowcount > 0

        if updated:
            logger.info(f"Updated experiment run {run_id} status to {status.value}")
        return updated

    def lock_run_for_promotion(self, run_id: str) -> Optional[ExperimentRun]:
        """
        Lock a run for promotion (FOR UPDATE NOWAIT).

        Returns the locked run if successful, None if already locked or not found.
        """
        with self._conn() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("""
                        SELECT run_id,
                               baseline_variant_id, champion_variant_id, challenger_variant_id,
                               baseline_module_map_snapshot, champion_module_map_snapshot, challenger_module_map_snapshot,
                               mode, split_baseline_pct, split_champion_pct, split_challenger_pct,
                               setup_id, asset_universe_snapshot, timeframes,
                               execution_spec_snapshot,
                               status, start_ts, end_ts, notes, created_at
                        FROM cc_experiment_runs
                        WHERE run_id = %s
                        FOR UPDATE NOWAIT;
                    """, (run_id,))
                    row = cur.fetchone()
                except psycopg.errors.LockNotAvailable:
                    logger.warning(f"Run {run_id} is locked by another process")
                    return None

        if not row:
            return None

        return self._row_to_experiment_run(row)

    # =========================================================================
    # TRADES
    # =========================================================================

    def save_trade(self, run_id: str, variant_id: str, trade: ShadowTrade) -> ShadowTrade:
        """Save a shadow trade."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_experiment_trades (
                        id, run_id, variant_id,
                        symbol, timeframe, signal_ts, signal_bar_ts,
                        entry_ts, entry_price, entry_price_with_slippage,
                        exit_ts, exit_price, exit_price_with_slippage, exit_reason,
                        r_multiple, pnl_gross, pnl_net, fees_paid, bars_held,
                        mae, mfe,
                        was_executed, execution_id, trace_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        exit_ts = EXCLUDED.exit_ts,
                        exit_price = EXCLUDED.exit_price,
                        exit_price_with_slippage = EXCLUDED.exit_price_with_slippage,
                        exit_reason = EXCLUDED.exit_reason,
                        r_multiple = EXCLUDED.r_multiple,
                        pnl_gross = EXCLUDED.pnl_gross,
                        pnl_net = EXCLUDED.pnl_net,
                        fees_paid = EXCLUDED.fees_paid,
                        bars_held = EXCLUDED.bars_held,
                        mae = EXCLUDED.mae,
                        mfe = EXCLUDED.mfe;
                """, (
                    trade.trade_id, run_id, variant_id,
                    trade.symbol, trade.timeframe, trade.signal_ts, trade.signal_bar_ts,
                    trade.entry_ts, trade.entry_price, trade.entry_price_with_slippage,
                    trade.exit_ts, trade.exit_price, trade.exit_price_with_slippage, trade.exit_reason,
                    trade.r_multiple, trade.pnl_gross, trade.pnl_net, trade.fees_paid, trade.bars_held,
                    trade.mae, trade.mfe,
                    trade.was_executed, trade.execution_id,
                    json.dumps(trade.trace_json) if trade.trace_json else None,
                ))

        return trade

    def get_trades(
        self,
        run_id: str,
        variant_id: Optional[str] = None,
        limit: int = 100,
    ) -> list[ShadowTrade]:
        """Get trades for a run, optionally filtered by variant."""
        conditions = ["run_id = %s"]
        params = [run_id]

        if variant_id:
            conditions.append("variant_id = %s")
            params.append(variant_id)

        where_clause = " AND ".join(conditions)

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, run_id, variant_id,
                           symbol, timeframe, signal_ts, signal_bar_ts,
                           entry_ts, entry_price, entry_price_with_slippage,
                           exit_ts, exit_price, exit_price_with_slippage, exit_reason,
                           r_multiple, pnl_gross, pnl_net, fees_paid, bars_held,
                           mae, mfe,
                           was_executed, execution_id, trace_json, created_at
                    FROM cc_experiment_trades
                    WHERE {where_clause}
                    ORDER BY signal_ts DESC
                    LIMIT %s;
                """, params + [limit])
                rows = cur.fetchall()

        return [self._row_to_trade(row) for row in rows]

    def _row_to_trade(self, row: tuple) -> ShadowTrade:
        """Convert a database row to a ShadowTrade."""
        return ShadowTrade(
            trade_id=str(row[0]),
            symbol=row[3],
            timeframe=row[4],
            direction=Direction.LONG,  # TODO: Store direction in DB
            signal_ts=row[5],
            signal_bar_ts=row[6],
            entry_ts=row[7],
            entry_price=row[8],
            entry_price_with_slippage=row[9],
            exit_ts=row[10],
            exit_price=row[11],
            exit_price_with_slippage=row[12],
            exit_reason=row[13],
            r_multiple=row[14] or 0.0,
            pnl_gross=row[15] or 0.0,
            pnl_net=row[16] or 0.0,
            fees_paid=row[17] or 0.0,
            bars_held=row[18] or 0,
            mae=row[19] or 0.0,
            mfe=row[20] or 0.0,
            was_executed=row[21] or False,
            execution_id=row[22],
            trace_json=row[23],
            created_at=row[24],
        )

    # =========================================================================
    # METRICS
    # =========================================================================

    def save_run_metrics(self, metrics: RunMetrics) -> RunMetrics:
        """Save aggregated metrics for a variant in a run."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_run_metrics (
                        run_id, variant_id,
                        trade_count, win_count, loss_count,
                        expectancy, total_r, avg_r,
                        gross_pnl, net_pnl, total_fees,
                        max_drawdown_pct, win_rate, profit_factor
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, variant_id) DO UPDATE SET
                        trade_count = EXCLUDED.trade_count,
                        win_count = EXCLUDED.win_count,
                        loss_count = EXCLUDED.loss_count,
                        expectancy = EXCLUDED.expectancy,
                        total_r = EXCLUDED.total_r,
                        avg_r = EXCLUDED.avg_r,
                        gross_pnl = EXCLUDED.gross_pnl,
                        net_pnl = EXCLUDED.net_pnl,
                        total_fees = EXCLUDED.total_fees,
                        max_drawdown_pct = EXCLUDED.max_drawdown_pct,
                        win_rate = EXCLUDED.win_rate,
                        profit_factor = EXCLUDED.profit_factor,
                        computed_at = NOW();
                """, (
                    metrics.run_id, metrics.variant_id,
                    metrics.trade_count, metrics.win_count, metrics.loss_count,
                    metrics.expectancy, metrics.total_r, metrics.avg_r,
                    metrics.gross_pnl, metrics.net_pnl, metrics.total_fees,
                    metrics.max_drawdown_pct, metrics.win_rate, metrics.profit_factor,
                ))

        return metrics

    def get_run_metrics(self, run_id: str) -> dict[str, RunMetrics]:
        """Get metrics for all variants in a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_id, variant_id,
                           trade_count, win_count, loss_count,
                           expectancy, total_r, avg_r,
                           gross_pnl, net_pnl, total_fees,
                           max_drawdown_pct, win_rate, profit_factor, computed_at
                    FROM cc_run_metrics WHERE run_id = %s;
                """, (run_id,))
                rows = cur.fetchall()

        result = {}
        for row in rows:
            metrics = RunMetrics(
                run_id=str(row[0]),
                variant_id=row[1],
                trade_count=row[2],
                win_count=row[3],
                loss_count=row[4],
                expectancy=row[5],
                total_r=row[6],
                avg_r=row[7],
                gross_pnl=row[8],
                net_pnl=row[9],
                total_fees=row[10],
                max_drawdown_pct=row[11],
                win_rate=row[12],
                profit_factor=row[13],
                computed_at=row[14],
            )
            result[metrics.variant_id] = metrics

        return result

    def save_asset_metrics(self, metrics: AssetMetrics) -> AssetMetrics:
        """Save per-asset metrics."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_run_metrics_by_asset (
                        run_id, variant_id, symbol,
                        trade_count, expectancy, total_r, win_rate, max_drawdown_pct
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, variant_id, symbol) DO UPDATE SET
                        trade_count = EXCLUDED.trade_count,
                        expectancy = EXCLUDED.expectancy,
                        total_r = EXCLUDED.total_r,
                        win_rate = EXCLUDED.win_rate,
                        max_drawdown_pct = EXCLUDED.max_drawdown_pct;
                """, (
                    metrics.run_id, metrics.variant_id, metrics.symbol,
                    metrics.trade_count, metrics.expectancy, metrics.total_r,
                    metrics.win_rate, metrics.max_drawdown_pct,
                ))

        return metrics

    def get_asset_metrics(self, run_id: str, variant_id: str) -> list[AssetMetrics]:
        """Get per-asset metrics for a variant in a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_id, variant_id, symbol,
                           trade_count, expectancy, total_r, win_rate, max_drawdown_pct
                    FROM cc_run_metrics_by_asset
                    WHERE run_id = %s AND variant_id = %s;
                """, (run_id, variant_id))
                rows = cur.fetchall()

        return [
            AssetMetrics(
                run_id=str(row[0]),
                variant_id=row[1],
                symbol=row[2],
                trade_count=row[3],
                expectancy=row[4],
                total_r=row[5],
                win_rate=row[6],
                max_drawdown_pct=row[7],
            )
            for row in rows
        ]

    def get_all_asset_metrics(self, run_id: str) -> dict[str, list[AssetMetrics]]:
        """Get per-asset metrics for all variants in a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_id, variant_id, symbol,
                           trade_count, expectancy, total_r, win_rate, max_drawdown_pct
                    FROM cc_run_metrics_by_asset
                    WHERE run_id = %s;
                """, (run_id,))
                rows = cur.fetchall()

        result: dict[str, list[AssetMetrics]] = {}
        for row in rows:
            am = AssetMetrics(
                run_id=str(row[0]),
                variant_id=row[1],
                symbol=row[2],
                trade_count=row[3],
                expectancy=row[4],
                total_r=row[5],
                win_rate=row[6],
                max_drawdown_pct=row[7],
            )
            if am.variant_id not in result:
                result[am.variant_id] = []
            result[am.variant_id].append(am)

        return result

    # =========================================================================
    # DIAGNOSTICS
    # =========================================================================

    def save_diagnostic(self, diagnostic: TradeDiagnostic) -> TradeDiagnostic:
        """Save a trade diagnostic."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_trade_diagnostics (
                        trade_id, variant_id, run_id, is_winner,
                        failure_bucket, winner_bucket,
                        notes, hypothesis_module_kind
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
                """, (
                    diagnostic.trade_id,
                    diagnostic.variant_id,
                    diagnostic.run_id,
                    diagnostic.is_winner,
                    diagnostic.failure_bucket.value if diagnostic.failure_bucket else None,
                    diagnostic.winner_bucket.value if diagnostic.winner_bucket else None,
                    diagnostic.notes,
                    diagnostic.hypothesis_module_kind.value if diagnostic.hypothesis_module_kind else None,
                ))

        return diagnostic

    def get_loser_bucket_counts(self, run_id: str) -> dict[str, int]:
        """Get failure bucket counts for a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT failure_bucket, COUNT(*) FROM cc_trade_diagnostics
                    WHERE run_id = %s AND is_winner = false AND failure_bucket IS NOT NULL
                    GROUP BY failure_bucket;
                """, (run_id,))
                rows = cur.fetchall()

        return {row[0]: row[1] for row in rows}

    def get_winner_bucket_counts(self, run_id: str) -> dict[str, int]:
        """Get winner bucket counts for a run."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT winner_bucket, COUNT(*) FROM cc_trade_diagnostics
                    WHERE run_id = %s AND is_winner = true AND winner_bucket IS NOT NULL
                    GROUP BY winner_bucket;
                """, (run_id,))
                rows = cur.fetchall()

        return {row[0]: row[1] for row in rows}

    # =========================================================================
    # SHADOW POSITIONS (Postgres backup for Redis state)
    # =========================================================================

    def save_shadow_position(self, position: ShadowPosition) -> ShadowPosition:
        """Save a shadow position (Redis backup)."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_shadow_positions (
                        run_id, variant_id, symbol, timeframe,
                        trade_id, direction,
                        entry_ts, entry_price, entry_price_with_slippage,
                        stop_price, tp1_price, r_value,
                        bars_held, half_r_touched, mae, mfe,
                        pending_exit_reason, pending_exit_flagged_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, variant_id, symbol, timeframe) DO UPDATE SET
                        trade_id = EXCLUDED.trade_id,
                        direction = EXCLUDED.direction,
                        entry_ts = EXCLUDED.entry_ts,
                        entry_price = EXCLUDED.entry_price,
                        entry_price_with_slippage = EXCLUDED.entry_price_with_slippage,
                        stop_price = EXCLUDED.stop_price,
                        tp1_price = EXCLUDED.tp1_price,
                        r_value = EXCLUDED.r_value,
                        bars_held = EXCLUDED.bars_held,
                        half_r_touched = EXCLUDED.half_r_touched,
                        mae = EXCLUDED.mae,
                        mfe = EXCLUDED.mfe,
                        pending_exit_reason = EXCLUDED.pending_exit_reason,
                        pending_exit_flagged_at = EXCLUDED.pending_exit_flagged_at,
                        updated_at = NOW();
                """, (
                    position.run_id, position.variant_id, position.symbol, position.timeframe,
                    position.trade_id, position.direction.value,
                    position.entry_ts, position.entry_price, position.entry_price_with_slippage,
                    position.stop_price, position.tp1_price, position.r_value,
                    position.bars_held, position.half_r_touched, position.mae, position.mfe,
                    position.pending_exit.exit_reason if position.pending_exit else None,
                    datetime.now(timezone.utc) if position.pending_exit else None,
                ))

        return position

    def get_shadow_position(
        self,
        run_id: str,
        variant_id: str,
        symbol: str,
        timeframe: str,
    ) -> Optional[ShadowPosition]:
        """Get a shadow position from Postgres (fallback if Redis miss)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_id, variant_id, symbol, timeframe,
                           trade_id, direction,
                           entry_ts, entry_price, entry_price_with_slippage,
                           stop_price, tp1_price, r_value,
                           bars_held, half_r_touched, mae, mfe,
                           pending_exit_reason
                    FROM cc_shadow_positions
                    WHERE run_id = %s AND variant_id = %s AND symbol = %s AND timeframe = %s;
                """, (run_id, variant_id, symbol, timeframe))
                row = cur.fetchone()

        if not row:
            return None

        from .models import ExitResult

        pending_exit = None
        if row[16]:  # pending_exit_reason
            pending_exit = ExitResult(
                should_exit=True,
                exit_reason=row[16],
                exit_at_next_open=True,
            )

        return ShadowPosition(
            run_id=str(row[0]),
            variant_id=row[1],
            symbol=row[2],
            timeframe=row[3],
            trade_id=row[4],
            direction=Direction(row[5]),
            entry_ts=row[6],
            entry_price=row[7],
            entry_price_with_slippage=row[8],
            stop_price=row[9],
            tp1_price=row[10],
            r_value=row[11],
            bars_held=row[12],
            half_r_touched=row[13],
            mae=row[14],
            mfe=row[15],
            pending_exit=pending_exit,
        )

    def delete_shadow_position(
        self,
        run_id: str,
        variant_id: str,
        symbol: str,
        timeframe: str,
    ) -> bool:
        """Delete a shadow position."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM cc_shadow_positions
                    WHERE run_id = %s AND variant_id = %s AND symbol = %s AND timeframe = %s;
                """, (run_id, variant_id, symbol, timeframe))
                deleted = cur.rowcount > 0

        return deleted

    def get_all_shadow_positions(self, run_id: str) -> list[ShadowPosition]:
        """Get all shadow positions for a run (for heartbeat refresh)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT run_id, variant_id, symbol, timeframe,
                           trade_id, direction,
                           entry_ts, entry_price, entry_price_with_slippage,
                           stop_price, tp1_price, r_value,
                           bars_held, half_r_touched, mae, mfe,
                           pending_exit_reason
                    FROM cc_shadow_positions
                    WHERE run_id = %s;
                """, (run_id,))
                rows = cur.fetchall()

        positions = []
        for row in rows:
            from .models import ExitResult

            pending_exit = None
            if row[16]:
                pending_exit = ExitResult(
                    should_exit=True,
                    exit_reason=row[16],
                    exit_at_next_open=True,
                )

            positions.append(ShadowPosition(
                run_id=str(row[0]),
                variant_id=row[1],
                symbol=row[2],
                timeframe=row[3],
                trade_id=row[4],
                direction=Direction(row[5]),
                entry_ts=row[6],
                entry_price=row[7],
                entry_price_with_slippage=row[8],
                stop_price=row[9],
                tp1_price=row[10],
                r_value=row[11],
                bars_held=row[12],
                half_r_touched=row[13],
                mae=row[14],
                mfe=row[15],
                pending_exit=pending_exit,
            ))

        return positions

    # =========================================================================
    # CHANGE LEDGER
    # =========================================================================

    def record_change(
        self,
        setup_id: str,
        variant_id: str,
        change_type: str,
    ) -> ChangeLedgerEntry:
        """Record a change in the ledger."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_change_ledger (setup_id, variant_id, change_type)
                    VALUES (%s, %s, %s)
                    RETURNING id, created_at;
                """, (setup_id, variant_id, change_type))
                row = cur.fetchone()

        return ChangeLedgerEntry(
            id=row[0],
            setup_id=setup_id,
            variant_id=variant_id,
            change_type=change_type,
            created_at=row[1],
        )

    def get_last_change(self, setup_id: str) -> Optional[ChangeLedgerEntry]:
        """Get the most recent change for a setup."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, setup_id, variant_id, change_type, created_at
                    FROM cc_change_ledger
                    WHERE setup_id = %s
                    ORDER BY created_at DESC
                    LIMIT 1;
                """, (setup_id,))
                row = cur.fetchone()

        if not row:
            return None

        return ChangeLedgerEntry(
            id=row[0],
            setup_id=row[1],
            variant_id=row[2],
            change_type=row[3],
            created_at=row[4],
        )

    def get_change_ledger(
        self,
        setup_id: Optional[str] = None,
        limit: int = 20,
    ) -> list[ChangeLedgerEntry]:
        """Get change ledger entries."""
        conditions = []
        params = []

        if setup_id:
            conditions.append("setup_id = %s")
            params.append(setup_id)

        where_clause = "WHERE " + " AND ".join(conditions) if conditions else ""

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(f"""
                    SELECT id, setup_id, variant_id, change_type, created_at
                    FROM cc_change_ledger
                    {where_clause}
                    ORDER BY created_at DESC
                    LIMIT %s;
                """, params + [limit])
                rows = cur.fetchall()

        return [
            ChangeLedgerEntry(
                id=row[0],
                setup_id=row[1],
                variant_id=row[2],
                change_type=row[3],
                created_at=row[4],
            )
            for row in rows
        ]

    # =========================================================================
    # PENDING SIGNALS (Postgres backup for Redis state)
    # =========================================================================

    def save_pending_signal(self, signal: PendingSignal) -> PendingSignal:
        """Save a pending signal (Redis backup)."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cc_pending_signals (
                        run_id, variant_id, symbol, timeframe,
                        signal_ts, signal_bar_ts, direction,
                        stop_price, tp1_price, tp2_price, r_value,
                        bars_pending, max_pending_bars
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (run_id, variant_id, symbol, timeframe) DO UPDATE SET
                        signal_ts = EXCLUDED.signal_ts,
                        signal_bar_ts = EXCLUDED.signal_bar_ts,
                        direction = EXCLUDED.direction,
                        stop_price = EXCLUDED.stop_price,
                        tp1_price = EXCLUDED.tp1_price,
                        tp2_price = EXCLUDED.tp2_price,
                        r_value = EXCLUDED.r_value,
                        bars_pending = EXCLUDED.bars_pending;
                """, (
                    signal.run_id, signal.variant_id, signal.symbol, signal.timeframe,
                    signal.signal_ts, signal.signal_bar_ts, signal.direction.value,
                    signal.stop_price, signal.tp1_price, signal.tp2_price, signal.r_value,
                    signal.bars_pending, signal.max_pending_bars,
                ))

        return signal

    def delete_pending_signal(
        self,
        run_id: str,
        variant_id: str,
        symbol: str,
        timeframe: str,
    ) -> bool:
        """Delete a pending signal."""
        with self._transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM cc_pending_signals
                    WHERE run_id = %s AND variant_id = %s AND symbol = %s AND timeframe = %s;
                """, (run_id, variant_id, symbol, timeframe))
                deleted = cur.rowcount > 0

        return deleted
