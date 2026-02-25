"""Equities Strategies Package."""
from equities_strategies.base import EquitiesSignal, EquitiesStrategy
from equities_strategies.eq_tsmom import EqTsmom
from equities_strategies.eq_xsmom import EqXsmom
from equities_strategies.eq_rev1a import EqRev1a
from equities_strategies.eq_rev1b import EqRev1b
from equities_strategies.eq_vpd import EqVpd
from equities_strategies.eq_atc import EqAtc
from equities_strategies.eq_nvda_macro import EqNvdaMacro
from equities_strategies.features import (
    load_ohlcv_matrix, load_news_matrix, load_atc_matrix, engineer_features,
    check_data_freshness, check_split_guard,
)

# Safe import: tone_scorer uses compiled regexes; degrade gracefully if broken
try:
    from equities_strategies.tone_scorer import score_title_tone
except Exception:
    score_title_tone = None

ALL_STRATEGIES = [EqTsmom, EqXsmom, EqRev1a, EqRev1b, EqVpd, EqAtc, EqNvdaMacro]

__all__ = [
    "EquitiesSignal", "EquitiesStrategy",
    "EqTsmom", "EqXsmom", "EqRev1a", "EqRev1b", "EqVpd", "EqAtc", "EqNvdaMacro",
    "ALL_STRATEGIES",
    "load_ohlcv_matrix", "load_news_matrix", "load_atc_matrix", "engineer_features",
    "check_data_freshness", "check_split_guard",
    "score_title_tone",
]
