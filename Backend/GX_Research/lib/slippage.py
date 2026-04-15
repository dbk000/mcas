"""
Slippage + fee model. Per-symbol-per-TF, keyed off median 24h quote volume tier.
Plan §8. Fee fixed at 20 bps round-trip (Binance spot taker × 2).
"""
from __future__ import annotations

from typing import Literal

Tier = Literal["T1", "T2", "T3", "T4", "T5"]

TIER_CUTOFFS_USD: list[tuple[float, str]] = [
    (100_000_000.0, "T1"),
    (25_000_000.0, "T2"),
    (5_000_000.0, "T3"),
    (1_000_000.0, "T4"),
    (0.0, "T5"),
]

SLIP_TABLE_BPS_RT: dict[tuple[str, str], float] = {
    ("5m", "T1"): 6,   ("5m", "T2"): 10,  ("5m", "T3"): 16,  ("5m", "T4"): 26,  ("5m", "T5"): 40,
    ("15m","T1"): 5,   ("15m","T2"): 8,   ("15m","T3"): 13,  ("15m","T4"): 22,  ("15m","T5"): 34,
    ("1h", "T1"): 4,   ("1h", "T2"): 6,   ("1h", "T3"): 10,  ("1h", "T4"): 16,  ("1h", "T5"): 26,
    ("4h", "T1"): 3,   ("4h", "T2"): 5,   ("4h", "T3"): 8,   ("4h", "T4"): 13,  ("4h", "T5"): 20,
    ("1d", "T1"): 3,   ("1d", "T2"): 4,   ("1d", "T3"): 7,   ("1d", "T4"): 11,  ("1d", "T5"): 17,
}

FEE_BPS_RT: float = 20.0  # Binance spot taker 10bps × 2 sides


def assign_tier(median_24h_quote_vol_usd: float) -> Tier:
    for cutoff, tier in TIER_CUTOFFS_USD:
        if median_24h_quote_vol_usd > cutoff:
            return tier  # type: ignore[return-value]
    return "T5"


def slippage_bps(tier: Tier, timeframe: str, slip_multiplier: float = 1.0) -> float:
    base = SLIP_TABLE_BPS_RT.get((timeframe, tier))
    if base is None:
        return 30.0 * slip_multiplier
    return float(base) * slip_multiplier


def total_cost_bps(tier: Tier, timeframe: str, slip_multiplier: float = 1.0) -> float:
    """Fees + slippage in bps, round-trip."""
    return FEE_BPS_RT + slippage_bps(tier, timeframe, slip_multiplier)


def apply_costs_to_trades(trades_df, tier: Tier, timeframe: str, slip_multiplier: float = 1.0):
    """
    Given a DataFrame with 'pnl_gross_bps', produce 'pnl_net_bps' after fees+slippage.
    Called per (symbol, timeframe) since tier is per-symbol.
    """
    cost = total_cost_bps(tier, timeframe, slip_multiplier)
    trades_df = trades_df.copy()
    trades_df["slip_bps_rt"] = cost
    trades_df["pnl_net_bps"] = trades_df["pnl_gross_bps"] - cost
    trades_df["liquidity_tier"] = tier
    return trades_df
