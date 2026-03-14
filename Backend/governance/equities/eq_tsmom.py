"""
EQ_TSMOM — Time Series Momentum
================================
Long when trailing 60d return > 0. Confidence scaled by return/volatility ratio.
Horizon: 20 trading days.
Validated AggPF: 2.128 (5/5 OOS folds profitable).
"""

import numpy as np
from governance.equities.base import EquitiesStrategy, EquitiesSignal


class EqTsmom(EquitiesStrategy):
    strategy_id = "EQ_TSMOM"
    horizon_days = 20
    version = "EQ_TSMOM_v1.0"

    # From iter4 discovery
    TARGET_VOL = 0.01  # 1% daily vol target
    MAX_POSITION_SCALE = 3.0
    EXPECTED_RETURN_BPS = 326  # iter4 holdout mean

    def evaluate(self, features, bar_idx, symbols, close_prices):
        signals = []
        ret_60 = features["ret_60d"]
        vol_20 = features["realized_vol_20"]
        T, N = ret_60.shape

        if bar_idx < 0 or bar_idx >= T:
            return signals

        for n in range(N):
            r60 = ret_60[bar_idx, n]
            v20 = vol_20[bar_idx, n]
            close = close_prices[bar_idx, n]

            if not np.isfinite(r60) or not np.isfinite(v20) or not np.isfinite(close):
                continue
            if close <= 0 or v20 <= 0.001:
                continue

            # Signal: long when 60d return positive
            if r60 <= 0:
                continue

            # Vol-scaled confidence
            vol_safe = max(v20, 0.001)
            position_scale = min(self.TARGET_VOL / vol_safe, self.MAX_POSITION_SCALE)
            confidence = min(1.0, position_scale / self.MAX_POSITION_SCALE)

            explain = [
                f"60d trailing return: {r60*100:.1f}% (positive -> LONG)",
                f"Realized vol (20d): {v20*100:.2f}%",
                f"Vol-scaled position: {position_scale:.2f}x",
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
                reason=f"TSMOM LONG: 60d ret={r60*100:.1f}%, vol-scale={position_scale:.1f}x",
                explain=explain,
                features={"ret_60d": round(r60, 4), "realized_vol_20": round(v20, 4),
                          "position_scale": round(position_scale, 2)},
            ))

        return signals
