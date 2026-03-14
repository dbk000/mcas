"""
EQ_REV1B — Mean Reversion + Per-Symbol News Filter
====================================================
Long bottom quintile, Short top quintile of 20d return cross-sectional rank.
Suppress entries when symbol's events_20d > median of all symbols.
Horizon: 10 trading days.
Validated AggPF: 1.377 (5/5 OOS folds profitable).
"""

import numpy as np
from governance.equities.base import EquitiesStrategy, EquitiesSignal


class EqRev1b(EquitiesStrategy):
    strategy_id = "EQ_REV1B"
    horizon_days = 10
    version = "EQ_REV1B_v1.0"

    QUINTILE_LOW = 0.2
    QUINTILE_HIGH = 0.8
    EXPECTED_RETURN_BPS = 138  # iter4 holdout mean

    def evaluate(self, features, bar_idx, symbols, close_prices):
        signals = []
        cs_rank_20 = features["cs_rank_ret_20d"]
        events_20d = features.get("events_20d_avg")
        T, N = cs_rank_20.shape

        if bar_idx < 0 or bar_idx >= T:
            return signals

        # Check breadth
        row = cs_rank_20[bar_idx]
        valid_count = np.isfinite(row).sum()
        if valid_count < max(5, int(N * 0.3)):
            return signals

        # Per-symbol news filter: compute median events_20d
        news_filter_active = False
        no_news_flag = ""
        events_median = np.nan
        if events_20d is not None:
            ev_row = events_20d[bar_idx]
            ev_valid = ev_row[np.isfinite(ev_row)]
            if len(ev_valid) >= int(N * 0.05):
                events_median = np.median(ev_valid)
                news_filter_active = True

        if not news_filter_active:
            no_news_flag = " [NO_NEWS_FILTER]"

        for n in range(N):
            rank = row[n]
            close = close_prices[bar_idx, n]
            ret_20 = features["ret_20d"][bar_idx, n] if "ret_20d" in features else 0

            if not np.isfinite(rank) or not np.isfinite(close):
                continue
            if close <= 0:
                continue

            # Per-symbol news filter
            if news_filter_active and events_20d is not None:
                sym_events = events_20d[bar_idx, n]
                if np.isfinite(sym_events) and sym_events > events_median:
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

            explain = [
                f"20d return cs rank: {rank:.2f} ({'bottom' if direction == 'LONG' else 'top'} quintile)",
                f"20d trailing return: {(ret_20 or 0)*100:.1f}%",
                f"Per-symbol news filter: events_20d median={events_median:.1f}" if news_filter_active else "News filter: unavailable",
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
                reason=f"REV1B {direction}: cs_rank_20d={rank:.2f}{no_news_flag}",
                explain=explain,
                features={"cs_rank_ret_20d": round(rank, 3),
                          "ret_20d": round(float(ret_20 or 0), 4)},
            ))

        return signals
