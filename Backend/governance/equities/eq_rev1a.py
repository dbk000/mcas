"""
EQ_REV1A — Mean Reversion + Aggregate News Filter
===================================================
Long bottom quintile, Short top quintile of 20d return cross-sectional rank.
Suppress entries when aggregate news velocity z-score > 75th percentile.
Horizon: 10 trading days.
Validated AggPF: 1.709 (5/5 OOS folds profitable).
"""

import numpy as np
from governance.equities.base import EquitiesStrategy, EquitiesSignal


class EqRev1a(EquitiesStrategy):
    strategy_id = "EQ_REV1A"
    horizon_days = 10
    version = "EQ_REV1A_v1.0"

    QUINTILE_LOW = 0.2   # Bottom 20%
    QUINTILE_HIGH = 0.8  # Top 20%
    NEWS_Z_PERCENTILE = 75  # Suppress above this percentile
    EXPECTED_RETURN_BPS = 170  # iter4 holdout mean

    def evaluate(self, features, bar_idx, symbols, close_prices):
        signals = []
        cs_rank_20 = features["cs_rank_ret_20d"]
        agg_news_z = features.get("agg_news_vel_zscore")
        T, N = cs_rank_20.shape

        if bar_idx < 0 or bar_idx >= T:
            return signals

        # Check breadth
        row = cs_rank_20[bar_idx]
        valid_count = np.isfinite(row).sum()
        if valid_count < max(5, int(N * 0.3)):
            return signals

        # Aggregate news filter
        news_filter_active = False
        news_suppressed = False
        no_news_flag = ""
        if agg_news_z is not None:
            agg_z_val = agg_news_z[bar_idx, 0]
            if np.isfinite(agg_z_val):
                news_filter_active = True
                # Use rolling 75th percentile approximation: z > 0.675
                if agg_z_val > 0.675:
                    news_suppressed = True

        if not news_filter_active:
            no_news_flag = " [NO_NEWS_FILTER]"

        if news_suppressed:
            return signals

        for n in range(N):
            rank = row[n]
            close = close_prices[bar_idx, n]
            ret_20 = features["ret_20d"][bar_idx, n] if "ret_20d" in features else 0

            if not np.isfinite(rank) or not np.isfinite(close):
                continue
            if close <= 0:
                continue

            direction = None
            if rank <= self.QUINTILE_LOW:
                direction = "LONG"
                confidence = min(1.0, (self.QUINTILE_LOW - rank) / self.QUINTILE_LOW * 0.5 + 0.5)
            elif rank >= self.QUINTILE_HIGH:
                direction = "SHORT"
                confidence = min(1.0, (rank - self.QUINTILE_HIGH) / (1.0 - self.QUINTILE_HIGH) * 0.5 + 0.5)

            if direction is None:
                continue

            agg_z_str = f"{agg_news_z[bar_idx, 0]:.2f}" if news_filter_active else "N/A"
            explain = [
                f"20d return cs rank: {rank:.2f} ({'bottom' if direction == 'LONG' else 'top'} quintile)",
                f"20d trailing return: {(ret_20 or 0)*100:.1f}%",
                f"Agg news velocity z-score: {agg_z_str} (filter: <= 75th pct)",
                f"Horizon: {self.horizon_days} trading days{no_news_flag}",
            ]

            signals.append(EquitiesSignal(
                strategy_id=self.strategy_id,
                symbol=symbols[n],
                direction=direction,
                horizon_days=self.horizon_days,
                expected_return_bps=self.EXPECTED_RETURN_BPS,
                confidence=confidence,
                entry_price=close,
                reason=f"REV1A {direction}: cs_rank_20d={rank:.2f}, agg_news_z={agg_z_str}{no_news_flag}",
                explain=explain,
                features={"cs_rank_ret_20d": round(rank, 3),
                          "ret_20d": round(float(ret_20 or 0), 4)},
            ))

        return signals
