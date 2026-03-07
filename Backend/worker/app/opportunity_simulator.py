"""
Opportunity Simulator
=====================

Simulates expired opportunities to gauge expectancy.

For each expired opportunity:
1. Assume a position opened at signal generation time
2. Walk forward through OHLCV data
3. Close when the system's exit logic would have triggered

Uses batch OHLCV loading to avoid rate limits and shared exit_checker
module to ensure exit logic consistency with BACKTEST/PAPER runs.
"""

import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid

import psycopg

from exit_checker import check_exit_simple, ExitConfig
from ohlcv_backfill import backfill_for_simulation

logger = logging.getLogger(__name__)

# Keep simulation defaults centralized so worker/API callers cannot drift from
# the research baseline by accident.
SIM_DEFAULT_MAX_BARS_FORWARD = 200
SIM_DEFAULT_MIN_R_PRESERVATION = 0.5
# Causal entry-time D1 gate defaults (entry-known features only).
SIM_DEFAULT_D1_CAUSAL_GATE_ENABLED = True
SIM_DEFAULT_D1_CAUSAL_MIN_ENTRY_PRICE = 0.0
SIM_DEFAULT_D1_CAUSAL_MIN_STOP_PCT = 0.0
SIM_DEFAULT_D1_CAUSAL_MIN_SCORE = 0.0

# Per-setup exit configs — override default ExitConfig for strategies with
# validated exit behavior. Without this, all strategies use the default which
# includes fail-fast and trailing stops that may not match the validated config.
SETUP_EXIT_CONFIGS: dict[str, ExitConfig] = {
    # D1 Config D (production winner): no fail-fast, no trailing stop.
    # Validated via 5/5 walk-forward folds, daily PF 2.70.
    "D1": ExitConfig(
        fail_fast_bars=0, fail_fast_threshold=0.0,
        trail_breakeven_r=0, trail_activation_r=999.0, trail_distance_r=0.5,
    ),
}


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val is not None else default


@dataclass
class SimulationConfig:
    """Configuration for opportunity simulation."""
    position_size_usd: float = 1000.0
    slippage_entry_bps: float = 5.0
    slippage_exit_bps: float = 5.0
    fee_taker_bps: float = 4.0
    max_bars_forward: int = SIM_DEFAULT_MAX_BARS_FORWARD  # Max bars to simulate forward
    time_stop_bars: int = 50    # Default time stop if not specified
    max_fee_r_ratio: float = _env_float("SIM_MAX_FEE_R_RATIO", 0.3)    # Skip trades where fees > this fraction of risk
    min_r_preservation: float = SIM_DEFAULT_MIN_R_PRESERVATION  # Skip trades where gap consumed > 50% of original R

    # Stop distance factor (Experiment B: tighter stops at simulation time)
    stop_distance_factor: float = 1.0  # Global multiplier (1.0 = unchanged)
    stop_distance_factors: dict = field(default_factory=dict)  # Per-setup overrides

    # Entry-gate filters (applied during simulation to catch pre-filter opportunities)
    # Matches S6 evaluator defaults — protects against microcap shorts & wide stops
    min_short_price: float = _env_float("SIM_MIN_SHORT_PRICE", 1.00)
    max_stop_pct: float = _env_float("SIM_MAX_STOP_PCT", 0.10)

    # Exit config override (None = use defaults from exit_checker.py)
    exit_config: Optional[ExitConfig] = None
    intrabar_policy: str = "STOP_FIRST"
    one_event_per_bar: bool = True
    # Causal D1 entry gate (uses only information available at entry time).
    d1_causal_gate_enabled: bool = SIM_DEFAULT_D1_CAUSAL_GATE_ENABLED
    d1_causal_min_entry_price: float = SIM_DEFAULT_D1_CAUSAL_MIN_ENTRY_PRICE
    d1_causal_min_stop_pct: float = SIM_DEFAULT_D1_CAUSAL_MIN_STOP_PCT
    d1_causal_min_score: float = SIM_DEFAULT_D1_CAUSAL_MIN_SCORE


@dataclass
class SimulationResult:
    """Result of simulating a single opportunity."""
    opportunity_id: int
    symbol: str
    timeframe: str
    setup_id: str
    direction: str
    opportunity_created_ts: datetime

    # Entry
    entry_ts: Optional[datetime] = None
    entry_price: Optional[float] = None
    entry_price_with_slippage: Optional[float] = None
    position_size: Optional[float] = None

    # Levels
    stop_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    r_value: Optional[float] = None

    # Exit
    exit_ts: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_price_with_slippage: Optional[float] = None
    exit_reason: Optional[str] = None
    bars_held: int = 0

    # Results
    pnl_gross: Optional[float] = None
    fees: Optional[float] = None
    pnl_net: Optional[float] = None
    r_multiple: Optional[float] = None

    # Metadata
    data_available: bool = True
    error_message: Optional[str] = None

    # Audit trace (for correlation analysis)
    trace_json: Optional[dict] = None

    # TP invalidation observability
    tp_invalidated: bool = False
    tp_invalidation_reason: Optional[str] = None


