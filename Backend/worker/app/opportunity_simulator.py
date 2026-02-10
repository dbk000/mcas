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

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import uuid

import psycopg

from exit_checker import check_exit_simple

logger = logging.getLogger(__name__)


@dataclass
class SimulationConfig:
    """Configuration for opportunity simulation."""
    position_size_usd: float = 1000.0
    slippage_entry_bps: float = 5.0
    slippage_exit_bps: float = 5.0
    fee_taker_bps: float = 4.0
    max_bars_forward: int = 100  # Max bars to simulate forward
    time_stop_bars: int = 50    # Default time stop if not specified


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
                        SELECT bar_start_ts, open, high, low, close, volume
                        FROM ohlcv
                        WHERE symbol = %s AND timeframe = %s
                        AND bar_start_ts >= %s AND bar_start_ts <= %s
                        AND is_final = TRUE
                        ORDER BY bar_start_ts;
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

        # Calculate r_value if not present
        if r_value is None:
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

        # Find the first bar after opportunity timestamp (NEXT_OPEN fill)
        entry_bar_idx = None
        for i, bar in enumerate(bars):
            bar_ts = bar["bar_start_ts"]
            if bar_ts.tzinfo is None:
                bar_ts = bar_ts.replace(tzinfo=timezone.utc)
            if bar_ts > opp_ts:
                entry_bar_idx = i
                break

        if entry_bar_idx is None:
            result.data_available = False
            result.error_message = "No bar found after opportunity timestamp"
            return result

        # Entry at next bar's open (NEXT_OPEN fill model)
        entry_bar = bars[entry_bar_idx]
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

        # Walk forward through bars checking for exit
        hwm = None
        half_r_touched = False
        exit_reason = None
        exit_price = None
        exit_ts = None
        bars_held = 0

        max_bar_idx = min(
            entry_bar_idx + self.config.max_bars_forward,
            len(bars) - 1
        )

        for i in range(entry_bar_idx + 1, max_bar_idx + 1):
            bar = bars[i]
            bars_held += 1

            exit_result, new_hwm, new_half_r = check_exit_simple(
                side=direction,
                bar=bar,
                entry_price=entry_with_slip,
                stop_price=stop_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                r_value=actual_r_value,
                bars_held=bars_held,
                max_bars=time_stop_bars,
                high_water_mark=hwm,
                half_r_touched=half_r_touched,
            )

            hwm = new_hwm
            half_r_touched = new_half_r

            if exit_result:
                exit_reason, exit_price = exit_result
                exit_ts = bar["bar_start_ts"]
                break

        # If no exit triggered, force close at last bar's close
        if exit_reason is None:
            last_bar = bars[max_bar_idx] if max_bar_idx < len(bars) else bars[-1]
            exit_reason = "TIME_STOP"
            exit_price = last_bar["close"]
            exit_ts = last_bar["bar_start_ts"]

        result.exit_ts = exit_ts
        result.exit_price = exit_price
        result.exit_reason = exit_reason
        result.bars_held = bars_held

        # Apply exit slippage (NOT for stop losses - fill is already modeled)
        if exit_reason != "STOP_LOSS":
            exit_slip_bps = self.config.slippage_exit_bps
            if direction == "long":
                exit_with_slip = exit_price * (1 - exit_slip_bps / 10000)
            else:
                exit_with_slip = exit_price * (1 + exit_slip_bps / 10000)
        else:
            exit_with_slip = exit_price

        result.exit_price_with_slippage = exit_with_slip

        # Calculate PnL
        if direction == "long":
            pnl_gross = (exit_with_slip - entry_with_slip) * position_size
        else:
            pnl_gross = (entry_with_slip - exit_with_slip) * position_size

        # Fees (entry + exit)
        fee_bps = self.config.fee_taker_bps
        entry_fee = entry_with_slip * position_size * (fee_bps / 10000)
        exit_fee = exit_with_slip * position_size * (fee_bps / 10000)
        total_fees = entry_fee + exit_fee

        pnl_net = pnl_gross - total_fees

        # R-multiple
        risk_per_unit = actual_r_value * position_size
        r_multiple = pnl_net / risk_per_unit if risk_per_unit > 0 else 0

        result.pnl_gross = pnl_gross
        result.fees = total_fees
        result.pnl_net = pnl_net
        result.r_multiple = r_multiple

        # Extract trace data from opportunity payload for correlation analysis
        result.trace_json = self._extract_trace_from_payload(payload, direction, exit_reason, bars_held)

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
                "win_rate": 0,
                "profit_factor": 0,
                "expectancy_r": 0,
                "avg_r": 0,
                "total_r": 0,
                "total_pnl_net": 0,
                "position_size_usd": position_size_usd,
                "per_setup": {},
                "exit_reasons": {},
            }

        winners = [r for r in valid_results if r.pnl_net > 0]
        losers = [r for r in valid_results if r.pnl_net <= 0]

        total_pnl = sum(r.pnl_net for r in valid_results)
        r_multiples = [r.r_multiple for r in valid_results]
        total_r = sum(r_multiples)
        avg_r = total_r / len(r_multiples) if r_multiples else 0

        gross_profit = sum(r.pnl_net for r in winners)
        gross_loss = abs(sum(r.pnl_net for r in losers))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

        win_rate = len(winners) / len(valid_results) if valid_results else 0

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

        # Exit reason distribution
        exit_reasons: dict[str, int] = {}
        for r in valid_results:
            reason = r.exit_reason or "UNKNOWN"
            exit_reasons[reason] = exit_reasons.get(reason, 0) + 1

        # TP invalidation count
        tp_invalidated_count = sum(1 for r in valid_results if r.tp_invalidated)

        return {
            "total_opportunities": len(results),
            "simulated_count": len(valid_results),
            "skipped_no_data": len(results) - len(valid_results),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 3) if profit_factor != float('inf') else None,
            "expectancy_r": round(avg_r, 4),
            "avg_r": round(avg_r, 4),
            "total_r": round(total_r, 2),
            "total_pnl_net": round(total_pnl, 2),
            "position_size_usd": position_size_usd,
            "per_setup": per_setup,
            "exit_reasons": exit_reasons,
            "tp_invalidated_count": tp_invalidated_count,
        }
