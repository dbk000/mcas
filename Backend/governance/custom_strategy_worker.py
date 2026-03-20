"""
Custom Strategy Worker — evaluates user-defined strategies against indicator snapshots.

Standalone async service. Consumes indicators_ready stream events via its own
dedicated consumer group (cstrat_group). DO NOT scale replicas > 1 without
redesigning crossover state (cstrat_prev write path).

Architecture:
- Reads ind_snap via read_fresh_snapshot() (m1/snap/m2 consistency)
- Evaluates all active user strategies for each (symbol, tf) event
- Writes hits to user_opportunities table
- Maintains crossover state in Redis (cstrat_prev:{symbol}:{tf})
- Hit-rate circuit breaker auto-pauses runaway strategies
"""

import sys
import json
import time
import math
import logging
from datetime import datetime, timezone, timedelta

import redis
import psycopg

from governance.bootstrap import bootstrap_genesis, get_config
bootstrap_genesis()

from governance.cache_contract import (
    read_fresh_snapshot,
    b2s, b2f, get_universe, get_timeframes,
    is_cache_ready, TF_SECONDS,
    CSTRAT_CONSUMER_GROUP, CSTRAT_GEN_KEY,
    cstrat_prev_key, COMPOSABLE_FIELDS,
)
from core_logic.user_strategy_eval import evaluate_conditions

logger = logging.getLogger("custom_strategy_worker")


