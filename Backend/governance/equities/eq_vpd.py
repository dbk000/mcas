"""
EQ_VPD — Volume-Price Divergence
==================================
Long: price near 5-day low + OBV slope positive (accumulation divergence).
Short: price near 5-day high + OBV slope negative (distribution divergence).
Horizon: 1 trading day.
Validated AggPF: 1.241 (5/5 OOS folds profitable).
"""

import numpy as np
from governance.equities.base import EquitiesStrategy, EquitiesSignal


class EqVpd(EquitiesStrategy):
    strategy_id = "EQ_VPD"
    horizon_days = 1
    version = "EQ_VPD_v1.0"

    NEAR_HIGH_FACTOR = 0.99  # Within 1% of 5d high
    NEAR_LOW_FACTOR = 1.01   # Within 1% of 5d low
    EXPECTED_RETURN_BPS = 45  # iter4 holdout mean

    def evaluate(self, features, bar_idx, symbols, close_prices):
        signals = []
        obv_slope = features.get("obv_slope_5")
        high_5 = features.get("high_5d")
        low_5 = features.get("low_5d")

        if obv_slope is None or high_5 is None or low_5 is None:
            return signals

        T, N = close_prices.shape
        if bar_idx < 0 or bar_idx >= T:
            return signals

        for n in range(N):
            close = close_prices[bar_idx, n]
            h5 = high_5[bar_idx, n]
            l5 = low_5[bar_idx, n]
            slope = obv_slope[bar_idx, n]

            if not all(np.isfinite(v) for v in [close, h5, l5, slope]):
                continue
            if close <= 0:
                continue

            direction = None
            if close >= h5 * self.NEAR_HIGH_FACTOR and slope < 0:
                direction = "SHORT"
            elif close <= l5 * self.NEAR_LOW_FACTOR and slope > 0:
                direction = "LONG"

            if direction is None:
                continue

            # Confidence from slope magnitude
            confidence = min(1.0, abs(slope) / (abs(slope) + 1e-6) * 0.7 + 0.3)

            price_pos = (close - l5) / max(h5 - l5, 1e-8)
            explain = [
                f"Price at {'5d high' if direction == 'SHORT' else '5d low'}: {close:.2f}",
                f"5d range: [{l5:.2f}, {h5:.2f}], position: {price_pos:.1%}",
                f"OBV slope (5d): {slope:.2f} ({'negative' if slope < 0 else 'positive'} = {'distribution' if slope < 0 else 'accumulation'})",
                f"Divergence: price {'high' if direction == 'SHORT' else 'low'} but volume {'exiting' if direction == 'SHORT' else 'entering'}",
            ]

            signals.append(EquitiesSignal(
                strategy_id=self.strategy_id,
                symbol=symbols[n],
                direction=direction,
                horizon_days=self.horizon_days,
                expected_return_bps=self.EXPECTED_RETURN_BPS,
                confidence=confidence,
                entry_price=close,
                reason=f"VPD {direction}: close={'near high' if direction == 'SHORT' else 'near low'}, OBV slope={slope:.2f}",
                explain=explain,
                features={"close": round(close, 2), "high_5d": round(h5, 2),
                          "low_5d": round(l5, 2), "obv_slope_5": round(slope, 4)},
            ))

        return signals