class OpportunitySimulator:
    """
    Simulates expired opportunities to gauge expectancy.

    Key patterns:
    - Batch OHLCV loading to minimize DB queries
    - Groups opportunities by (symbol, timeframe)
    - Uses shared exit_checker for consistent exit logic
    """

    def __init__(self, database_url: str, config: SimulationConfig = None):
        self.database_url = database_url
        self.config = config or SimulationConfig()

    def _get_conn(self):
        return psycopg.connect(self.database_url)

    def _resolve_stop_distance_factor(self, setup_id: str) -> float:
        """Resolve stop multiplier safely for a strategy/setup."""
        if self.config.stop_distance_factors:
            val = self.config.stop_distance_factors.get(setup_id, 1.0)
        else:
            val = self.config.stop_distance_factor
        try:
            return float(val)
        except (TypeError, ValueError):
            return 1.0

    def simulate_batch(
        self,
        opportunities: list[dict],
        run_id: str,
    ) -> list[SimulationResult]:
        """
        Simulate a batch of opportunities.

        Args:
            opportunities: List of opportunity dicts from DB
            run_id: Run ID for result storage

        Returns:
            List of SimulationResult objects
        """
        if not opportunities:
            return []

        # Group by (symbol, timeframe) and compute OHLCV date ranges needed
        symbol_tf_ranges = self._compute_ohlcv_ranges(opportunities)

        # Pre-sim backfill: fill any OHLCV gaps before loading
        self._backfill_degraded = False
        try:
            backfill_for_simulation(self.database_url, symbol_tf_ranges)
        except Exception as e:
            logger.warning("Pre-sim backfill failed (continuing with existing data): %s", e)
            self._backfill_degraded = True

        # Batch load OHLCV data
        ohlcv_cache = self._batch_load_ohlcv(symbol_tf_ranges)

        # Simulate each opportunity
        results = []
        for opp in opportunities:
            result = self._simulate_single(opp, ohlcv_cache)
            results.append(result)

        # Store results in database
        self._store_results(results, run_id)

        return results

    def _compute_ohlcv_ranges(
        self,
        opportunities: list[dict],
    ) -> dict[str, tuple[datetime, datetime]]:
        """
        Compute the OHLCV date range needed for each (symbol, timeframe) pair.

        Groups opportunities and finds min start_ts and max end_ts for batch loading.
        """
        ranges: dict[str, tuple[datetime, datetime]] = {}

        # Timeframe to bar duration for calculating end range
        tf_delta = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "30m": timedelta(minutes=30),
            "1h": timedelta(hours=1),
            "4h": timedelta(hours=4),
            "1d": timedelta(days=1),
        }

        for opp in opportunities:
            symbol = opp.get("symbol", "")
            timeframe = opp.get("timeframe", "1h")
            key = f"{symbol}_{timeframe}"

            # Opportunity timestamp
            opp_ts = opp.get("created_ts")
            if isinstance(opp_ts, str):
                opp_ts = datetime.fromisoformat(opp_ts.replace("Z", "+00:00"))
            if opp_ts is None:
                continue

            # Make timezone-aware if not already
            if opp_ts.tzinfo is None:
                opp_ts = opp_ts.replace(tzinfo=timezone.utc)

            # Calculate end ts (opp_ts + max_bars_forward bars)
            bar_delta = tf_delta.get(timeframe, timedelta(hours=1))
            end_ts = opp_ts + (bar_delta * (self.config.max_bars_forward + 10))

            if key not in ranges:
                ranges[key] = (opp_ts, end_ts)
            else:
                existing_start, existing_end = ranges[key]
                ranges[key] = (
                    min(existing_start, opp_ts),
                    max(existing_end, end_ts),
                )

        return ranges

    def _batch_load_ohlcv(
        self,
        symbol_tf_ranges: dict[str, tuple[datetime, datetime]],
    ) -> dict[str, list[dict]]:
        """
        Batch load OHLCV data for all (symbol, timeframe) pairs.

        Returns dict keyed by "SYMBOL_TF" with list of bar dicts.
        """
        cache: dict[str, list[dict]] = {}

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                for key, (start_ts, end_ts) in symbol_tf_ranges.items():
                    symbol, tf = key.rsplit("_", 1)

                    cur.execute("""
                        SELECT DISTINCT ON (bar_start_ts)
                               bar_start_ts, open, high, low, close, volume
                        FROM ohlcv
                        WHERE symbol = %s AND timeframe = %s
                          AND bar_start_ts >= %s AND bar_start_ts < %s
                          AND is_final = TRUE
                        ORDER BY bar_start_ts,
                                 CASE source
                                     WHEN 'ws' THEN 0
                                     WHEN 'binance' THEN 1
                                     ELSE 2
                                 END;
                    """, (symbol, tf, start_ts, end_ts))

                    rows = cur.fetchall()
                    bars = [
                        {
                            "bar_start_ts": row[0],
                            "open": row[1],
                            "high": row[2],
                            "low": row[3],
                            "close": row[4],
                            "volume": row[5],
                        }
                        for row in rows
                    ]
                    cache[key] = bars

        return cache

    def _simulate_single(
        self,
        opp: dict,
        ohlcv_cache: dict[str, list[dict]],
    ) -> SimulationResult:
        """
        Simulate a single opportunity.

        Walks forward through OHLCV data from opportunity timestamp,
        checking for exit conditions at each bar.
        """
        symbol = opp.get("symbol", "")
        timeframe = opp.get("timeframe", "1h")
        setup_id = opp.get("setup_id", "")
        opp_id = opp.get("id", 0)

        # Get direction from payload_json (preferred) or decision column
        payload = opp.get("payload_json")
        if isinstance(payload, str):
            import json
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        if payload is None:
            payload = {}

        direction = payload.get("direction", opp.get("decision", ""))  # 'long' or 'short'

        # Parse opportunity timestamp
        opp_ts = opp.get("created_ts")
        if isinstance(opp_ts, str):
            opp_ts = datetime.fromisoformat(opp_ts.replace("Z", "+00:00"))
        if opp_ts is None:
            return SimulationResult(
                opportunity_id=opp_id,
                symbol=symbol,
                timeframe=timeframe,
                setup_id=setup_id,
                direction=direction,
                opportunity_created_ts=datetime.now(timezone.utc),
                data_available=False,
                error_message="No opportunity timestamp",
            )

        if opp_ts.tzinfo is None:
            opp_ts = opp_ts.replace(tzinfo=timezone.utc)

        # Initialize result
        result = SimulationResult(
            opportunity_id=opp_id,
            symbol=symbol,
            timeframe=timeframe,
            setup_id=setup_id,
            direction=direction,
            opportunity_created_ts=opp_ts,
        )

        # Get OHLCV bars
        key = f"{symbol}_{timeframe}"
        bars = ohlcv_cache.get(key, [])

        if not bars:
            result.data_available = False
            result.error_message = "No OHLCV data available"
            return result

        # Get price levels from opportunity payload (already parsed above)
        entry_price = payload.get("entry_price")
        stop_price = payload.get("stop_price")
        tp1_price = payload.get("tp1_price")
        tp2_price = payload.get("tp2_price")
        r_value = payload.get("r_value")

        if entry_price is None or stop_price is None:
            result.data_available = False
            result.error_message = "Missing entry_price or stop_price in payload"
            return result

        # Apply stop distance factor (Experiment B: tighter stops at simulation time)
        # Safety rule: when per-setup factors are provided, only those setups are
        # overridden. Missing setup keys fall back to neutral 1.0 (not global).
        # This prevents a single strategy tweak from unintentionally becoming
        # a system-wide multiplier.
        _sdf = self._resolve_stop_distance_factor(setup_id)
        if _sdf != 1.0 and stop_price is not None and entry_price is not None:
            _orig_dist = abs(entry_price - stop_price)
            _new_dist = _orig_dist * _sdf
            if direction == "long":
                stop_price = entry_price - _new_dist
            else:
                stop_price = entry_price + _new_dist

        # ── Entry-gate filters (catch pre-filter opportunities) ──────────
        # Microcap short protection: skip shorts on sub-threshold-price assets
        if direction == "short" and entry_price < self.config.min_short_price:
            result.data_available = False
            result.error_message = (
                f"Microcap short blocked: price={entry_price:.6f} "
                f"< min={self.config.min_short_price}"
            )
            return result

        # Stop-width gate: skip trades where stop distance is structurally too wide
        stop_pct = None
        if entry_price > 0:
            stop_pct = abs(stop_price - entry_price) / entry_price
            if stop_pct > self.config.max_stop_pct:
                result.data_available = False
                result.error_message = (
                    f"Stop too wide: {stop_pct:.1%} "
                    f"> max {self.config.max_stop_pct:.0%}"
                )
                return result

        # Causal D1 entry gate: filter risky opportunities using entry-known fields.
        # This replaces outcome-based slicing for primary simulation KPIs.
        if self.config.d1_causal_gate_enabled and setup_id == "D1":
            if (
                self.config.d1_causal_min_entry_price > 0
                and entry_price < self.config.d1_causal_min_entry_price
            ):
                result.data_available = False
                result.error_message = (
                    f"D1 causal gate: entry_price={entry_price:.6f} "
                    f"< min={self.config.d1_causal_min_entry_price:.6f}"
                )
                return result

            if (
                stop_pct is not None
                and self.config.d1_causal_min_stop_pct > 0
                and stop_pct < (self.config.d1_causal_min_stop_pct - 1e-9)
            ):
                result.data_available = False
                result.error_message = (
                    f"D1 causal gate: stop_pct={stop_pct:.2%} "
                    f"< min={self.config.d1_causal_min_stop_pct:.2%}"
                )
                return result

            if self.config.d1_causal_min_score > 0:
                score = opp.get("score")
                score_val = None
                try:
                    if score is not None:
                        score_val = float(score)
                except (TypeError, ValueError):
                    score_val = None

                if score_val is None or score_val < self.config.d1_causal_min_score:
                    result.data_available = False
                    result.error_message = (
                        f"D1 causal gate: score={score_val if score_val is not None else 'NA'} "
                        f"< min={self.config.d1_causal_min_score:.2f}"
                    )
                    return result

        # Always compute r_value as absolute price distance from entry to stop.
        # Payload r_value may be an R-multiple (e.g. D1 stores reward/risk ratio ~6.7)
        # which would break the R-preservation guard that compares absolute distances.
        if direction == "long":
            r_value = entry_price - stop_price
        else:
            r_value = stop_price - entry_price
        if r_value <= 0:
            result.data_available = False
            result.error_message = "Invalid r_value"
            return result

        result.stop_price = stop_price
        result.r_value = r_value

        # Determine fill model: NEXT_OPEN (default) or PAYLOAD_ENTRY.
        # S9 has sub-bar microstructure entry — the payload contains a precise
        # entry price derived from 1s tape analysis at +300s after the hourly
        # close. Using next bar's open would place entry ~55min late,
        # destroying the strategy's math.
        micro = payload.get("microstructure") if isinstance(payload, dict) else None
        use_micro_entry = (
            isinstance(micro, dict)
            and micro.get("entry_type") not in (None, "SKIP")
            and micro.get("suggested_entry_price") is not None
        )

        entry_bar_idx = None
        for i, bar in enumerate(bars):
            bar_ts = bar["bar_start_ts"]
            if bar_ts.tzinfo is None:
                bar_ts = bar_ts.replace(tzinfo=timezone.utc)
            if bar_ts > opp_ts:
                if use_micro_entry and i > 0:
                    # Sub-bar entry: use the bar CONTAINING opp_ts
                    entry_bar_idx = i - 1
                else:
                    # NEXT_OPEN: first bar after signal
                    entry_bar_idx = i
                break

        # Handle opp_ts within or after last bar (micro entry edge case)
        if entry_bar_idx is None and use_micro_entry and bars:
            last_ts = bars[-1]["bar_start_ts"]
            if hasattr(last_ts, 'replace') and last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            if last_ts <= opp_ts:
                entry_bar_idx = len(bars) - 1

        if entry_bar_idx is None:
            result.data_available = False
            result.error_message = "No bar found near opportunity timestamp"
            return result

        entry_bar = bars[entry_bar_idx]

        # Staleness check (NEXT_OPEN only — micro entry bar contains opp_ts)
        if not use_micro_entry:
            tf_delta = {
                "1m": timedelta(minutes=1), "5m": timedelta(minutes=5),
                "15m": timedelta(minutes=15), "30m": timedelta(minutes=30),
                "1h": timedelta(hours=1), "4h": timedelta(hours=4),
                "1d": timedelta(days=1),
            }
            bar_duration = tf_delta.get(timeframe, timedelta(hours=1))
            max_entry_delay = bar_duration * 3
            entry_bar_ts = entry_bar["bar_start_ts"]
            if entry_bar_ts.tzinfo is None:
                entry_bar_ts = entry_bar_ts.replace(tzinfo=timezone.utc)
            entry_delay = entry_bar_ts - opp_ts
            if entry_delay > max_entry_delay:
                result.data_available = False
                result.error_message = (
                    f"No OHLCV data near signal time "
                    f"(first bar {entry_delay} after signal, max {max_entry_delay})"
                )
                return result

        if use_micro_entry:
            # Sub-bar microstructure entry: use the tape-derived price
            actual_entry_price = micro["suggested_entry_price"]
        else:
            # Standard NEXT_OPEN: entry at next bar's open
            actual_entry_price = entry_bar["open"]

        # Apply entry slippage
        slip_bps = self.config.slippage_entry_bps
        if direction == "long":
            entry_with_slip = actual_entry_price * (1 + slip_bps / 10000)
        else:
            entry_with_slip = actual_entry_price * (1 - slip_bps / 10000)

        # Recalculate r_value with actual fill price
        if direction == "long":
            actual_r_value = entry_with_slip - stop_price
        else:
            actual_r_value = stop_price - entry_with_slip

        if actual_r_value <= 0:
            result.data_available = False
            result.error_message = "R-value invalid after slippage"
            return result

        # Guard: reject uneconomic trades where fees dominate risk
        fee_per_unit = 2 * entry_with_slip * self.config.fee_taker_bps / 10000
        if fee_per_unit > self.config.max_fee_r_ratio * actual_r_value:
            result.data_available = False
            result.error_message = (
                f"Uneconomic: fees/R={fee_per_unit / actual_r_value:.2f} "
                f"> max {self.config.max_fee_r_ratio}"
            )
            return result

        # Guard: reject trades where entry gap consumed too much of original R
        if r_value > 0 and actual_r_value < self.config.min_r_preservation * r_value:
            result.data_available = False
            result.error_message = (
                f"Adverse gap: R degraded {actual_r_value / r_value:.0%} "
                f"(min {self.config.min_r_preservation:.0%})"
            )
            return result

        # Guard 1: invalidate TP1/TP2 if on wrong side of actual entry
        # (happens when next_bar_open gap pushes entry past signal's TP)
        tp_invalidated = False
        tp_reasons = []
        if direction == "long":
            if tp1_price is not None and tp1_price <= entry_with_slip:
                tp1_price = None
                tp_invalidated = True
                tp_reasons.append("WRONG_SIDE")
            if tp2_price is not None and tp2_price <= entry_with_slip:
                tp2_price = None
                tp_invalidated = True
        else:
            if tp1_price is not None and tp1_price >= entry_with_slip:
                tp1_price = None
                tp_invalidated = True
                tp_reasons.append("WRONG_SIDE")
            if tp2_price is not None and tp2_price >= entry_with_slip:
                tp2_price = None
                tp_invalidated = True

        # Guard 2: invalidate TP1/TP2 if too close to entry to cover costs
        # Minimum distance = exit slippage + round-trip fees (entry + exit)
        min_tp_distance = entry_with_slip * (
            self.config.slippage_exit_bps + 2 * self.config.fee_taker_bps
        ) / 10000
        if direction == "long":
            if tp1_price is not None and (tp1_price - entry_with_slip) < min_tp_distance:
                tp1_price = None
                tp_invalidated = True
                tp_reasons.append("BELOW_COST")
            if tp2_price is not None and (tp2_price - entry_with_slip) < min_tp_distance:
                tp2_price = None
                tp_invalidated = True
        else:
            if tp1_price is not None and (entry_with_slip - tp1_price) < min_tp_distance:
                tp1_price = None
                tp_invalidated = True
                tp_reasons.append("BELOW_COST")
            if tp2_price is not None and (entry_with_slip - tp2_price) < min_tp_distance:
                tp2_price = None
                tp_invalidated = True

        if tp_invalidated:
            result.tp_invalidated = True
            result.tp_invalidation_reason = "+".join(tp_reasons) if tp_reasons else "WRONG_SIDE"

        result.tp1_price = tp1_price
        result.tp2_price = tp2_price

        # Position sizing
        position_size = self.config.position_size_usd / entry_with_slip

        result.entry_ts = entry_bar["bar_start_ts"]
        result.entry_price = actual_entry_price
        result.entry_price_with_slippage = entry_with_slip
        result.position_size = position_size
        result.r_value = actual_r_value

        # Time stop from setup or config default
        time_stop_bars = payload.get("time_stop_bars", self.config.time_stop_bars)

        # Per-setup exit config (e.g. D1 uses no fail-fast/trailing)
        exit_cfg = SETUP_EXIT_CONFIGS.get(setup_id, self.config.exit_config)
        if exit_cfg is None:
            exit_cfg = ExitConfig(
                intrabar_policy=self.config.intrabar_policy,
                one_event_per_bar=self.config.one_event_per_bar,
            )

        # Walk forward through bars checking for exit.
        # Align with backtest/paper behavior: TP1 can be a partial close
        # (50%) with a runner to TP2 and stop moved to breakeven.
        hwm = None
        half_r_touched = False
        exit_reason = None
        exit_price = None
        exit_ts = None
        bars_held = 0
        sim_stop_price = stop_price

        remaining_size = position_size
        tp1_taken = False
        tp1_close_size = 0.0
        tp1_exit_price = None
        tp1_exit_with_slip = None
        realized_gross = 0.0
        realized_exit_fees = 0.0
        fee_bps = self.config.fee_taker_bps
        exit_slip_bps = self.config.slippage_exit_bps

        # MAE/MFE tracking (using high/low, not close)
        mae_price = entry_with_slip
        mfe_price = entry_with_slip
        mae_bar = 0
        mfe_bar = 0

        max_bar_idx = min(
            entry_bar_idx + self.config.max_bars_forward,
            len(bars) - 1
        )

        for i in range(entry_bar_idx, max_bar_idx + 1):
            bar = bars[i]
            is_entry_bar = (i == entry_bar_idx)
            if not is_entry_bar:
                bars_held += 1

            # Update MAE/MFE using high/low (captures intra-bar excursions)
            if direction == "long":
                if bar["low"] < mae_price:
                    mae_price = bar["low"]
                    mae_bar = bars_held
                if bar["high"] > mfe_price:
                    mfe_price = bar["high"]
                    mfe_bar = bars_held
            else:  # short
                if bar["high"] > mae_price:
                    mae_price = bar["high"]
                    mae_bar = bars_held
                if bar["low"] < mfe_price:
                    mfe_price = bar["low"]
                    mfe_bar = bars_held

            exit_result, new_hwm, new_half_r = check_exit_simple(
                side=direction,
                bar=bar,
                entry_price=entry_with_slip,
                stop_price=sim_stop_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                r_value=actual_r_value,
                bars_held=bars_held,
                max_bars=time_stop_bars,
                high_water_mark=hwm,
                half_r_touched=half_r_touched,
                config=exit_cfg,
                is_entry_bar=is_entry_bar,
            )

            hwm = new_hwm
            half_r_touched = new_half_r

            if exit_result:
                leg_reason, leg_exit_price = exit_result
                leg_exit_ts = bar["bar_start_ts"]

                # Partial TP1 close: close 50%, keep runner to TP2,
                # move stop to entry (breakeven) for remainder.
                if leg_reason == "TP1" and tp2_price is not None and not tp1_taken:
                    close_size = remaining_size * 0.5
                    if close_size > 0:
                        if direction == "long":
                            leg_exit_with_slip = leg_exit_price * (1 - exit_slip_bps / 10000)
                            leg_pnl_gross = (leg_exit_with_slip - entry_with_slip) * close_size
                        else:
                            leg_exit_with_slip = leg_exit_price * (1 + exit_slip_bps / 10000)
                            leg_pnl_gross = (entry_with_slip - leg_exit_with_slip) * close_size

                        leg_exit_fee = leg_exit_with_slip * close_size * (fee_bps / 10000)
                        realized_gross += leg_pnl_gross
                        realized_exit_fees += leg_exit_fee

                        tp1_taken = True
                        tp1_close_size = close_size
                        tp1_exit_price = leg_exit_price
                        tp1_exit_with_slip = leg_exit_with_slip
                        remaining_size -= close_size

                        # Runner leg settings after TP1 partial.
                        sim_stop_price = entry_with_slip
                        tp1_price = None
                        continue

                exit_reason = leg_reason
                exit_price = leg_exit_price
                exit_ts = leg_exit_ts
                break

        # If no exit triggered, force close at last bar's close
        if exit_reason is None:
            last_bar = bars[max_bar_idx] if max_bar_idx < len(bars) else bars[-1]
            exit_reason = "TIME_STOP"
            exit_price = last_bar["close"]
            exit_ts = last_bar["bar_start_ts"]

        # Final leg (or full leg if TP1 partial never triggered)
        # Detect gap-through for slippage and observability
        gap_through_info = None
        if exit_reason == "STOP_LOSS":
            # Normal stop fills at stop_price (limit order) — no slippage.
            # Gap-through fills at bar_open (market sweep) — apply directional slippage.
            is_gap_stop = (exit_price != sim_stop_price)
            if is_gap_stop:
                # Gap-through: sweeps thin order book, apply directional slippage
                if direction == "long":
                    exit_with_slip = exit_price * (1 - exit_slip_bps / 10000)
                else:
                    exit_with_slip = exit_price * (1 + exit_slip_bps / 10000)
                gap_through_info = {
                    "type": "adverse",
                    "fill_price": exit_price,
                    "stop_price": sim_stop_price,
                    "gap_r": round(abs(exit_price - sim_stop_price) / actual_r_value, 4) if actual_r_value > 0 else None,
                }
            else:
                exit_with_slip = exit_price  # limit order at stop_price, no slippage
        elif exit_reason in ("TP1", "TP2"):
            # Detect TP windfall gaps for observability
            tp_ref = tp1_price if exit_reason == "TP1" else tp2_price
            is_windfall = False
            if tp_ref is not None:
                if direction == "long" and exit_price > tp_ref:
                    is_windfall = True
                elif direction == "short" and exit_price < tp_ref:
                    is_windfall = True
            if is_windfall:
                gap_through_info = {
                    "type": "windfall",
                    "fill_price": exit_price,
                    "tp_price": tp_ref,
                    "windfall_r": round(abs(exit_price - tp_ref) / actual_r_value, 4) if actual_r_value > 0 else None,
                }
            # All non-STOP_LOSS exits get directional slippage
            if direction == "long":
                exit_with_slip = exit_price * (1 - exit_slip_bps / 10000)
            else:
                exit_with_slip = exit_price * (1 + exit_slip_bps / 10000)
        else:
            # TIME_STOP, TRAIL_STOP, FAIL_FAST — normal directional slippage
            if direction == "long":
                exit_with_slip = exit_price * (1 - exit_slip_bps / 10000)
            else:
                exit_with_slip = exit_price * (1 + exit_slip_bps / 10000)

        if direction == "long":
            final_leg_gross = (exit_with_slip - entry_with_slip) * remaining_size
        else:
            final_leg_gross = (entry_with_slip - exit_with_slip) * remaining_size
        final_leg_exit_fee = exit_with_slip * remaining_size * (fee_bps / 10000)

        # Fees (entry + exits on each closed leg)
        entry_fee = entry_with_slip * position_size * (fee_bps / 10000)
        pnl_gross = realized_gross + final_leg_gross
        total_fees = entry_fee + realized_exit_fees + final_leg_exit_fee

        pnl_net = pnl_gross - total_fees

        # R-multiple
        risk_per_unit = actual_r_value * position_size
        r_multiple = pnl_net / risk_per_unit if risk_per_unit > 0 else 0

        # Represent the trade with weighted average exit prices when TP1 partial
        # closes happened, so reported price fields remain economically coherent.
        if tp1_taken and tp1_close_size > 0:
            weighted_exit_price = (
                (tp1_exit_price * tp1_close_size) + (exit_price * remaining_size)
            ) / position_size
            weighted_exit_with_slip = (
                (tp1_exit_with_slip * tp1_close_size) + (exit_with_slip * remaining_size)
            ) / position_size
            result.exit_reason = f"TP1+{exit_reason}"
            result.exit_price = weighted_exit_price
            result.exit_price_with_slippage = weighted_exit_with_slip
        else:
            result.exit_reason = exit_reason
            result.exit_price = exit_price
            result.exit_price_with_slippage = exit_with_slip

        result.exit_ts = exit_ts
        result.bars_held = bars_held
        result.pnl_gross = pnl_gross
        result.fees = total_fees
        result.pnl_net = pnl_net
        result.r_multiple = r_multiple

        # Extract trace data from opportunity payload for correlation analysis
        result.trace_json = self._extract_trace_from_payload(
            payload, direction, result.exit_reason, bars_held
        )

        # Compute MAE/MFE R-multiples and store in trace_json
        stop_distance = abs(entry_with_slip - stop_price) if stop_price else None
        if result.trace_json and stop_distance and stop_distance > 0:
            dir_mult = 1.0 if direction == "long" else -1.0
            result.trace_json["mae_mfe"] = {
                "mae_price": round(mae_price, 8),
                "mfe_price": round(mfe_price, 8),
                "mae_r": round((mae_price - entry_with_slip) * dir_mult / stop_distance, 4),
                "mfe_r": round((mfe_price - entry_with_slip) * dir_mult / stop_distance, 4),
                "mae_pct": round((mae_price - entry_with_slip) / entry_with_slip * dir_mult * 100, 4),
                "mfe_pct": round((mfe_price - entry_with_slip) / entry_with_slip * dir_mult * 100, 4),
                "mae_bar": mae_bar,
                "mfe_bar": mfe_bar,
                "bars_to_mae": mae_bar,
                "bars_to_mfe": mfe_bar,
            }

        # Merge gap-through info into trace
        if gap_through_info and result.trace_json:
            result.trace_json["gap_through"] = gap_through_info

        # Merge TP invalidation into trace
        if result.tp_invalidated and result.trace_json:
            result.trace_json["tp_invalidated"] = True
            result.trace_json["tp_invalidation_reason"] = result.tp_invalidation_reason

        return result

    def _extract_trace_from_payload(
        self,
        payload: dict,
        direction: str,
        exit_reason: str,
        bars_held: int,
    ) -> dict:
        """
        Extract trace data from opportunity payload for correlation analysis.

        Maps payload fields to the standard trace_json format used by backtests.
        """
        trace = {
            "conditions_met": {},
            "indicator_values": {},
            "state_snapshot": {},
            "score_components": {},
            "exit_reason": exit_reason,
            "exit_conditions": {"bars_held": bars_held},
        }

        # Extract indicator values if present in payload
        # Common patterns: payload may have these directly or nested
        indicator_keys = [
            "bb_width_pctile", "clv", "volume_z", "atr", "rsi",
            "ema_9", "ema_21", "sma_50", "sma_200", "momentum",
            "bb_upper", "bb_lower", "bb_mid", "kc_upper", "kc_lower",
        ]
        for key in indicator_keys:
            if key in payload and payload[key] is not None:
                trace["indicator_values"][key] = payload[key]

        # Extract state snapshot values
        state_keys = [
            "squeeze_on", "squeeze_age", "bias_score", "regime",
            "trend_strength", "volatility_regime", "box_high", "box_low",
        ]
        for key in state_keys:
            if key in payload and payload[key] is not None:
                trace["state_snapshot"][key] = payload[key]

        # Extract score components
        score_keys = [
            "severity", "participation", "bias_strength", "quality_score",
            "score", "total_score", "rank",
        ]
        for key in score_keys:
            if key in payload and payload[key] is not None:
                trace["score_components"][key] = payload[key]

        # Extract from nested structures if present
        if "indicator_values" in payload and isinstance(payload["indicator_values"], dict):
            trace["indicator_values"].update(payload["indicator_values"])

        if "state_snapshot" in payload and isinstance(payload["state_snapshot"], dict):
            trace["state_snapshot"].update(payload["state_snapshot"])

        if "score_components" in payload and isinstance(payload["score_components"], dict):
            trace["score_components"].update(payload["score_components"])

        # Extract why_items as conditions
        if "why_items" in payload and isinstance(payload["why_items"], list):
            for i, item in enumerate(payload["why_items"][:10]):
                trace["conditions_met"][f"why_{i}"] = 1.0

        return trace

    def _store_results(self, results: list[SimulationResult], run_id: str):
        """Store simulation results in the database."""
        import json

        if not results:
            return

        with self._get_conn() as conn:
            with conn.cursor() as cur:
                for r in results:
                    # Serialize trace_json
                    trace_json_str = json.dumps(r.trace_json) if r.trace_json else None

                    cur.execute("""
                        INSERT INTO simulation_results (
                            id, run_id, opportunity_id, symbol, timeframe,
                            setup_id, direction, opportunity_created_ts,
                            entry_ts, entry_price, entry_price_with_slippage,
                            position_size, stop_price, tp1_price, tp2_price,
                            r_value, exit_ts, exit_price, exit_price_with_slippage,
                            exit_reason, bars_held, pnl_gross, fees, pnl_net,
                            r_multiple, data_available, error_message, trace_json
                        ) VALUES (
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s
                        );
                    """, (
                        str(uuid.uuid4()), run_id, r.opportunity_id, r.symbol,
                        r.timeframe, r.setup_id, r.direction, r.opportunity_created_ts,
                        r.entry_ts, r.entry_price, r.entry_price_with_slippage,
                        r.position_size, r.stop_price, r.tp1_price, r.tp2_price,
                        r.r_value, r.exit_ts, r.exit_price, r.exit_price_with_slippage,
                        r.exit_reason, r.bars_held, r.pnl_gross, r.fees, r.pnl_net,
                        r.r_multiple, r.data_available, r.error_message, trace_json_str,
                    ))
            conn.commit()

    def compute_summary(
        self,
        results: list[SimulationResult],
        position_size_usd: float,
        starting_capital: float = 10000.0,
    ) -> dict:
        """
        Compute summary statistics from simulation results.

        Returns dict with win_rate, expectancy, profit_factor, etc.
        """
        valid_results = [r for r in results if r.data_available and r.pnl_net is not None]

        if not valid_results:
            return {
                "total_opportunities": len(results),
                "simulated_count": 0,
                "skipped_no_data": len(results) - len(valid_results),
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0,
                "profit_factor": 0,
                "expectancy_r": 0,
                "avg_r": 0,
                "total_r": 0,
                "max_r": 0,
                "min_r": 0,
                "gross_pnl": 0,
                "total_fees": 0,
                "total_slippage": 0,
                "total_pnl_net": 0,
                "position_size_usd": position_size_usd,
                "max_drawdown_pct": 0,
                "starting_capital": round(starting_capital, 2),
                "final_equity": round(starting_capital, 2),
                "return_pct": 0,
                "bars_processed": 0,
                "assets_processed": 0,
                "assets_skipped": 0,
                "sharpe_ratio": 0,
                "sortino_ratio": 0,
                "calmar_ratio": 0,
                "max_consecutive_wins": 0,
                "max_consecutive_losses": 0,
                "avg_holding_bars": 0,
                "avg_win_r": 0,
                "avg_loss_r": 0,
                "payoff_ratio": 0,
                "per_setup": {},
                "per_symbol": {},
                "exit_reasons": {},
                "tp_invalidated_count": 0,
                "error_breakdown": {},
            }

        winners = [r for r in valid_results if r.pnl_net > 0]
        losers = [r for r in valid_results if r.pnl_net <= 0]

        total_pnl = sum(r.pnl_net for r in valid_results)
        total_fees = sum((r.fees or 0.0) for r in valid_results)
        gross_pnl = total_pnl + total_fees
        bars_processed = sum((r.bars_held or 0) for r in valid_results)
        r_multiples = [r.r_multiple or 0.0 for r in valid_results]
        total_r = sum(r_multiples)
        avg_r = total_r / len(r_multiples) if r_multiples else 0

        gross_profit = sum(r.pnl_net for r in winners)
        gross_loss = abs(sum(r.pnl_net for r in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        win_rate = len(winners) / len(valid_results) if valid_results else 0

        # Slippage impact in quote currency terms
        total_slippage = 0.0
        for r in valid_results:
            if (
                r.entry_price is not None and r.entry_price_with_slippage is not None
                and r.exit_price is not None and r.exit_price_with_slippage is not None
                and r.position_size is not None
            ):
                entry_slip = abs(r.entry_price_with_slippage - r.entry_price) * r.position_size
                exit_slip = abs(r.exit_price_with_slippage - r.exit_price) * r.position_size
                total_slippage += (entry_slip + exit_slip)

        # P2-style extended metrics for UI parity with backtest summary
        bars_held_list = [r.bars_held or 0 for r in valid_results]
        avg_holding = sum(bars_held_list) / len(bars_held_list) if bars_held_list else 0

        win_rs = [r.r_multiple or 0.0 for r in winners]
        loss_rs = [r.r_multiple or 0.0 for r in losers]
        avg_win_r = sum(win_rs) / len(win_rs) if win_rs else 0
        avg_loss_r = sum(loss_rs) / len(loss_rs) if loss_rs else 0
        payoff_ratio = abs(avg_win_r / avg_loss_r) if avg_loss_r != 0 else 0

        max_con_wins = 0
        max_con_losses = 0
        cur_wins = 0
        cur_losses = 0
        for r in valid_results:
            if (r.pnl_net or 0) > 0:
                cur_wins += 1
                cur_losses = 0
                max_con_wins = max(max_con_wins, cur_wins)
            else:
                cur_losses += 1
                cur_wins = 0
                max_con_losses = max(max_con_losses, cur_losses)

        import math
        sharpe = 0.0
        sortino = 0.0
        if len(r_multiples) > 1:
            mean_r = avg_r
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in r_multiples) / (len(r_multiples) - 1))
            sharpe = (mean_r / std_r) if std_r > 0 else 0.0
            downside = [min(0.0, r) for r in r_multiples]
            downside_var = sum(d ** 2 for d in downside) / (len(r_multiples) - 1)
            downside_std = math.sqrt(downside_var)
            sortino = (mean_r / downside_std) if downside_std > 0 else 0.0

        # Mark-to-market style equity path using sequential simulated trades
        equity = starting_capital
        peak = starting_capital
        max_drawdown_pct = 0.0
        for r in valid_results:
            equity += (r.pnl_net or 0.0)
            peak = max(peak, equity)
            if peak > 0:
                dd = (peak - equity) / peak * 100
                max_drawdown_pct = max(max_drawdown_pct, dd)
        final_equity = equity
        return_pct = ((final_equity - starting_capital) / starting_capital * 100) if starting_capital > 0 else 0.0
        calmar = (return_pct / max_drawdown_pct) if max_drawdown_pct > 0 else 0.0

        # Per-setup breakdown
        per_setup: dict[str, dict] = {}
        for r in valid_results:
            sid = r.setup_id
            if sid not in per_setup:
                per_setup[sid] = {
                    "trades": 0, "wins": 0, "pnl_net": 0.0, "total_r": 0.0
                }
            per_setup[sid]["trades"] += 1
            if r.pnl_net > 0:
                per_setup[sid]["wins"] += 1
            per_setup[sid]["pnl_net"] += r.pnl_net
            per_setup[sid]["total_r"] += r.r_multiple

        for sid in per_setup:
            s = per_setup[sid]
            s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0
            s["avg_r"] = s["total_r"] / s["trades"] if s["trades"] > 0 else 0
            s["pnl_net"] = round(s["pnl_net"], 2)
            s["total_r"] = round(s["total_r"], 2)

        # Per-symbol breakdown (trim to top 20 by |PnL|)
        per_symbol: dict[str, dict] = {}
        for r in valid_results:
            sym = r.symbol
            if sym not in per_symbol:
                per_symbol[sym] = {"trades": 0, "wins": 0, "pnl_net": 0.0, "total_r": 0.0}
            per_symbol[sym]["trades"] += 1
            if (r.pnl_net or 0) > 0:
                per_symbol[sym]["wins"] += 1
            per_symbol[sym]["pnl_net"] += (r.pnl_net or 0.0)
            per_symbol[sym]["total_r"] += (r.r_multiple or 0.0)
        for sym in per_symbol:
            s = per_symbol[sym]
            s["win_rate"] = s["wins"] / s["trades"] if s["trades"] > 0 else 0
            s["avg_r"] = s["total_r"] / s["trades"] if s["trades"] > 0 else 0
            s["pnl_net"] = round(s["pnl_net"], 2)
            s["total_r"] = round(s["total_r"], 2)
        if len(per_symbol) > 20:
            top_syms = sorted(per_symbol, key=lambda s: abs(per_symbol[s]["pnl_net"]), reverse=True)[:20]
            per_symbol = {s: per_symbol[s] for s in top_syms}

        # Exit reason distribution
        exit_reasons: dict[str, int] = {}
        for r in valid_results:
            reason = r.exit_reason or "UNKNOWN"
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        # Error breakdown (filtered/skipped trades by category)
        error_breakdown: dict[str, int] = {}
        for r in results:
            if not r.data_available and r.error_message:
                category = r.error_message.split(":")[0]
                error_breakdown[category] = error_breakdown.get(category, 0) + 1

        # TP invalidation count
        tp_invalidated_count = sum(1 for r in valid_results if r.tp_invalidated)
        valid_symbols = {r.symbol for r in valid_results}
        skipped_symbols = {r.symbol for r in results if not r.data_available and r.symbol not in valid_symbols}

        return {
            "total_opportunities": len(results),
            "simulated_count": len(valid_results),
            "skipped_no_data": len(results) - len(valid_results),
            "winning_trades": len(winners),
            "losing_trades": len(losers),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 3) if profit_factor != float('inf') else None,
            "expectancy_r": round(avg_r, 4),
            "avg_r": round(avg_r, 4),
            "total_r": round(total_r, 2),
            "max_r": round(max(r_multiples), 4) if r_multiples else 0,
            "min_r": round(min(r_multiples), 4) if r_multiples else 0,
            "gross_pnl": round(gross_pnl, 2),
            "total_fees": round(total_fees, 2),
            "total_slippage": round(total_slippage, 2),
            "total_pnl_net": round(total_pnl, 2),
            "position_size_usd": position_size_usd,
            "max_drawdown_pct": round(max_drawdown_pct, 2),
            "starting_capital": round(starting_capital, 2),
            "final_equity": round(final_equity, 2),
            "return_pct": round(return_pct, 2),
            "bars_processed": int(bars_processed),
            "assets_processed": len(valid_symbols),
            "assets_skipped": len(skipped_symbols),
            "sharpe_ratio": round(sharpe, 3),
            "sortino_ratio": round(sortino, 3),
            "calmar_ratio": round(calmar, 3),
            "max_consecutive_wins": max_con_wins,
            "max_consecutive_losses": max_con_losses,
            "avg_holding_bars": round(avg_holding, 1),
            "avg_win_r": round(avg_win_r, 3),
            "avg_loss_r": round(avg_loss_r, 3),
            "payoff_ratio": round(payoff_ratio, 3),
            "per_setup": per_setup,
            "per_symbol": per_symbol,
            "exit_reasons": exit_reasons,
            "tp_invalidated_count": tp_invalidated_count,
            "error_breakdown": error_breakdown,
        }
