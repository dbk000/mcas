"""
EQ_NVDA_MACRO - NVDA Earnings Cascade Strategy (v1.2)
=====================================================
Event-driven strategy that detects laggard equities after NVDA earnings.

Core thesis: when NVDA reports strong earnings, correlated equities move
in sympathy — but some lag behind. Those laggards tend to catch up over
the following 2-5 trading days.

This strategy (close-phase evaluation):
  1. Detects NVDA earnings reaction days (next trading day after AMC report)
  2. Measures NVDA's close-to-close return (ret_1d)
  3. For each cascade-eligible asset (direction_consistency >= 6/N from D1):
     - Computes SPY-controlled excess return (decontaminated for NVDA weight)
     - Identifies laggards (excess return below D1-derived threshold)
     - Skips NVDA itself (can't be a laggard of itself)
  4. Generates LONG signals for laggards on positive NVDA reactions

Key limitation (v1): evaluates at close (~4:30 PM). The strongest cascade
signal lives in the overnight gap (open). Gap-phase assets like QQQ (7/7
gap, 4/7 close) and XLK (7/7 gap, 3/7 close) reverse by close and are
NOT captured by this strategy. Manual gap trades at open are a separate
play documented in the D1 cascade report.

Close-phase candidates (v1.2): META (6/7 close, 7/7 gap), AVGO (6/7
close, 6/7 gap). TMO excluded (negative avg excess). NVDA excluded
(can't lag itself).

v1.2 changes:
  - SPY decontamination (removes NVDA weight from SPY return)
  - Removed divergence filter (was mutually exclusive with laggard detection)
  - EXPECTED_RETURN_BPS=0 (close-to-close catch-up is exploratory)

v1 constraints:
  - LONG-only (positive NVDA reactions only)
  - Equities only (no crypto)
  - Max 10 signals per event
  - exploratory=True (pending walk-forward validation)

Cascade profiles loaded from D1 output:
  /scripts/output/nvda_cascade_profiles.json

Status: exploratory (7-event sample, no backtest possible).
"""

import json
import logging
import os
from datetime import date, datetime, timezone

import numpy as np
from governance.equities.base import EquitiesStrategy, EquitiesSignal

logger = logging.getLogger(__name__)

# Paths to cascade profiles (bind-mounted in Docker, host fallback)
_PROFILE_PATHS = [
    "/scripts/output/nvda_cascade_profiles.json",
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "scripts", "output", "nvda_cascade_profiles.json"),
]

# Max staleness of cascade profiles (days)
MAX_PROFILE_AGE_DAYS = 120

# LONG-only v1: minimum NVDA ret_1d to generate signals
MIN_NVDA_RET = 0.03

# Max signals per event
MAX_SIGNALS = 10

# Minimum direction consistency for cascade eligibility
# D1 analyzer uses 6 (86%). At 7, only NVDA itself passes at close-to-close,
# making the strategy circular. At 6: META (6/7) and AVGO (6/7) qualify.
MIN_DIRECTION_CONSISTENCY = 6

# NVDA's approximate weight in SPY (for decontamination)
NVDA_SPY_WEIGHT = 0.06


