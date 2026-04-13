"""Canonical keyword cluster registry.

Single source of truth for keyword-based category matching and velocity
calculation. Consumers:

  * ``Backend/scripts/validate_regime_thesis.py`` — macro clusters
    (GEOPOLITICAL / MONETARY / REGULATORY / STRUCTURAL)
  * ``Backend/governance/signal_worker.py`` (indirectly via social feature
    library) — C-series social features
  * ``Backend/scripts/label_shock_events.py`` — regime Phase 1 (A1)
  * ``Backend/scripts/discover_social_safe_filters.py`` — A2 Phase 2

Do **not** define keyword lists anywhere else. Import from here.

Naming convention
-----------------

* Macro clusters (from ``validate_regime_thesis.py``) keep their original
  names: ``GEOPOLITICAL``, ``MONETARY``, ``REGULATORY``, ``STRUCTURAL``.
  Patterns and ordering are byte-identical to the source so that historical
  ``validate_regime_thesis.py`` runs reproduce exactly.

* Social clusters (derived from ``governance/social_features.py``) are
  prefixed with ``SOCIAL_`` to avoid shadowing / accidental collision:
  ``SOCIAL_WHALE``, ``SOCIAL_NEWS``, ``SOCIAL_COMMUNITY``, ``SOCIAL_REDDIT``.
  The upstream ``social_features.py`` categorises posts by *channel name*
  (not by post content), so for content-based text matching we derive
  regexes from the canonical channel identifiers plus a small set of
  polarity tokens that already appear in the whale-alert helper.
  Channel-level categorisation remains the authoritative path via
  ``social_features.categorize_channel`` — the regexes here are for the
  separate use case of scanning *post text* (e.g. when the channel field is
  missing or when a caller wants a text-only fallback).

All functions are pure (no DB, no network I/O, no clock reads).
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Iterable


# ---------------------------------------------------------------------------
# Macro keyword clusters (extracted verbatim from validate_regime_thesis.py).
# Do not edit without simultaneously updating the consumer spot-check.
# ---------------------------------------------------------------------------

_MACRO_CLUSTERS: dict[str, list[str]] = {
    "GEOPOLITICAL": [
        r"\biran\b", r"\bhormuz\b", r"\bstrait\b", r"\bisrael\b", r"\bgaza\b",
        r"\brussia\b", r"\bukraine\b", r"\bnato\b", r"\bputin\b",
        r"\bchina\s+invasion\b", r"\btaiwan\b", r"\bnorth\s+korea\b",
        r"\btariff\b", r"\bsanction\b", r"\bembargo\b",
        r"\bmissile\b", r"\bairstrike\b", r"\bdrone\s+strike\b",
        r"\bwar\b", r"\bconflict\b", r"\boil\s+shock\b",
    ],
    "MONETARY": [
        r"\bfed\b", r"\bfomc\b", r"\bjerome\s+powell\b", r"\bpowell\b",
        r"\brate\s+hike\b", r"\brate\s+cut\b", r"\binterest\s+rate\b",
        r"\brate\s+decision\b", r"\brates?\s+(higher|lower)\b",
        r"\binflation\b", r"\bcpi\b", r"\bppi\b", r"\bhot\s+cpi\b",
        r"\bhawkish\b", r"\bdovish\b", r"\bquantitative\s+tightening\b",
        r"\bliquidity\s+crunch\b", r"\brecession\b",
        r"\bbond\s+yields\b", r"\b10y\s+yield\b", r"\btreasury\s+yield\b",
        r"\bdxy\b", r"\bdollar\s+strength\b",
    ],
    "REGULATORY": [
        r"\bsec\b", r"\bcftc\b", r"\bdoj\b", r"\bfincen\b",
        r"\bgary\s+gensler\b", r"\bsubpoena\b", r"\blawsuit\b", r"\binjunction\b",
        r"\bban(?:ned)?\b.*\bcrypto\b", r"\bcrackdown\b",
        r"\bbinance\b.*\b(charge|fine|settlement|indict)",
        r"\bcoinbase\b.*\b(charge|fine|settlement|indict)",
        r"\btether\b.*\b(probe|investigat)",
        r"\bmica\b", r"\beu\s+regulation\b",
    ],
    "STRUCTURAL": [
        r"\bexchange\s+hack\b", r"\bhacked\b", r"\bexploit\b", r"\bdrained\b",
        r"\bdepeg\b", r"\bde-peg\b", r"\bstablecoin\s+(depeg|collapse)\b",
        r"\busdt\s+depeg\b", r"\busdc\s+depeg\b",
        r"\bmt\.?\s*gox\b", r"\bftx\b.*\bcollapse\b", r"\bcelsius\b",
        r"\bluna\b.*\bcrash\b", r"\bterra\b.*\bcollapse\b",
        r"\bliquidation\s+cascade\b", r"\bflash\s+crash\b",
        r"\betf\s+rejection\b", r"\betf\s+delay\b",
        r"\bbankruptcy\b", r"\binsolvency\b",
    ],
}


# ---------------------------------------------------------------------------
# Social keyword clusters (derived from governance/social_features.py).
# Source categorises by channel name; these regexes match channel strings
# or closely related tokens appearing in post text. Whale polarity tokens
# are lifted verbatim from ``compute_whale_polarity`` so that whale
# polarity detection elsewhere continues to match.
# ---------------------------------------------------------------------------

# Channel names from social_features.WHALE_CHANNELS + polarity tokens.
_SOCIAL_WHALE_PATTERNS: list[str] = [
    r"\bwhalebotalerts\b", r"\bwhalebotpumps\b",
    r"\bwhale_alert_io\b", r"\bwhalealert\b",
    r"\bwhale_alert\b", r"\bwhale\s+alert\b",
    r"\bwhale\b",
    # polarity tokens from compute_whale_polarity (bullish/bearish)
    r"(?:bought|buy|accumul|transfer.*to.*exchange|deposit)",
    r"(?:sold|sell|dump|transfer.*from.*exchange|withdraw)",
]

# Channel names from social_features.NEWS_CHANNELS + SOURCE_CATEGORIES=news
_SOCIAL_NEWS_PATTERNS: list[str] = [
    r"\bcoindesk\b", r"\bcointelegraph\b", r"\btheblock\b",
    r"\bdecrypt_co\b", r"\bdecrypt\b",
    r"\bcrypto_news\b", r"\bcrypto\s+news\b",
    r"\bcoingecko\b",
    r"\brss\b",
]

# Channel categorisation: telegram channels that are not whale/news.
# We do not have a fixed allowlist for community channels (the source
# categorises "telegram + not in NEWS_CHANNELS + not whale" as community),
# so we match explicit tokens a caller could put in free-text.
_SOCIAL_COMMUNITY_PATTERNS: list[str] = [
    r"\btelegram\b", r"\bchat\b", r"\bcommunity\b",
    r"\bdiscord\b", r"\bsignal\s+group\b",
]

_SOCIAL_REDDIT_PATTERNS: list[str] = [
    r"\breddit\b", r"\br/\w+", r"\bsubreddit\b",
]


# ---------------------------------------------------------------------------
# Public registry
# ---------------------------------------------------------------------------

# Preserve insertion order — macro clusters first, then social.
_ALL_CLUSTERS_RAW: dict[str, list[str]] = {
    **_MACRO_CLUSTERS,
    "SOCIAL_WHALE": _SOCIAL_WHALE_PATTERNS,
    "SOCIAL_NEWS": _SOCIAL_NEWS_PATTERNS,
    "SOCIAL_COMMUNITY": _SOCIAL_COMMUNITY_PATTERNS,
    "SOCIAL_REDDIT": _SOCIAL_REDDIT_PATTERNS,
}

#: Public mapping of category name -> compiled regex pattern.
#:
#: Patterns are joined with ``|`` and compiled case-insensitively — the
#: same construction used historically by ``validate_regime_thesis.py``
#: so that match-sets over identical inputs are byte-identical.
CLUSTERS: dict[str, re.Pattern] = {
    cat: re.compile("|".join(patterns), re.IGNORECASE)
    for cat, patterns in _ALL_CLUSTERS_RAW.items()
}


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def match_categories(text: str) -> list[str]:
    """Return list of cluster names whose regex matches ``text``.

    Membership is independent per category: a string that hits both
    GEOPOLITICAL and MONETARY appears in both lists. Order of returned
    names follows the registry's insertion order (macro first, then
    SOCIAL_*).
    """
    if not text:
        return []
    return [cat for cat, pat in CLUSTERS.items() if pat.search(text)]


# ---------------------------------------------------------------------------
# Velocity primitives
# ---------------------------------------------------------------------------

def _count_matches(
    category: str,
    texts: Iterable[tuple[int, str]],
    start_ts: int,
    end_ts: int,
) -> int:
    """Count texts whose ts falls in [start_ts, end_ts) and which match ``category``.

    ``start_ts`` / ``end_ts`` are integer timestamps in the same unit as
    the input tuples (seconds). Half-open interval per CLAUDE.md §2.
    """
    if category not in CLUSTERS:
        raise KeyError(f"unknown category: {category!r}")
    pat = CLUSTERS[category]
    n = 0
    for ts, text in texts:
        if start_ts <= ts < end_ts:
            if text and pat.search(text):
                n += 1
    return n


def velocity(
    category: str,
    texts_in_window: list[tuple[int, str]],
    window_seconds: int,
) -> float:
    """Matches per hour for ``category`` in ``texts_in_window``.

    ``texts_in_window`` is a list of ``(ts_seconds, text)`` tuples — the
    caller is responsible for selecting the window; this function does
    not re-filter by timestamp. ``window_seconds`` is the nominal window
    width used to normalise the count to a matches-per-hour rate.

    Returns ``matches / (window_seconds / 3600)``. If ``window_seconds``
    is non-positive, returns ``0.0``.
    """
    if category not in CLUSTERS:
        raise KeyError(f"unknown category: {category!r}")
    if window_seconds <= 0:
        return 0.0
    pat = CLUSTERS[category]
    matches = sum(1 for _, text in texts_in_window if text and pat.search(text))
    hours = window_seconds / 3600.0
    return matches / hours


def baseline_velocity(
    category: str,
    texts: list[tuple[int, str]],
    now_ts: int,
    baseline_hours: int = 168,
    lookback_hours: int = 24,
) -> float:
    """Trailing baseline matches/hour.

    Computes matches per hour over the trailing ``baseline_hours`` window
    that ends at ``now_ts - lookback_hours * 3600`` — i.e. the window
    *preceding* the comparison window so the ratio in
    :func:`velocity_ratio` is strictly trailing and leakage-free, matching
    the semantics of ``validate_regime_thesis.py``'s rolling baseline.

    Returns ``0.0`` if the window is empty. Callers who want the
    validate_regime_thesis floor (``max(b, 0.5)``) should apply it
    themselves — keeping the primitive zero-biased preserves honest
    reporting for the "no-baseline-evidence" case.
    """
    if category not in CLUSTERS:
        raise KeyError(f"unknown category: {category!r}")
    if baseline_hours <= 0:
        return 0.0
    end_ts = now_ts - lookback_hours * 3600
    start_ts = end_ts - baseline_hours * 3600
    n = _count_matches(category, texts, start_ts, end_ts)
    return n / float(baseline_hours)


def velocity_ratio(
    category: str,
    texts: list[tuple[int, str]],
    now_ts: int,
    lookback_hours: int = 24,
    baseline_hours: int = 168,
    baseline_floor: float = 0.5,
) -> float:
    """Ratio of recent matches/hour to trailing-baseline matches/hour.

    * Numerator: matches/hour over ``[now_ts - lookback_hours*3600, now_ts)``.
    * Denominator: matches/hour over the ``baseline_hours`` window
      immediately preceding the numerator window, floored at
      ``baseline_floor`` to prevent div-by-zero noise spikes. The floor
      default of 0.5 matches ``validate_regime_thesis.py`` exactly.

    Returns ``0.0`` if the lookback window is empty OR (for the
    zero-on-zero case) if both numerator and denominator are zero.
    """
    if category not in CLUSTERS:
        raise KeyError(f"unknown category: {category!r}")
    if lookback_hours <= 0:
        return 0.0
    recent_start = now_ts - lookback_hours * 3600
    recent_matches = _count_matches(category, texts, recent_start, now_ts)
    recent_per_hour = recent_matches / float(lookback_hours)

    base = baseline_velocity(
        category, texts, now_ts,
        baseline_hours=baseline_hours,
        lookback_hours=lookback_hours,
    )
    denom = max(base, baseline_floor)
    if denom <= 0:
        return 0.0
    return recent_per_hour / denom


# ---------------------------------------------------------------------------
# Bulk helper — mirrors validate_regime_thesis.scan_social_posts semantics
# for callers that want per-category hourly counts.
# ---------------------------------------------------------------------------

def hourly_counts(
    texts: Iterable[tuple[int, str]],
    start_ts: int,
    end_ts: int,
) -> dict[str, dict[int, int]]:
    """Return {category: {hour_start_ts: count}} over [start_ts, end_ts).

    ``hour_start_ts`` is the UNIX epoch second at the top of the hour
    (floored). Categories with zero matches still appear as empty dicts.
    Useful for reproducing ``validate_regime_thesis`` histograms without
    re-implementing the regex loop.
    """
    out: dict[str, dict[int, int]] = {cat: defaultdict(int) for cat in CLUSTERS}
    for ts, text in texts:
        if not (start_ts <= ts < end_ts) or not text:
            continue
        hour_ts = (ts // 3600) * 3600
        for cat, pat in CLUSTERS.items():
            if pat.search(text):
                out[cat][hour_ts] += 1
    return {cat: dict(counts) for cat, counts in out.items()}
