"""
EQ_ATC - After-Trading Chatter (v2)
=====================================
Volume anomaly + momentum direction + tone confirmation strategy.

Core hypothesis: unusual overnight attention (postclose + preopen chatter)
predicts next-day volatility. Direction comes from recent price momentum.

v2 adds keyword tone scoring of OPEN_NEWS titles as a confirmatory signal:
  - If OHLCV says LONG and overnight news independently says bullish → confidence boost
  - If tone disagrees → warning explain item (no suppression)
  - If no tone data → zero change from v1 behavior

Signal logic:
  1. Activity gate:   atc_event_count >= 2
  2. Velocity gate:   atc_velocity > 1.5 (50%+ above 20d average)
  3. Multi-source:    atc_unique_pubs >= 2 (at least 2 distinct publishers)
  4. Direction:       LONG if ret_1d > 0, SHORT if ret_1d < 0
  5. Confidence:      composite of velocity_z, source diversity, event count
  6. Tone (v2):       modest boost when OPEN_NEWS tone confirms direction

Status: exploratory (pending walk-forward validation).
"""

import numpy as np
from governance.equities.base import EquitiesStrategy, EquitiesSignal


class EqAtc(EquitiesStrategy):
    strategy_id = "EQ_ATC"
    horizon_days = 1
    version = "EQ_ATC_v2.0"

    # Gates
    MIN_EVENT_COUNT = 2
    MIN_VELOCITY = 1.5
    MIN_UNIQUE_PUBS = 2

    # Placeholder pending backtest validation
    EXPECTED_RETURN_BPS = 30

    def evaluate(self, features, bar_idx, symbols, close_prices):
        signals = []

        # Required ATC features — skip entirely if not loaded
        atc_count = features.get("atc_event_count")
        atc_velocity = features.get("atc_velocity")
        atc_velocity_z = features.get("atc_velocity_z")
        atc_pubs = features.get("atc_unique_pubs")
        atc_diversity = features.get("atc_source_diversity")
        ret_1d = features.get("ret_1d")

        if any(f is None for f in [atc_count, atc_velocity, atc_pubs, ret_1d]):
            return signals

        # Optional tone features (v2)
        _tone = features.get("atc_tone")
        _tone_strength = features.get("atc_tone_strength")
        _tone_breadth = features.get("atc_tone_breadth")
        _tone_agrees = features.get("atc_tone_agrees_momentum")
        _tone_elig = features.get("atc_tone_eligible_count")
        _tone_nz = features.get("atc_tone_nonzero_count")
        has_tone_features = _tone is not None and _tone_elig is not None

        T, N = close_prices.shape
        if bar_idx < 0 or bar_idx >= T:
            return signals

        for n in range(N):
            close = close_prices[bar_idx, n]
            count = atc_count[bar_idx, n]
            vel = atc_velocity[bar_idx, n]
            pubs = atc_pubs[bar_idx, n]
            ret = ret_1d[bar_idx, n]

            # Validate values
            if not all(np.isfinite(v) for v in [close, count, vel, pubs, ret]):
                continue
            if close <= 0:
                continue

            # Gate 1: minimum activity
            if count < self.MIN_EVENT_COUNT:
                continue

            # Gate 2: velocity surge
            if vel < self.MIN_VELOCITY:
                continue

            # Gate 3: multi-source confirmation
            if pubs < self.MIN_UNIQUE_PUBS:
                continue

            # Gate 4: direction from momentum (skip if flat)
            if ret == 0:
                continue
            direction = "LONG" if ret > 0 else "SHORT"

            # Confidence: composite of velocity_z, diversity, event count
            vel_z = atc_velocity_z[bar_idx, n] if atc_velocity_z is not None else 0.0
            div = atc_diversity[bar_idx, n] if atc_diversity is not None else 0.0
            if not np.isfinite(vel_z):
                vel_z = 0.0
            if not np.isfinite(div):
                div = 0.0

            confidence = (
                0.2
                + 0.3 * min(max(vel_z, 0.0), 2.0) / 2.0
                + 0.3 * div
                + 0.2 * min(count, 10.0) / 10.0
            )

            explain = [
                f"Overnight events: {int(count)} (velocity {vel:.1f}x vs 20d avg)",
                f"Distinct publishers: {int(pubs)} (diversity {div:.2f})",
                f"Prior-day return: {ret:+.2%} -> {direction} momentum",
                f"Velocity z-score: {vel_z:.2f}",
            ]

            # --- Tone confirmation (v2) ---
            tone_val = 0.0
            tone_n_news = 0
            tone_n_dir = 0
            tone_agrees = False
            feat_tone = {}

            if has_tone_features:
                tv = _tone[bar_idx, n]
                te = _tone_elig[bar_idx, n]
                if np.isfinite(tv) and np.isfinite(te) and te > 0:
                    tone_val = tv
                    ts = _tone_strength[bar_idx, n] if _tone_strength is not None else 0.0
                    tb = _tone_breadth[bar_idx, n] if _tone_breadth is not None else 0.0
                    ta = _tone_agrees[bar_idx, n] if _tone_agrees is not None else np.nan
                    tone_n_news = int(te)
                    tone_n_dir = int(_tone_nz[bar_idx, n]) if _tone_nz is not None else 0

                    if not np.isfinite(ts):
                        ts = 0.0

                    # Modest confidence boost when tone agrees with direction
                    if np.isfinite(ta) and ta == 1.0:
                        boost = min(0.1, 0.1 * ts)
                        confidence += boost
                        tone_agrees = True
                        explain.append(f"Tone confirms {direction} momentum")
                    elif np.isfinite(ta) and ta == 0.0 and abs(tone_val) > 0:
                        # Tone disagrees — warning only, NO suppression
                        explain.append(
                            f"Note: overnight news tone ({tone_val:+.2f}) diverges from price momentum"
                        )

                    explain.append(
                        f"Overnight news tone: {tone_val:+.2f} ({tone_n_news} articles, {tone_n_dir} directional)"
                    )

                    feat_tone = {
                        "atc_tone": round(tone_val, 4),
                        "atc_tone_breadth": round(tb, 4),
                        "atc_tone_agrees": tone_agrees,
                    }

            confidence = min(1.0, max(0.1, confidence))

            feat_dict = {
                "atc_events": int(count),
                "atc_velocity": round(vel, 2),
                "atc_unique_pubs": int(pubs),
                "atc_diversity": round(div, 3),
                "ret_1d": round(ret, 5),
            }
            feat_dict.update(feat_tone)

            signals.append(EquitiesSignal(
                strategy_id=self.strategy_id,
                symbol=symbols[n],
                direction=direction,
                horizon_days=self.horizon_days,
                expected_return_bps=self.EXPECTED_RETURN_BPS,
                confidence=round(confidence, 4),
                entry_price=close,
                reason=f"ATC {direction}: {int(count)} overnight events ({vel:.1f}x velocity), ret_1d={ret:+.2%}",
                explain=explain,
                features=feat_dict,
            ))

        return signals