class CustomStrategyWorker:
    """Evaluates user-defined strategies against live indicator snapshots."""

    def __init__(self):
        cfg = get_config()
        self.worker_cfg = cfg.Worker
        self.sb_cfg = cfg.StrategyBuilder
        self.r = redis.Redis.from_url(
            self.worker_cfg.REDIS_URL,
            decode_responses=True,
        )
        self.db_url = self.worker_cfg.DATABASE_URL
        self.conn = None

        # Strategy cache
        self._strategies = []  # list of strategy dicts
        self._strategy_gen = ""  # last seen cstrat_gen value
        self._last_strategy_load = 0.0

        # Cooldown cache: {(strategy_id, symbol, tf): last_hit_ts}
        self._cooldowns = {}

        # Logging
        self._eval_count = 0
        self._hit_count = 0
        self._last_log_ts = time.time()
        self._cycle_count = 0

    def _get_conn(self):
        """Get or create DB connection (single connection, not pool)."""
        if self.conn is None or self.conn.closed:
            self.conn = psycopg.connect(self.db_url)
        return self.conn

    def _create_consumer_groups(self, timeframes):
        """Create cstrat_group on all indicators_ready streams (idempotent)."""
        for tf in timeframes:
            stream = f"stream:indicators_ready:{tf}"
            try:
                self.r.xgroup_create(stream, CSTRAT_CONSUMER_GROUP, id="0", mkstream=True)
                logger.info(f"Created consumer group {CSTRAT_CONSUMER_GROUP} on {stream}")
            except redis.ResponseError as e:
                if "BUSYGROUP" in str(e):
                    pass
                else:
                    raise

    def _load_strategies(self):
        """Load active, non-paused user strategies from Postgres."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, user_id, name, conditions, logic, timeframes,
                       symbol_filter, cooldown_minutes
                FROM user_strategies
                WHERE active = TRUE AND auto_paused = FALSE
            """)
            rows = cur.fetchall()

        strategies = []
        for row in rows:
            strategies.append({
                "id": row[0],
                "user_id": row[1],
                "name": row[2],
                "conditions": row[3] if isinstance(row[3], list) else json.loads(row[3]),
                "logic": row[4],
                "timeframes": row[5],
                "symbol_filter": row[6],
                "cooldown_minutes": row[7],
            })

        self._strategies = strategies
        self._last_strategy_load = time.time()
        logger.info(f"Loaded {len(strategies)} active strategies")

        # Prune cooldown entries for strategies no longer active
        active_ids = {s["id"] for s in strategies}
        stale_keys = [k for k in self._cooldowns if k[0] not in active_ids]
        for k in stale_keys:
            del self._cooldowns[k]

    def _load_cooldowns(self):
        """Populate cooldown cache from DB at startup."""
        sb = self.sb_cfg
        lookback_days = max(7, sb.cooldown_floor_minutes / 1440 + 1)
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT strategy_id, symbol, timeframe, MAX(created_ts)
                FROM user_opportunities
                WHERE created_ts > NOW() - make_interval(days => %s)
                  AND strategy_id IN (
                      SELECT id FROM user_strategies
                      WHERE active = TRUE AND auto_paused = FALSE
                  )
                GROUP BY 1, 2, 3
            """, (lookback_days,))
            for row in cur.fetchall():
                key = (row[0], row[1], row[2])
                self._cooldowns[key] = row[3].replace(tzinfo=timezone.utc).timestamp()
        logger.info(f"Loaded {len(self._cooldowns)} cooldown entries")

    def _check_strategy_refresh(self):
        """Check if strategies need reloading (cstrat_gen or periodic)."""
        current_gen = self.r.get(CSTRAT_GEN_KEY) or ""
        if current_gen != self._strategy_gen:
            self._strategy_gen = current_gen
            self._load_strategies()
            return

        if time.time() - self._last_strategy_load > self.sb_cfg.strategy_cache_refresh_s:
            self._load_strategies()

    def _is_on_cooldown(self, strategy_id, symbol, tf, cooldown_minutes):
        """Check if a (strategy, symbol, tf) is within cooldown."""
        key = (strategy_id, symbol, tf)
        last_hit = self._cooldowns.get(key)
        if last_hit is None:
            return False
        return (time.time() - last_hit) < (cooldown_minutes * 60)

    def _record_hit(self, strategy, symbol, tf, snapshot, triggered_ts):
        """Write hit to user_opportunities and update cooldown."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO user_opportunities
                    (strategy_id, user_id, symbol, timeframe, direction,
                     triggered_ts, conditions_snapshot)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                strategy["id"],
                strategy["user_id"],
                symbol,
                tf,
                None,  # direction is nullable in V1
                datetime.fromtimestamp(triggered_ts, tz=timezone.utc),
                json.dumps(snapshot, separators=(",", ":")),
            ))
        conn.commit()

        # Update cooldown
        self._cooldowns[(strategy["id"], symbol, tf)] = time.time()
        self._hit_count += 1

    def _auto_pause_strategy(self, strategy_id, fire_rate):
        """Set auto_paused = TRUE for a runaway strategy."""
        conn = self._get_conn()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE user_strategies
                SET auto_paused = TRUE, updated_ts = NOW()
                WHERE id = %s
            """, (strategy_id,))
        conn.commit()

        # Invalidate strategy cache
        self.r.set(CSTRAT_GEN_KEY, str(time.time()))
        logger.warning(
            f"Auto-paused strategy {strategy_id}: "
            f"fire rate {fire_rate:.1f}% exceeds {self.sb_cfg.max_fire_rate_pct}%"
        )

    def _evaluate_cycle(self, tf, events):
        """Evaluate all strategies for a batch of (symbol, tf) events."""
        if not self._strategies:
            return

        # Track per-strategy fire counts for circuit breaker
        fire_counts = {}  # strategy_id -> count
        total_evaluated = 0

        for event_data in events:
            symbol = b2s(event_data.get("symbol", ""))
            bar_start_ts = b2s(event_data.get("bar_start_ts", ""))
            if not symbol or not bar_start_ts:
                continue

            # Read snapshot with m1/snap/m2 consistency
            snap, snap_ts = read_fresh_snapshot(
                self.r, symbol, tf, expected_bar_start_ts=bar_start_ts,
            )
            if snap is None:
                continue

            # Convert snapshot to flat dict for evaluation
            from dataclasses import asdict
            snap_dict = asdict(snap)
            # Remove non-composable fields
            current = {}
            for k, v in snap_dict.items():
                if k in COMPOSABLE_FIELDS and v is not None:
                    current[k] = float(v) if not isinstance(v, (list, dict)) else None
            # Remove None entries
            current = {k: v for k, v in current.items() if v is not None}

            # Also read OHLCV price fields from ind_snap directly
            snap_key = f"ind_snap:{symbol}:{tf}"
            for price_field in ("close", "open", "high", "low"):
                if price_field not in current:
                    raw = self.r.hget(snap_key, price_field)
                    if raw is not None:
                        try:
                            current[price_field] = float(raw)
                        except (ValueError, TypeError):
                            pass

            # Read previous snapshot for crossovers
            prev_key = cstrat_prev_key(symbol, tf)
            prev_raw = self.r.hgetall(prev_key)
            previous = None
            if prev_raw:
                previous = {}
                for k, v in prev_raw.items():
                    try:
                        previous[k] = float(v)
                    except (ValueError, TypeError):
                        pass

            # Evaluate each strategy
            triggered_ts = float(bar_start_ts) if bar_start_ts.replace(".", "").isdigit() else time.time()

            for strategy in self._strategies:
                # Skip if strategy doesn't target this timeframe
                if tf not in strategy["timeframes"]:
                    continue

                # Skip if symbol filter doesn't match
                sf = strategy["symbol_filter"]
                if sf and sf != "all_monitored":
                    allowed = [s.strip() for s in sf.split(",")]
                    if symbol not in allowed:
                        continue

                total_evaluated += 1
                self._eval_count += 1

                # Cooldown check
                if self._is_on_cooldown(
                    strategy["id"], symbol, tf, strategy["cooldown_minutes"]
                ):
                    continue

                # Evaluate conditions
                all_passed, snapshot = evaluate_conditions(
                    strategy["conditions"], current, previous,
                )

                if all_passed and snapshot:
                    self._record_hit(strategy, symbol, tf, snapshot, triggered_ts)
                    fire_counts[strategy["id"]] = fire_counts.get(strategy["id"], 0) + 1

            # Write current as previous for crossovers (AFTER all evaluations)
            if current:
                self.r.hset(prev_key, mapping={k: str(v) for k, v in current.items()})

        # Circuit breaker check
        if total_evaluated > 0:
            symbols_in_cycle = len(events)
            for strategy_id, fires in fire_counts.items():
                fire_rate = (fires / symbols_in_cycle) * 100
                if fire_rate > self.sb_cfg.max_fire_rate_pct:
                    self._auto_pause_strategy(strategy_id, fire_rate)

    def _periodic_log(self):
        """Emit periodic status log."""
        now = time.time()
        if now - self._last_log_ts < self.sb_cfg.evaluation_log_interval_s:
            return
        elapsed = now - self._last_log_ts
        logger.info(
            f"[cstrat] cycles={self._cycle_count} "
            f"evals={self._eval_count} hits={self._hit_count} "
            f"strategies={len(self._strategies)} "
            f"cooldowns={len(self._cooldowns)} "
            f"elapsed={elapsed:.0f}s"
        )
        self._eval_count = 0
        self._hit_count = 0
        self._cycle_count = 0
        self._last_log_ts = now

    def run(self):
        """Main event loop."""
        logger.info("[cstrat] Starting custom strategy worker...")

        # Wait for cache readiness
        while not is_cache_ready(self.r):
            logger.info("[cstrat] Waiting for cache readiness...")
            time.sleep(5)

        timeframes = get_timeframes(self.r)
        if not timeframes:
            tf_str = self.worker_cfg.DEFAULT_TIMEFRAMES
            timeframes = [t.strip() for t in tf_str.split(",") if t.strip()]
        logger.info(f"[cstrat] Timeframes: {timeframes}")

        # Create consumer groups
        self._create_consumer_groups(timeframes)

        # Load strategies and cooldowns
        self._load_strategies()
        self._load_cooldowns()

        # Build stream list for XREADGROUP
        streams = {f"stream:indicators_ready:{tf}": ">" for tf in timeframes}
        consumer_name = "cstrat_0"

        logger.info("[cstrat] Entering main loop")

        while True:
            try:
                self._check_strategy_refresh()
                self._periodic_log()

                # Block up to 2s for new events
                result = self.r.xreadgroup(
                    CSTRAT_CONSUMER_GROUP, consumer_name,
                    streams, count=100, block=2000,
                )

                if not result:
                    continue

                for stream_name, messages in result:
                    # Extract timeframe from stream name
                    tf = stream_name.split(":")[-1]

                    events = []
                    msg_ids = []
                    for msg_id, data in messages:
                        events.append(data)
                        msg_ids.append(msg_id)

                    if events:
                        self._evaluate_cycle(tf, events)
                        self._cycle_count += 1

                    # ACK all messages
                    for msg_id in msg_ids:
                        self.r.xack(stream_name, CSTRAT_CONSUMER_GROUP, msg_id)

            except redis.ConnectionError:
                logger.error("[cstrat] Redis connection lost, reconnecting...")
                time.sleep(5)
                self.r = redis.Redis.from_url(
                    self.worker_cfg.REDIS_URL, decode_responses=True,
                )
            except psycopg.OperationalError:
                logger.error("[cstrat] DB connection lost, reconnecting...")
                self.conn = None
                time.sleep(5)
            except Exception:
                logger.exception("[cstrat] Unexpected error in main loop")
                time.sleep(2)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    worker = CustomStrategyWorker()
    worker.run()


if __name__ == "__main__":
    main()
