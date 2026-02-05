"""
Statistical Analysis Module
===========================

Computes correlation statistics between trade features and outcomes.

Primary metrics:
- Cohen's d: Effect size (pooled standard deviation)
- Binned win-rate: Win rate by feature value ranges

No external dependencies beyond numpy.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from .feature_extractor import TradeFeatures

logger = logging.getLogger(__name__)


@dataclass
class FeatureAnalysis:
    """Analysis results for a single feature."""
    feature_name: str
    n_samples: int
    winner_mean: float
    winner_median: float
    loser_mean: float
    loser_median: float
    cohens_d: float  # Effect size (primary ranking metric)
    bins: list[dict] = field(default_factory=list)
    insight: str = ""

    def to_dict(self) -> dict:
        return {
            "feature_name": self.feature_name,
            "n_samples": self.n_samples,
            "winner_mean": round(self.winner_mean, 4),
            "winner_median": round(self.winner_median, 4),
            "loser_mean": round(self.loser_mean, 4),
            "loser_median": round(self.loser_median, 4),
            "cohens_d": round(self.cohens_d, 4),
            "bins": self.bins,
            "insight": self.insight,
        }


@dataclass
class CorrelationReport:
    """Complete correlation analysis report."""
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    truncated: bool  # True if max_trades cap was hit
    feature_analyses: dict[str, FeatureAnalysis] = field(default_factory=dict)
    top_loser_predictors: list[tuple[str, float, str]] = field(default_factory=list)
    exit_reason_breakdown: dict[str, dict] = field(default_factory=dict)
    insights: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "truncated": self.truncated,
            "feature_analyses": {
                k: v.to_dict() for k, v in self.feature_analyses.items()
            },
            "top_predictors": [
                {"feature": f, "importance": round(imp, 4), "direction": d}
                for f, imp, d in self.top_loser_predictors
            ],
            "exit_reason_breakdown": self.exit_reason_breakdown,
            "insights": self.insights,
        }


class CorrelationAnalyzer:
    """
    Analyzes correlations between trade features and outcomes.

    Primary metric is Cohen's d (effect size), which measures how different
    the feature distributions are between winners and losers.

    Usage:
        analyzer = CorrelationAnalyzer()
        report = analyzer.analyze(trades, min_samples=30)
    """

    MAX_FEATURES = 50   # Cap features to prevent runaway memory
    MAX_TRADES = 2000   # Cap trades to prevent long-running jobs

    def __init__(
        self,
        max_features: int = 50,
        max_trades: int = 2000,
    ):
        self.max_features = max_features
        self.max_trades = max_trades

    def analyze(
        self,
        trades: list[TradeFeatures],
        min_samples: int = 30,
    ) -> CorrelationReport:
        """
        Analyze correlations between features and trade outcomes.

        Args:
            trades: List of trades with extracted features
            min_samples: Minimum samples required for feature analysis

        Returns:
            CorrelationReport with analysis results
        """
        if not trades:
            return CorrelationReport(
                total_trades=0,
                winning_trades=0,
                losing_trades=0,
                win_rate=0.0,
                truncated=False,
            )

        # Truncate if needed
        truncated = len(trades) > self.max_trades
        if truncated:
            logger.warning(
                f"Truncating trades from {len(trades)} to {self.max_trades}"
            )
            trades = trades[:self.max_trades]

        # Separate winners and losers
        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]

        total = len(trades)
        win_count = len(winners)
        lose_count = len(losers)
        win_rate = win_count / total if total > 0 else 0.0

        logger.info(
            f"Analyzing {total} trades: {win_count} winners, {lose_count} losers "
            f"({win_rate:.1%} win rate)"
        )

        # Select features to analyze (deterministic selection)
        selected_features = self._select_features(trades)
        logger.info(f"Selected {len(selected_features)} features for analysis")

        # Analyze each feature
        feature_analyses = {}
        for feature_name in selected_features:
            analysis = self._analyze_feature(
                feature_name,
                trades,
                winners,
                losers,
                win_rate,
                min_samples,
            )
            if analysis:
                feature_analyses[feature_name] = analysis

        # Rank features by predictive power (abs Cohen's d)
        top_predictors = self._rank_features(feature_analyses)

        # Compute exit reason breakdown
        exit_breakdown = self._compute_exit_breakdown(trades)

        # Generate insights
        insights = self._generate_insights(
            feature_analyses,
            exit_breakdown,
            win_rate,
            truncated,
        )

        return CorrelationReport(
            total_trades=total,
            winning_trades=win_count,
            losing_trades=lose_count,
            win_rate=win_rate,
            truncated=truncated,
            feature_analyses=feature_analyses,
            top_loser_predictors=top_predictors,
            exit_reason_breakdown=exit_breakdown,
            insights=insights,
        )

    def _select_features(self, trades: list[TradeFeatures]) -> list[str]:
        """
        Deterministic feature selection.

        Selection criteria (in order):
        1. Count n_samples per feature (non-null values)
        2. Sort by n_samples desc, then by variance desc
        3. Return top MAX_FEATURES

        This ensures stable, reproducible feature selection.
        """
        feature_stats = {}

        for trade in trades:
            for name, value in trade.features.items():
                if name not in feature_stats:
                    feature_stats[name] = {"values": [], "count": 0}
                feature_stats[name]["values"].append(value)
                feature_stats[name]["count"] += 1

        # Calculate variance for each feature
        for name, stats in feature_stats.items():
            values = np.array(stats["values"])
            stats["variance"] = float(np.var(values)) if len(values) > 1 else 0.0

        # Sort by count desc, then variance desc, then name (for stability)
        sorted_features = sorted(
            feature_stats.items(),
            key=lambda x: (-x[1]["count"], -x[1]["variance"], x[0]),
        )

        # Return top N feature names
        return [name for name, _ in sorted_features[:self.max_features]]

    def _analyze_feature(
        self,
        feature_name: str,
        all_trades: list[TradeFeatures],
        winners: list[TradeFeatures],
        losers: list[TradeFeatures],
        overall_win_rate: float,
        min_samples: int,
    ) -> Optional[FeatureAnalysis]:
        """Analyze a single feature."""
        # Extract values for winners and losers
        winner_values = [
            t.features[feature_name]
            for t in winners
            if feature_name in t.features
        ]
        loser_values = [
            t.features[feature_name]
            for t in losers
            if feature_name in t.features
        ]

        # Check minimum samples
        if len(winner_values) < min_samples // 2 or len(loser_values) < min_samples // 2:
            return None

        winner_arr = np.array(winner_values)
        loser_arr = np.array(loser_values)

        # Compute statistics
        winner_mean = float(np.mean(winner_arr))
        winner_median = float(np.median(winner_arr))
        loser_mean = float(np.mean(loser_arr))
        loser_median = float(np.median(loser_arr))

        # Compute Cohen's d
        cohens_d = self._compute_cohens_d(winner_arr, loser_arr)

        # Compute binned analysis
        all_values = [
            (t.features[feature_name], t.is_winner, t.r_multiple)
            for t in all_trades
            if feature_name in t.features
        ]
        bins = self._compute_binned_analysis(all_values)

        # Generate insight for this feature
        insight = self._feature_insight(
            feature_name,
            winner_mean,
            loser_mean,
            cohens_d,
            bins,
            overall_win_rate,
        )

        return FeatureAnalysis(
            feature_name=feature_name,
            n_samples=len(winner_values) + len(loser_values),
            winner_mean=winner_mean,
            winner_median=winner_median,
            loser_mean=loser_mean,
            loser_median=loser_median,
            cohens_d=cohens_d,
            bins=bins,
            insight=insight,
        )

    def _compute_cohens_d(
        self,
        group1: np.ndarray,
        group2: np.ndarray,
    ) -> float:
        """
        Compute Cohen's d effect size using pooled standard deviation.

        Cohen's d = (mean1 - mean2) / pooled_std

        Interpretation:
        - |d| < 0.2: negligible
        - 0.2 <= |d| < 0.5: small
        - 0.5 <= |d| < 0.8: medium
        - |d| >= 0.8: large

        Positive d means group1 (winners) has higher values.
        Negative d means group2 (losers) has higher values.
        """
        n1, n2 = len(group1), len(group2)

        if n1 < 2 or n2 < 2:
            return 0.0

        mean1, mean2 = np.mean(group1), np.mean(group2)
        var1, var2 = np.var(group1, ddof=1), np.var(group2, ddof=1)

        # Pooled standard deviation
        pooled_var = ((n1 - 1) * var1 + (n2 - 1) * var2) / (n1 + n2 - 2)
        pooled_std = np.sqrt(pooled_var)

        if pooled_std == 0:
            return 0.0

        return float((mean1 - mean2) / pooled_std)

    def _compute_binned_analysis(
        self,
        data: list[tuple[float, bool, float]],
        n_bins: int = 5,
    ) -> list[dict]:
        """
        Compute win rate and avg R by feature value bins.

        Args:
            data: List of (feature_value, is_winner, r_multiple) tuples
            n_bins: Number of bins to create

        Returns:
            List of bin dicts with range, count, win_rate, avg_r
        """
        if len(data) < n_bins:
            return []

        values = np.array([d[0] for d in data])
        is_winners = np.array([d[1] for d in data])
        r_multiples = np.array([d[2] for d in data])

        # Use percentile-based bins for even distribution
        try:
            percentiles = np.percentile(values, np.linspace(0, 100, n_bins + 1))
        except Exception:
            return []

        # Ensure unique bin edges
        percentiles = np.unique(percentiles)
        if len(percentiles) < 2:
            return []

        bins = []
        for i in range(len(percentiles) - 1):
            low, high = percentiles[i], percentiles[i + 1]

            # Include right edge for last bin
            if i == len(percentiles) - 2:
                mask = (values >= low) & (values <= high)
            else:
                mask = (values >= low) & (values < high)

            count = int(np.sum(mask))
            if count == 0:
                continue

            win_rate = float(np.mean(is_winners[mask]))
            avg_r = float(np.mean(r_multiples[mask]))

            bins.append({
                "range": f"{low:.2f}-{high:.2f}",
                "low": round(low, 4),
                "high": round(high, 4),
                "count": count,
                "win_rate": round(win_rate, 4),
                "avg_r": round(avg_r, 4),
            })

        return bins

    def _rank_features(
        self,
        analyses: dict[str, FeatureAnalysis],
    ) -> list[tuple[str, float, str]]:
        """
        Rank features by predictive power (abs Cohen's d).

        Returns list of (feature_name, importance, direction) tuples.
        Direction is "positive" if winners have higher values, "negative" otherwise.
        """
        ranked = []

        for name, analysis in analyses.items():
            importance = abs(analysis.cohens_d)
            direction = "positive" if analysis.cohens_d > 0 else "negative"
            ranked.append((name, importance, direction))

        # Sort by importance descending
        ranked.sort(key=lambda x: -x[1])

        return ranked[:20]  # Top 20

    def _compute_exit_breakdown(
        self,
        trades: list[TradeFeatures],
    ) -> dict[str, dict]:
        """
        Compute statistics grouped by exit reason.

        Returns dict of exit_reason -> {count, avg_r, win_rate, avg_features}.
        """
        by_exit = {}

        for trade in trades:
            reason = trade.exit_reason
            if reason not in by_exit:
                by_exit[reason] = {
                    "trades": [],
                    "r_multiples": [],
                    "features": {},
                }

            by_exit[reason]["trades"].append(trade)
            by_exit[reason]["r_multiples"].append(trade.r_multiple)

            # Accumulate feature values
            for fname, fval in trade.features.items():
                if fname not in by_exit[reason]["features"]:
                    by_exit[reason]["features"][fname] = []
                by_exit[reason]["features"][fname].append(fval)

        # Compute summary stats per exit reason
        result = {}
        for reason, data in by_exit.items():
            trades_list = data["trades"]
            r_arr = np.array(data["r_multiples"])

            win_count = sum(1 for t in trades_list if t.is_winner)

            # Average feature values
            avg_features = {}
            for fname, vals in data["features"].items():
                if len(vals) > 0:
                    avg_features[fname] = round(float(np.mean(vals)), 4)

            result[reason] = {
                "count": len(trades_list),
                "avg_r": round(float(np.mean(r_arr)), 4) if len(r_arr) > 0 else 0.0,
                "win_rate": round(win_count / len(trades_list), 4) if trades_list else 0.0,
                "avg_features": avg_features,
            }

        return result

    def _feature_insight(
        self,
        feature_name: str,
        winner_mean: float,
        loser_mean: float,
        cohens_d: float,
        bins: list[dict],
        overall_win_rate: float,
    ) -> str:
        """Generate a human-readable insight for a feature."""
        abs_d = abs(cohens_d)

        # Effect size interpretation
        if abs_d < 0.2:
            effect = "negligible"
        elif abs_d < 0.5:
            effect = "small"
        elif abs_d < 0.8:
            effect = "medium"
        else:
            effect = "large"

        # Direction
        if cohens_d > 0:
            direction = "higher in winners"
            ratio = winner_mean / loser_mean if loser_mean != 0 else 0
        else:
            direction = "lower in winners"
            ratio = loser_mean / winner_mean if winner_mean != 0 else 0

        # Find bins with extreme win rates
        extreme_bins = []
        for b in bins:
            deviation = abs(b["win_rate"] - overall_win_rate)
            if deviation > 0.15:  # 15% deviation from overall
                extreme_bins.append(b)

        if extreme_bins and abs_d >= 0.3:
            worst_bin = min(extreme_bins, key=lambda x: x["win_rate"])
            return (
                f"{feature_name} in range {worst_bin['range']} has "
                f"{worst_bin['win_rate']:.0%} win rate vs {overall_win_rate:.0%} overall "
                f"(d={cohens_d:.2f})"
            )
        elif abs_d >= 0.5:
            return (
                f"{feature_name} is {effect}ly {direction} "
                f"(d={cohens_d:.2f})"
            )
        else:
            return ""

    def _generate_insights(
        self,
        analyses: dict[str, FeatureAnalysis],
        exit_breakdown: dict[str, dict],
        overall_win_rate: float,
        truncated: bool,
    ) -> list[str]:
        """Generate actionable insights from the analysis."""
        insights = []

        if truncated:
            insights.append(
                f"Analysis truncated to {self.max_trades} trades. "
                "Results may not represent full population."
            )

        # Top predictors insight
        sorted_by_d = sorted(
            analyses.items(),
            key=lambda x: abs(x[1].cohens_d),
            reverse=True,
        )

        # Strong predictors (|d| >= 0.5)
        strong = [(n, a) for n, a in sorted_by_d if abs(a.cohens_d) >= 0.5]
        if strong:
            names = [n for n, _ in strong[:3]]
            insights.append(
                f"Strong predictors of outcome: {', '.join(names)}"
            )

        # Feature-specific insights
        for name, analysis in sorted_by_d[:5]:
            if analysis.insight:
                insights.append(analysis.insight)

        # Exit reason insights
        if "STOP_LOSS" in exit_breakdown and "TP1" in exit_breakdown:
            sl_data = exit_breakdown["STOP_LOSS"]
            tp1_data = exit_breakdown["TP1"]

            # Compare features between stop loss and TP1 exits
            common_features = set(sl_data["avg_features"].keys()) & set(tp1_data["avg_features"].keys())

            for fname in list(common_features)[:3]:
                sl_val = sl_data["avg_features"][fname]
                tp1_val = tp1_data["avg_features"][fname]
                if sl_val != 0 and tp1_val != 0:
                    ratio = tp1_val / sl_val
                    if ratio > 1.5 or ratio < 0.67:
                        insights.append(
                            f"STOP_LOSS exits: avg {fname} = {sl_val:.2f} "
                            f"vs TP1 exits: {tp1_val:.2f}"
                        )

        return insights[:10]  # Cap at 10 insights
