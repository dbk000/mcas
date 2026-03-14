"""
Feature Extractor
=================

Extracts namespaced features from run_trades.trace_json for correlation analysis.

Namespace conventions:
- ind.*    : indicator values (e.g., ind.ATR_14, ind.RSI_14)
- state.*  : state snapshot values (e.g., state.squeeze_age, state.bias_score)
- score.*  : score components (e.g., score.participation, score.severity)
"""

import json
import logging
from dataclasses import dataclass, field
from contextlib import contextmanager
from typing import Optional

import psycopg

from governance.persistence import get_conn as _pool_get_conn

logger = logging.getLogger(__name__)


@dataclass
class TradeFeatures:
    """Extracted features from a single trade."""
    trade_id: str
    run_id: str
    setup_id: str
    r_multiple: float
    pnl_net: float
    is_winner: bool
    exit_reason: str
    bars_held: int
    features: dict[str, float] = field(default_factory=dict)


class FeatureExtractor:
    """
    Extracts features from run_trades for correlation analysis.

    Usage:
        extractor = FeatureExtractor(database_url)
        trades = extractor.extract_from_runs(["uuid1", "uuid2"])
    """

    def __init__(self, database_url: str):
        self.database_url = database_url

    @contextmanager
    def _conn(self):
        """Context manager for database connections from the pool."""
        with _pool_get_conn() as conn:
            yield conn

    def extract_from_runs(
        self,
        run_ids: list[str],
        setup_id: Optional[str] = None,
    ) -> list[TradeFeatures]:
        """
        Load trades from run_trades and simulation_results, extract features from trace_json.

        Args:
            run_ids: List of run UUIDs to analyze
            setup_id: Optional filter for specific setup (e.g., "S1")

        Returns:
            List of TradeFeatures with flattened, namespaced features
        """
        if not run_ids:
            return []

        trades = []

        with self._conn() as conn:
            with conn.cursor() as cur:
                # Query 1: BACKTEST trades from run_trades
                query = """
                    SELECT
                        id,
                        run_id,
                        setup_id,
                        r_multiple,
                        pnl,
                        exit_reason,
                        bars_held,
                        trace_json
                    FROM run_trades
                    WHERE run_id = ANY(%s)
                      AND status = 'CLOSED'
                      AND exit_reason IS NOT NULL
                """
                params = [run_ids]

                if setup_id:
                    query += " AND setup_id = %s"
                    params.append(setup_id)

                query += " ORDER BY created_at"

                cur.execute(query, params)
                rows = cur.fetchall()

                for row in rows:
                    trade = self._row_to_trade_features(row, source="run_trades")
                    if trade:
                        trades.append(trade)

                # Query 2: SIMULATION results from simulation_results
                sim_query = """
                    SELECT
                        id,
                        run_id,
                        setup_id,
                        r_multiple,
                        pnl_net,
                        exit_reason,
                        bars_held,
                        trace_json
                    FROM simulation_results
                    WHERE run_id = ANY(%s)
                      AND data_available = TRUE
                      AND exit_reason IS NOT NULL
                """
                sim_params = [run_ids]

                if setup_id:
                    sim_query += " AND setup_id = %s"
                    sim_params.append(setup_id)

                sim_query += " ORDER BY created_at"

                cur.execute(sim_query, sim_params)
                sim_rows = cur.fetchall()

                for row in sim_rows:
                    trade = self._row_to_trade_features(row, source="simulation_results")
                    if trade:
                        trades.append(trade)

        logger.info(f"Extracted {len(trades)} trades from {len(run_ids)} runs")
        return trades

    def _row_to_trade_features(self, row: tuple, source: str) -> Optional[TradeFeatures]:
        """Convert a database row to TradeFeatures."""
        (
            trade_id,
            run_id,
            setup_id_val,
            r_multiple,
            pnl,
            exit_reason,
            bars_held,
            trace_json,
        ) = row

        # Parse trace_json
        trace = self._parse_trace(trace_json)

        # Flatten trace into namespaced features
        features = self._flatten_trace(trace, setup_id_val or "")

        # Determine if winner (R >= 0 counts as winner for analysis)
        is_winner = (r_multiple or 0) >= 0

        return TradeFeatures(
            trade_id=str(trade_id),
            run_id=str(run_id),
            setup_id=setup_id_val or "",
            r_multiple=r_multiple or 0.0,
            pnl_net=pnl or 0.0,
            is_winner=is_winner,
            exit_reason=exit_reason or "UNKNOWN",
            bars_held=bars_held or 0,
            features=features,
        )

    def _parse_trace(self, trace_json) -> dict:
        """Parse trace_json, handling both string and dict inputs."""
        if trace_json is None:
            return {}

        if isinstance(trace_json, dict):
            return trace_json

        if isinstance(trace_json, str):
            try:
                return json.loads(trace_json)
            except (json.JSONDecodeError, TypeError):
                return {}

        return {}

    def _flatten_trace(self, trace: dict, setup_id: str) -> dict[str, float]:
        """
        Flatten trace_json with namespaced keys. Null-safe.

        Namespace mapping:
        - trace.indicator_values.ATR_14 -> ind.ATR_14
        - trace.state_snapshot.squeeze_age -> state.squeeze_age
        - trace.score_components.participation -> score.participation

        Only numeric values are included. Non-numeric values are skipped.

        Args:
            trace: Parsed trace_json dict
            setup_id: Setup ID for potential future setup-specific handling

        Returns:
            Dict of namespaced feature names to float values
        """
        features = {}

        if not trace:
            return features

        # Extract indicator values
        indicator_values = trace.get("indicator_values", {}) or {}
        for key, value in indicator_values.items():
            float_val = self._to_float(value)
            if float_val is not None:
                features[f"ind.{key}"] = float_val

        # Extract state snapshot
        state_snapshot = trace.get("state_snapshot", {}) or {}
        for key, value in state_snapshot.items():
            float_val = self._to_float(value)
            if float_val is not None:
                features[f"state.{key}"] = float_val

        # Extract score components
        score_components = trace.get("score_components", {}) or {}
        for key, value in score_components.items():
            float_val = self._to_float(value)
            if float_val is not None:
                features[f"score.{key}"] = float_val

        # Also include bars_held as a pseudo-feature (useful for analysis)
        exit_conditions = trace.get("exit_conditions", {}) or {}
        if "bars_held" in exit_conditions:
            bars = self._to_float(exit_conditions["bars_held"])
            if bars is not None:
                features["state.bars_held_at_exit"] = bars

        return features

    def _to_float(self, value) -> Optional[float]:
        """
        Convert value to float if possible, return None otherwise.

        Handles:
        - int, float: direct conversion
        - bool: 1.0 for True, 0.0 for False
        - str: attempt parse
        - None, other: return None
        """
        if value is None:
            return None

        if isinstance(value, bool):
            return 1.0 if value else 0.0

        if isinstance(value, (int, float)):
            # Check for NaN/Inf
            if value != value or value == float('inf') or value == float('-inf'):
                return None
            return float(value)

        if isinstance(value, str):
            try:
                f = float(value)
                if f != f or f == float('inf') or f == float('-inf'):
                    return None
                return f
            except (ValueError, TypeError):
                return None

        return None
