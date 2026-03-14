"""
EQ_XSMOM — Cross-Sectional Momentum (Long Only)
=================================================
Long top decile by 60-day return.
Horizon: 20 trading days.
Validated AggPF: 1.912 (5/5 OOS folds profitable).
"""

import numpy as np
from governance.equities.base import EquitiesStrategy, EquitiesSignal


class EqXsmom(EquitiesStrategy):
    strategy_id = "EQ_XSMOM"
    horizon_days = 20
    version = "EQ_XSMOM_v1.0"

    DECILE_THRESHOLD = 0.9  # Top 10%
    MIN_VALID_RATIO = 0.3   # 30% of universe must have valid data
    EXPECTED_RETURN_BPS = 280  # iter4 holdout mean

    def evaluate(self, features, bar_idx, symbols, close_prices):
        signals = []
        cs_rank_60 = features["cs_rank_ret_60d"]
        ret_60 = features["ret_60d"]
        T, N = cs_rank_60.shape

        if bar_idx < 0 or bar_idx >= T:
            return signals

        # Check breadth gate
        row = cs_rank_60[bar_idx]
        valid_count = np.isfinite(row).sum()
        if valid_count < max(5, int(N * self.MIN_VALID_RATIO)):
            return signals

        for n in range(N):
            rank = row[n]
            r60 = ret_60[bar_idx, n]
            close = close_prices[bar_idx, n]

            if not np.isfinite(rank) or not np.isfinite(close):
                continue
            if close <= 0:
                continue

            # Top decile gate
            if rank < self.DECILE_THRESHOLD:
                continue

            confidence = min(1.0, (rank - self.DECILE_THRESHOLD) / (1.0 - self.DECILE_THRESHOLD) * 0.5 + 0.5)

            explain = [
                f"60d return cross-sectional rank: {rank:.2f} (top decile >= {self.DECILE_THRESHOLD})",
                f"60d trailing return: {(r60 or 0)*100:.1f}%",
                f"Universe breadth: {valid_count}/{N} symbols valid",
                f"Horizon: {self.horizon_days} trading days",
            ]

            signals.append(EquitiesSignal(
                strategy_id=self.strategy_id,
                symbol=symbols[n],
                direction="LONG",
                horizon_days=self.horizon_days,
                expected_return_bps=self.EXPECTED_RETURN_BPS,
                confidence=confidence,
                entry_price=close,
                reason=f"XSMOM LONG: cs_rank_60d={rank:.2f}, top decile",
                explain=explain,
                features={"cs_rank_ret_60d": round(rank, 3),
                          "ret_60d": round(float(r60 or 0), 4)},
            ))

        return signals
