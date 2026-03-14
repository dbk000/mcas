"""
Champion/Challenger Framework - Evaluator
==========================================

Parallel evaluation of Baseline + Champion + Challenger.

Key components:
- VariantStateBackend: Redis state with namespace enforcement
- ParallelEvaluator: 3-way comparison on same market stream
- Cache management: Evaluator caching for run lifetime

Canon compliance:
- Baseline ALWAYS evaluates (even when no CC run is active)
- Each variant has namespaced state (no collision)
- One ShadowTradeSimulator per variant (no shared pending signals)
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional, Any

from .models import (
    CachedVariant, ExperimentRun, VariantStatus,
    ShadowTrade, ShadowPosition, ExitResult,
    CCEvaluatorCacheEntry, Direction,
    CC_STATE_TTL_S, MAX_PENDING_BARS,
    CACHE_CLEANUP_INTERVAL_SECONDS, CACHE_SIZE_ALERT_THRESHOLD,
)
from .repository import CCRepository
from .simulator import ShadowTradeSimulator

logger = logging.getLogger(__name__)


# =============================================================================
# REDIS KEY BUILDERS
# =============================================================================

def cc_active_run_key(setup_id: str) -> str:
    """Active run per setup (allows multiple setups to run CC independently)."""
    return f"cc:active_run:{setup_id}"  # STRING: run_id


def cc_variant_state_key(
    run_id: str,
    variant_id: str,
    setup_id: str,
    symbol: str,
    tf: str,
) -> str:
    """
    Per-symbol, per-timeframe state for a variant during an experiment.

    CRITICAL: Full namespace prevents Champion/Challenger state collision.
    Each variant maintains independent state (e.g., squeeze_age, box_high).
    """
    return f"cc:state:{run_id}:{variant_id}:{setup_id}:{symbol}:{tf}"  # HASH


def cc_shadow_position_key(
    run_id: str,
    variant_id: str,
    symbol: str,
    tf: str,
) -> str:
    """
    Shadow position tracking for exit simulation.

    CRITICAL: Includes timeframe to prevent collision when running CC
    on multiple timeframes (timeframes TEXT[] in cc_experiment_runs).
    """
    return f"cc:shadow_pos:{run_id}:{variant_id}:{symbol}:{tf}"  # HASH


def cc_pending_signal_key(
    run_id: str,
    variant_id: str,
    symbol: str,
    tf: str,
) -> str:
    """Pending signal awaiting next-bar entry."""
    return f"cc:pending:{run_id}:{variant_id}:{symbol}:{tf}"  # HASH


def cc_champion_key(setup_id: str) -> str:
    """Quick lookup: current champion variant_id for a setup."""
    return f"cc:champion:{setup_id}"  # STRING


def cc_baseline_key(setup_id: str) -> str:
    """Quick lookup: baseline variant_id for a setup."""
    return f"cc:baseline:{setup_id}"  # STRING


def cc_variant_cache_key(run_id: str, variant_id: str) -> str:
    """Cached variant object for the lifetime of a run."""
    return f"cc:variant_cache:{run_id}:{variant_id}"  # STRING (JSON)


# =============================================================================
# VARIANT STATE BACKEND
# =============================================================================

class VariantStateBackend:
    """
    State backend with mandatory namespace enforcement.

    INVARIANT: All keys MUST include {run_id}:{variant_id}:{symbol}:{tf}
    to prevent state collision between variants.
    """

    # Key format: cc:state:{run_id}:{variant_id}:{setup_id}:{symbol}:{tf}
    # Field positions (0-indexed after splitting on ':')
    KEY_PREFIX_POS = 0      # "cc"
    KEY_TYPE_POS = 1        # "state"
    RUN_ID_POS = 2
    VARIANT_ID_POS = 3
    SETUP_ID_POS = 4
    SYMBOL_POS = 5
    TF_POS = 6
    EXPECTED_FIELD_COUNT = 7

    def __init__(self, redis_client, repository: CCRepository):
        self.redis = redis_client
        self.repo = repository

    def _assert_namespace(
        self,
        key: str,
        run_id: str,
        variant_id: str,
        symbol: str,
        tf: str,
    ) -> None:
        """
        GUARD: Refuse reads/writes unless key has correct namespace fields at exact positions.

        v6 fix: Parse key by splitting on ':' and compare exact field positions.
        Substring-based checks can false-pass (e.g., "BTC" appears elsewhere)
        or false-fail if formatting changes.
        """
        parts = key.split(":")

        # Check field count
        if len(parts) < self.EXPECTED_FIELD_COUNT:
            raise ValueError(
                f"Namespace violation: key '{key}' has {len(parts)} fields, "
                f"expected {self.EXPECTED_FIELD_COUNT}"
            )

        # Check exact field positions
        errors = []
        if parts[self.RUN_ID_POS] != run_id:
            errors.append(f"run_id at pos {self.RUN_ID_POS}: got '{parts[self.RUN_ID_POS]}', expected '{run_id}'")
        if parts[self.VARIANT_ID_POS] != variant_id:
            errors.append(f"variant_id at pos {self.VARIANT_ID_POS}: got '{parts[self.VARIANT_ID_POS]}', expected '{variant_id}'")
        if parts[self.SYMBOL_POS] != symbol:
            errors.append(f"symbol at pos {self.SYMBOL_POS}: got '{parts[self.SYMBOL_POS]}', expected '{symbol}'")
        if parts[self.TF_POS] != tf:
            errors.append(f"tf at pos {self.TF_POS}: got '{parts[self.TF_POS]}', expected '{tf}'")

        if errors:
            raise ValueError(
                f"Namespace violation in key '{key}':\n  " + "\n  ".join(errors)
            )

    def load(
        self,
        run_id: str,
        variant_id: str,
        setup_id: str,
        symbol: str,
        tf: str,
    ) -> dict:
        """Load variant state with namespace enforcement."""
        key = cc_variant_state_key(run_id, variant_id, setup_id, symbol, tf)
        self._assert_namespace(key, run_id, variant_id, symbol, tf)

        state = self.redis.hgetall(key)
        if state:
            # Convert bytes to strings if needed
            return {
                k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                for k, v in state.items()
            }
        return {}

    def save(
        self,
        run_id: str,
        variant_id: str,
        setup_id: str,
        symbol: str,
        tf: str,
        state: dict,
    ) -> None:
        """Save variant state with namespace enforcement and TTL refresh."""
        key = cc_variant_state_key(run_id, variant_id, setup_id, symbol, tf)
        self._assert_namespace(key, run_id, variant_id, symbol, tf)

        # Convert all values to strings for Redis
        state_str = {k: str(v) for k, v in state.items()}

        pipe = self.redis.pipeline()
        pipe.hset(key, mapping=state_str)
        pipe.expire(key, CC_STATE_TTL_S)  # Refresh TTL on every write
        pipe.execute()

    def delete(
        self,
        run_id: str,
        variant_id: str,
        setup_id: str,
        symbol: str,
        tf: str,
    ) -> None:
        """Delete variant state."""
        key = cc_variant_state_key(run_id, variant_id, setup_id, symbol, tf)
        self._assert_namespace(key, run_id, variant_id, symbol, tf)
        self.redis.delete(key)

    # =========================================================================
    # SHADOW POSITION STATE
    # =========================================================================

    def load_shadow_position(
        self,
        run_id: str,
        variant_id: str,
        symbol: str,
        tf: str,
    ) -> Optional[ShadowPosition]:
        """Load shadow position from Redis, with Postgres fallback."""
        key = cc_shadow_position_key(run_id, variant_id, symbol, tf)

        state = self.redis.hgetall(key)
        if state:
            return self._redis_hash_to_shadow_position(state)

        # Fallback to Postgres
        position = self.repo.get_shadow_position(run_id, variant_id, symbol, tf)
        if position:
            # Restore to Redis
            self._save_shadow_position_to_redis(position)
            logger.warning(f"Recovered shadow position from Postgres: {key}")
            return position

        return None

    def save_shadow_position(self, position: ShadowPosition) -> None:
        """Save shadow position to both Redis and Postgres."""
        # Save to Redis
        self._save_shadow_position_to_redis(position)

        # Save to Postgres (backup)
        self.repo.save_shadow_position(position)

    def _save_shadow_position_to_redis(self, position: ShadowPosition) -> None:
        """Save shadow position to Redis only."""
        key = cc_shadow_position_key(
            position.run_id, position.variant_id, position.symbol, position.timeframe
        )

        pipe = self.redis.pipeline()
        pipe.hset(key, mapping=position.to_redis_hash())
        pipe.expire(key, CC_STATE_TTL_S)
        pipe.execute()

    def clear_shadow_position(
        self,
        run_id: str,
        variant_id: str,
        symbol: str,
        tf: str,
    ) -> None:
        """Clear shadow position from both Redis and Postgres."""
        key = cc_shadow_position_key(run_id, variant_id, symbol, tf)
        self.redis.delete(key)
        self.repo.delete_shadow_position(run_id, variant_id, symbol, tf)

    def _redis_hash_to_shadow_position(self, state: dict) -> ShadowPosition:
        """Convert Redis hash to ShadowPosition."""
        # Decode bytes if needed
        def get(key: str) -> str:
            v = state.get(key) or state.get(key.encode())
            if isinstance(v, bytes):
                return v.decode()
            return v or ""

        pending_exit = None
        pending_reason = get("pending_exit_reason")
        if pending_reason:
            pending_exit = ExitResult(
                should_exit=True,
                exit_reason=pending_reason,
                exit_at_next_open=True,
            )

        return ShadowPosition(
            trade_id=get("trade_id"),
            run_id=get("run_id"),
            variant_id=get("variant_id"),
            symbol=get("symbol"),
            timeframe=get("timeframe"),
            direction=Direction(get("direction")),
            entry_ts=datetime.fromisoformat(get("entry_ts")),
            entry_price=float(get("entry_price")),
            entry_price_with_slippage=float(get("entry_price_with_slippage")),
            stop_price=float(get("stop_price")),
            tp1_price=float(get("tp1_price")) if get("tp1_price") else None,
            r_value=float(get("r_value")),
            bars_held=int(get("bars_held") or 0),
            half_r_touched=get("half_r_touched") == "1",
            mae=float(get("mae") or 0),
            mfe=float(get("mfe") or 0),
            pending_exit=pending_exit,
        )


# =============================================================================
# PARALLEL EVALUATOR
# =============================================================================

class ParallelEvaluator:
    """
    Evaluates Baseline + Champion + Challenger on the same data stream.

    CRITICAL: Each variant has its own:
    - Namespaced state (via state_backend)
    - Dedicated ShadowTradeSimulator instance (prevents signal/exit collision)
    """

    def __init__(
        self,
        run_id: str,
        baseline: Optional[CachedVariant],
        champion: CachedVariant,
        challenger: CachedVariant,
        execution_spec: dict,
        state_backend: VariantStateBackend,
        repository: CCRepository,
    ):
        self.run_id = run_id
        self.execution_spec = execution_spec
        self.state_backend = state_backend
        self.repo = repository

        # CRITICAL: One simulator PER VARIANT to prevent signal/exit collision
        # If shared, first variant to call process_pending_entries() would pop
        # the signal, and other variants would never enter.
        self.variants: dict[str, tuple[CachedVariant, ShadowTradeSimulator]] = {}

        if baseline:
            self.variants["baseline"] = (
                baseline,
                ShadowTradeSimulator(execution_spec, state_backend, repository),
            )
        self.variants["champion"] = (
            champion,
            ShadowTradeSimulator(execution_spec, state_backend, repository),
        )
        self.variants["challenger"] = (
            challenger,
            ShadowTradeSimulator(execution_spec, state_backend, repository),
        )

    def evaluate_bar(
        self,
        symbol: str,
        timeframe: str,
        snapshot: Any,  # IndicatorSnapshot from indicators.py
    ) -> dict[str, Optional[dict]]:
        """
        Evaluate all variants on the same bar.

        Each produces independent:
        - State update (namespaced)
        - Signal (or None)
        - Trade outcome simulation (if signal active)

        Order of operations per variant:
        1. Process pending exits (TIME_STOP at next bar open)
        2. Process pending entries (signal from previous bar)
        3. Compute new state
        4. Evaluate trigger (may record new signal for next bar)
        5. Check exits on open positions

        Returns:
            Dict of role -> signal_dict (or None) for each variant
        """
        results = {}

        for role, (variant, simulator) in self.variants.items():
            try:
                result = self._evaluate_variant(
                    variant, simulator, symbol, timeframe, snapshot
                )
                results[role] = result
            except Exception as e:
                logger.error(
                    f"Error evaluating {role} ({variant.variant_id}) "
                    f"on {symbol}:{timeframe}: {e}"
                )
                results[role] = None

        return results

    def _evaluate_variant(
        self,
        variant: CachedVariant,
        simulator: ShadowTradeSimulator,
        symbol: str,
        timeframe: str,
        snapshot: Any,
    ) -> Optional[dict]:
        """Evaluate a single variant on a bar."""
        # Load variant-specific state (namespaced key)
        state = self.state_backend.load(
            run_id=self.run_id,
            variant_id=variant.variant_id,
            setup_id=variant.setup_id,
            symbol=symbol,
            tf=timeframe,
        )

        # =====================================================================
        # Step 1: Process pending exits (TIME_STOP finalization at bar open)
        # =====================================================================
        self._process_pending_exits(variant, simulator, symbol, timeframe, snapshot)

        # =====================================================================
        # Step 2: Process pending entries (next-bar-open entry)
        # =====================================================================
        new_trade = simulator.process_pending_entries(
            self.run_id, variant.variant_id, symbol, timeframe, snapshot
        )
        if new_trade:
            self.repo.save_trade(self.run_id, variant.variant_id, new_trade)

        # =====================================================================
        # Step 3: Compute new state
        # =====================================================================
        # evaluator is set at registration time: either None or a setup evaluator
        # with compute_state/evaluate_trigger methods. No hasattr needed.
        new_state = {}
        if variant.evaluator is not None:
            new_state = variant.evaluator.compute_state(snapshot, prev_state=state)
        else:
            new_state = state  # Preserve existing state if no evaluator

        # =====================================================================
        # Step 4: Evaluate trigger
        # =====================================================================
        signal = None
        if variant.evaluator is not None:
            signal = variant.evaluator.evaluate_trigger(
                snapshot, new_state, symbol, timeframe
            )

        # Save updated state (namespaced)
        if new_state:
            self.state_backend.save(
                run_id=self.run_id,
                variant_id=variant.variant_id,
                setup_id=variant.setup_id,
                symbol=symbol,
                tf=timeframe,
                state=new_state,
            )

        # Record signal for next-bar entry (if triggered)
        if signal:
            simulator.record_signal(
                self.run_id, variant.variant_id, signal, symbol, timeframe
            )
        else:
            # v7: Age any existing pending signal that wasn't processed this bar
            simulator.age_pending_signals(
                self.run_id, variant.variant_id, symbol, timeframe
            )

        # =====================================================================
        # Step 5: Check exits for any open shadow positions
        # =====================================================================
        self._check_shadow_exits(variant, simulator, symbol, timeframe, snapshot)

        return signal.to_dict() if signal else None

    def _process_pending_exits(
        self,
        variant: CachedVariant,
        simulator: ShadowTradeSimulator,
        symbol: str,
        tf: str,
        snapshot: Any,
    ) -> None:
        """
        Finalize TIME_STOP exits at this bar's OPEN.

        Called at START of bar processing, before entries.
        """
        shadow_pos = self.state_backend.load_shadow_position(
            self.run_id, variant.variant_id, symbol, tf
        )
        if not shadow_pos or not shadow_pos.pending_exit:
            return

        simulator.finalize_pending_exit(
            self.run_id, variant.variant_id, shadow_pos, snapshot
        )

    def _check_shadow_exits(
        self,
        variant: CachedVariant,
        simulator: ShadowTradeSimulator,
        symbol: str,
        tf: str,
        snapshot: Any,
    ) -> None:
        """Simulate bar-by-bar exit checks using same logic as paper trading."""
        shadow_pos = self.state_backend.load_shadow_position(
            self.run_id, variant.variant_id, symbol, tf
        )
        if not shadow_pos or shadow_pos.pending_exit:
            return  # Already pending exit, will process at next bar open

        exit_result = simulator.check_exit(shadow_pos, snapshot)

        if exit_result.should_exit:
            if exit_result.exit_at_next_open:
                # TIME_STOP: mark for exit at next bar open
                shadow_pos.pending_exit = exit_result
                self.state_backend.save_shadow_position(shadow_pos)
            else:
                # Immediate exit (STOP_LOSS, TP1)
                simulator.execute_immediate_exit(
                    self.run_id, variant.variant_id, shadow_pos, exit_result, snapshot
                )


# =============================================================================
# EVALUATOR CACHE MANAGEMENT
# =============================================================================

class CCEvaluatorCache:
    """
    Cache for ParallelEvaluator instances.

    - One evaluator per active run
    - Evicted on run completion/closure
    - Periodic cleanup for stale entries (TTL-based fallback)
    """

    def __init__(self):
        self._cache: dict[str, CCEvaluatorCacheEntry] = {}
        self._last_cleanup = time.time()

    def get_or_create(
        self,
        cc_run: ExperimentRun,
        state_backend: VariantStateBackend,
        repository: CCRepository,
    ) -> ParallelEvaluator:
        """
        Get or create cached ParallelEvaluator for this run.

        Variants are loaded ONCE at run start and cached for the run lifetime.
        Module maps are frozen in cc_experiment_runs at start.
        """
        cache_key = f"cc_evaluator:{cc_run.run_id}"
        now = time.time()

        # Check if cached and still valid
        if cache_key in self._cache:
            entry = self._cache[cache_key]
            entry.last_used = now
            return entry.evaluator

        # Create new evaluator
        baseline = self._load_variant_from_snapshot(
            cc_run.baseline_module_map_snapshot,
            cc_run.baseline_variant_id,
        ) if cc_run.baseline_variant_id else None

        champion = self._load_variant_from_snapshot(
            cc_run.champion_module_map_snapshot,
            cc_run.champion_variant_id,
        )

        challenger = self._load_variant_from_snapshot(
            cc_run.challenger_module_map_snapshot,
            cc_run.challenger_variant_id,
        )

        evaluator = ParallelEvaluator(
            run_id=cc_run.run_id,
            baseline=baseline,
            champion=champion,
            challenger=challenger,
            execution_spec=cc_run.execution_spec_snapshot,
            state_backend=state_backend,
            repository=repository,
        )

        # Store using canonical cache entry structure
        self._cache[cache_key] = CCEvaluatorCacheEntry(
            evaluator=evaluator,
            last_used=now,
        )

        logger.info(f"Created CC evaluator for run {cc_run.run_id}")
        return evaluator

    def _load_variant_from_snapshot(
        self,
        module_map_snapshot: Optional[dict],
        variant_id: Optional[str],
    ) -> Optional[CachedVariant]:
        """Load a variant from its frozen snapshot."""
        if not module_map_snapshot or not variant_id:
            return None

        # Extract data from snapshot
        setup_id = module_map_snapshot.get("setup_id", "")

        # TODO: Load actual evaluator instance based on module map
        # For now, return a CachedVariant without evaluator
        return CachedVariant(
            variant_id=variant_id,
            setup_id=setup_id,
            status=VariantStatus.CHAMPION,  # Will be overridden
            module_map_snapshot=module_map_snapshot,
            evaluator=None,  # TODO: Load evaluator from module map
        )

    def evict(self, run_id: str) -> None:
        """
        Explicit cache eviction on run completion/closure.

        MUST be called when a run is completed, cancelled, or promoted.
        Prevents unbounded memory growth in long-lived worker processes.
        """
        cache_key = f"cc_evaluator:{run_id}"
        if cache_key in self._cache:
            del self._cache[cache_key]
            logger.info(f"Evicted CC evaluator cache for run {run_id}")

    def evict_stale(self, max_age_seconds: int = 86400) -> int:
        """
        TTL-based eviction fallback for stale cache entries.

        v8 REQUIRED: This MUST be scheduled to run periodically.

        Args:
            max_age_seconds: Evict entries not used in this many seconds (default: 24h)

        Returns:
            Number of entries evicted
        """
        cache_size_before = len(self._cache)
        now = time.time()
        stale_keys = []

        for cache_key, entry in self._cache.items():
            if now - entry.last_used > max_age_seconds:
                stale_keys.append(cache_key)

        for key in stale_keys:
            del self._cache[key]

        cache_size_after = len(self._cache)

        logger.info(
            f"Cache cleanup: {len(stale_keys)} stale entries evicted, "
            f"cache size {cache_size_before} -> {cache_size_after}"
        )

        # v8: Alert if cache is suspiciously large
        if cache_size_after > CACHE_SIZE_ALERT_THRESHOLD:
            logger.warning(
                f"CC evaluator cache size ({cache_size_after}) exceeds threshold "
                f"({CACHE_SIZE_ALERT_THRESHOLD}). Possible memory leak or many active runs."
            )

        self._last_cleanup = now
        return len(stale_keys)

    def maybe_cleanup(self) -> None:
        """Run cleanup if enough time has passed."""
        now = time.time()
        if now - self._last_cleanup > CACHE_CLEANUP_INTERVAL_SECONDS:
            self.evict_stale()

    def size(self) -> int:
        """Get current cache size."""
        return len(self._cache)


# Global cache instance
_evaluator_cache = CCEvaluatorCache()


def get_evaluator_cache() -> CCEvaluatorCache:
    """Get the global evaluator cache."""
    return _evaluator_cache