class EqNvdaMacro(EquitiesStrategy):
    strategy_id = "EQ_NVDA_MACRO"
    horizon_days = 3
    version = "EQ_NVDA_MACRO_v1.2"

    EXPECTED_RETURN_BPS = 0  # Close-to-close laggard catch-up is exploratory (not validated)

    def __init__(self):
        self._profiles = None
        self._reaction_dates: set[str] = set()
        self._next_reaction_date: str | None = None
        self._recommended_threshold: float = 0.5
        self._load_profiles()

    def _load_profiles(self):
        """Load cascade profiles from D1 output JSON."""
        for path in _PROFILE_PATHS:
            abs_path = os.path.abspath(path)
            if not os.path.exists(abs_path):
                continue
            try:
                with open(abs_path) as f:
                    data = json.load(f)

                # Validate staleness
                gen_ts = data.get("generated_ts")
                if gen_ts:
                    gen_dt = datetime.fromisoformat(gen_ts.replace("Z", "+00:00"))
                    if gen_dt.tzinfo is None:
                        gen_dt = gen_dt.replace(tzinfo=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - gen_dt).days
                    if age_days > MAX_PROFILE_AGE_DAYS:
                        logger.warning(
                            "EQ_NVDA_MACRO: cascade profiles are %d days old (>%d), ignoring",
                            age_days, MAX_PROFILE_AGE_DAYS,
                        )
                        continue

                self._profiles = data
                self._reaction_dates = set(data.get("reaction_dates", []))
                self._next_reaction_date = data.get("next_reaction_date")
                if self._next_reaction_date:
                    self._reaction_dates.add(self._next_reaction_date)
                self._recommended_threshold = data.get("recommended_laggard_threshold", 0.5)
                logger.info("EQ_NVDA_MACRO: loaded cascade profiles from %s (%d events, %d eligible assets)",
                            abs_path, data.get("n_events", 0),
                            data.get("summary", {}).get("cascade_eligible_count", 0))
                return
            except Exception as e:
                logger.warning("EQ_NVDA_MACRO: failed to load profiles from %s: %s", abs_path, e)

        logger.warning("EQ_NVDA_MACRO: no cascade profiles found, strategy will produce 0 signals")

    def evaluate(self, features, bar_idx, symbols, close_prices):
        """Evaluate at current bar. Returns signals for laggard cascade assets."""
        signals = []

        if self._profiles is None:
            return signals

        # Get current bar date from timestamps
        timestamps = features.get("_timestamps")
        if timestamps is None:
            return signals

        current_ts = timestamps[bar_idx]
        current_date = _to_date(current_ts)
        current_date_str = str(current_date)

        # Check if today is a reaction date
        if current_date_str not in self._reaction_dates:
            return signals

        logger.info("EQ_NVDA_MACRO: reaction day detected (%s)", current_date_str)

        T, N = close_prices.shape

        # Build symbol index
        sym_idx = {s: i for i, s in enumerate(symbols)}

        # Get NVDA and SPY indices
        nvda_idx = sym_idx.get("NVDA")
        spy_idx = sym_idx.get("SPY")
        if nvda_idx is None or spy_idx is None:
            logger.warning("EQ_NVDA_MACRO: NVDA or SPY not in symbol list")
            return signals

        # NVDA ret_1d (close-to-close, already computed in features)
        ret_1d = features.get("ret_1d")
        if ret_1d is None:
            return signals

        nvda_ret = ret_1d[bar_idx, nvda_idx]
        if not np.isfinite(nvda_ret):
            logger.warning("EQ_NVDA_MACRO: NVDA ret_1d is NaN")
            return signals

        # LONG-only v1: skip negative NVDA reactions
        if nvda_ret < 0:
            logger.info("EQ_NVDA_MACRO: NVDA ret_1d = %.2f%% (negative), skipping (LONG-only v1)",
                        nvda_ret * 100)
            return signals

        # Minimum reaction threshold
        if nvda_ret < MIN_NVDA_RET:
            logger.info("EQ_NVDA_MACRO: NVDA ret_1d = %.2f%% (below %.0f%% threshold), skipping",
                        nvda_ret * 100, MIN_NVDA_RET * 100)
            return signals

        logger.info("EQ_NVDA_MACRO: NVDA ret_1d = %+.2f%%, evaluating cascade", nvda_ret * 100)

        # SPY return and beta
        spy_ret = ret_1d[bar_idx, spy_idx]
        # Decontaminate SPY: remove NVDA's index weight (same formula as D1 analyzer)
        spy_ret_clean = (spy_ret - NVDA_SPY_WEIGHT * nvda_ret) / (1 - NVDA_SPY_WEIGHT) if np.isfinite(spy_ret) else spy_ret
        spy_beta_feat = features.get("spy_beta_60")

        # Cascade asset profiles from D1
        asset_profiles = self._profiles.get("assets", {})

        candidates = []
        for sym, profile in asset_profiles.items():
            # NVDA can't be a laggard of itself
            if sym == "NVDA":
                continue
            if not profile.get("cascade_eligible"):
                continue
            if profile.get("direction_consistency", 0) < MIN_DIRECTION_CONSISTENCY:
                continue

            n = sym_idx.get(sym)
            if n is None:
                continue

            close = close_prices[bar_idx, n]
            if not np.isfinite(close) or close <= 0:
                continue

            asset_ret = ret_1d[bar_idx, n]
            if not np.isfinite(asset_ret):
                continue

            # SPY beta for this asset
            beta = spy_beta_feat[bar_idx, n] if spy_beta_feat is not None else 0.0
            if not np.isfinite(beta):
                beta = 0.0

            # Expected market return = beta * decontaminated SPY_ret
            exp_market = beta * spy_ret_clean if np.isfinite(spy_ret_clean) else 0.0

            # Excess return (the real cascade signal)
            excess_ret = asset_ret - exp_market

            # Laggard detection: compare actual excess to historical average
            avg_excess_1d = profile.get("avg_excess_day1")
            if avg_excess_1d is None or avg_excess_1d <= 0:
                continue

            lag_depth = excess_ret / avg_excess_1d

            # Asset is a laggard if lag_depth below recommended threshold
            if lag_depth >= self._recommended_threshold:
                continue  # Not a laggard — already moved

            # Compute confidence
            direction_consistency = profile.get("direction_consistency", 0)
            gap_direction_consistency = profile.get("gap_direction_consistency", 0)
            n_events = profile.get("n_events_valid", 8)
            avg_excess_day1 = avg_excess_1d

            confidence = (
                0.15                                                      # floor (tiny sample)
                + 0.35 * (direction_consistency - 6) / 2.0               # 0 at 6, 0.35 at 8
                + 0.25 * min(abs(nvda_ret) / 0.10, 1.0)                  # reaction magnitude
                + 0.15 * min(avg_excess_day1 / 0.02, 1.0)                # historical strength
                + 0.10 * (1.0 - min(max(lag_depth, 0.0), 1.0))           # lag depth
            )
            confidence = min(0.80, max(0.15, confidence))

            # Overnight gap (informational for explain)
            overnight = features.get("overnight_gap")
            gap_str = ""
            if overnight is not None:
                gap = overnight[bar_idx, n]
                if np.isfinite(gap):
                    gap_str = f", overnight gap={gap:+.2%}"

            explain = [
                f"NVDA post-earnings: ret_1d = {nvda_ret:+.2%} (above {MIN_NVDA_RET:.0%} threshold)",
                f"{sym} close consistency: {direction_consistency}/{n_events} | gap consistency: {gap_direction_consistency}/{n_events}",
                f"{sym} excess return today: {excess_ret:+.2%} vs historical avg {avg_excess_day1:+.2%}",
                f"SPY decontaminated: raw {spy_ret:+.2%} -> clean {spy_ret_clean:+.2%} (NVDA weight {NVDA_SPY_WEIGHT:.0%}), {sym} beta {beta:.2f} -> {exp_market:+.2%} expected",
                f"Lag depth: {lag_depth:.2f} (threshold: {self._recommended_threshold:.2f}){gap_str}",
                "Note: close-to-close laggard catch-up is exploratory (20-33% at percentile cuts, n=7 events). Not a validated edge.",
            ]

            candidates.append((confidence, EquitiesSignal(
                strategy_id=self.strategy_id,
                symbol=sym,
                direction="LONG",
                horizon_days=self.horizon_days,
                expected_return_bps=self.EXPECTED_RETURN_BPS,
                confidence=round(confidence, 4),
                entry_price=close,
                reason=f"NVDA_MACRO LONG {sym}: NVDA {nvda_ret:+.1%} post-earnings, {sym} lagging in excess returns",
                explain=explain,
                features={
                    "nvda_ret_1d": round(nvda_ret, 5),
                    "asset_ret_1d": round(asset_ret, 5),
                    "excess_ret_1d": round(excess_ret, 5),
                    "spy_beta": round(beta, 4),
                    "lag_depth": round(lag_depth, 4),
                    "direction_consistency": direction_consistency,
                    "gap_direction_consistency": gap_direction_consistency,
                    "avg_excess_day1": round(avg_excess_day1, 5),
                },
            )))

        # Sort by confidence descending, take top MAX_SIGNALS
        candidates.sort(key=lambda x: x[0], reverse=True)
        signals = [sig for _, sig in candidates[:MAX_SIGNALS]]

        logger.info("EQ_NVDA_MACRO: %d signals from %d candidates", len(signals), len(candidates))
        return signals


def _to_date(ts) -> date:
    """Convert a timestamp value to a date object."""
    if isinstance(ts, date) and not isinstance(ts, datetime):
        return ts
    if isinstance(ts, datetime):
        return ts.date()
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    return date.fromisoformat(str(ts)[:10])
