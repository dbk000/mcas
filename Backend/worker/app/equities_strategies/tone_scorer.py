"""
Tone Scorer — Keyword-based title tone classification
======================================================
Pure-function module. No DB dependency. Testable in isolation.

Scores equity news headlines for directional tone across 4 categories:
  ANALYST  ±1.0 — upgrades/downgrades, PT changes, ratings
  EARNINGS ±0.9 — beat/miss, strong/weak results
  GUIDANCE ±0.9 — raises/lowers outlook, above/below consensus
  INSIDER  ±0.8 — insider buys/sells, buybacks, dividends

PRICE_ACTION excluded: re-reports ret_1d (redundant with OHLCV data).
SEC_EDGAR and OPEN_SOCIAL sources excluded per review findings.
"""

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Negation tokens — checked in 3-token window before keyword match
# ---------------------------------------------------------------------------
_NEGATION_TOKENS = frozenset({
    'not', 'no', 'never', "don't", "doesn't", 'without',
    'unlikely', 'fails', 'despite', 'but', 'although', 'however',
})

# ---------------------------------------------------------------------------
# Category definitions: (weight, [(regex_pattern, polarity), ...])
# polarity: +1 = bullish, -1 = bearish
# First match per category wins. Patterns ordered longest/most-specific first.
# ---------------------------------------------------------------------------
_CATEGORY_DEFS = {
    'ANALYST': {
        'weight': 1.0,
        'patterns': [
            # Bullish
            (r'initiates? (?:coverage )?(?:at |with )(?:buy|overweight|outperform)', +1),
            (r'price target (?:raised|increased|hiked|boosted)', +1),
            (r'raises? (?:price )?target', +1),
            (r'reiterates? (?:buy|overweight|outperform)\b', +1),
            (r'maintains? (?:buy|overweight|outperform)\b', +1),
            (r'sector outperform', +1),
            (r'\bpt raised\b', +1),
            (r'\bstrong buy\b', +1),
            (r'\bbuy rating\b', +1),
            (r'\btop pick\b', +1),
            (r'overweight\b', +1),
            (r'outperform\b', +1),
            (r'upgrades?\b', +1),
            # Bearish
            (r'initiates? (?:coverage )?(?:at |with )(?:sell|underweight|underperform)', -1),
            (r'price target (?:cut|lowered|reduced|slashed)', -1),
            (r'(?:cuts?|lowers?|reduces?) (?:price )?target', -1),
            (r'reiterates? (?:sell|underweight|underperform)\b', -1),
            (r'sector underperform', -1),
            (r'\bpt (?:cut|lowered)\b', -1),
            (r'\bsell rating\b', -1),
            (r'underweight\b', -1),
            (r'underperform\b', -1),
            (r'downgrades?\b', -1),
        ],
    },
    'EARNINGS': {
        'weight': 0.9,
        'patterns': [
            # Bullish
            (r'(?:strong|solid|robust|stellar) earnings', +1),
            (r'record (?:revenue|profit|earnings)', +1),
            (r'better[- ]than[- ]expected', +1),
            (r'exceeds? (?:expectations|estimates)', +1),
            (r'surprises? to the upside', +1),
            (r'blowout quarter', +1),
            (r'beats? estimates', +1),
            (r'tops? estimates', +1),
            (r'earnings beat', +1),
            (r'revenue beat', +1),
            (r'\beps beat\b', +1),
            # Bearish
            (r'(?:weak|poor|disappointing|dismal) earnings', -1),
            (r'(?:revenue|earnings|profit) (?:decline|drop|fall|plunge)', -1),
            (r'worse[- ]than[- ]expected', -1),
            (r'surprises? to the downside', -1),
            (r'below expectations', -1),
            (r'miss(?:es)? estimates', -1),
            (r'earnings miss', -1),
            (r'revenue miss', -1),
            (r'\beps miss\b', -1),
            (r'disappoints?\b', -1),
        ],
    },
    'GUIDANCE': {
        'weight': 0.9,
        'patterns': [
            # Bullish
            (r'(?:raises?|increases?) (?:full[- ]year|annual|quarterly) (?:outlook|guidance|forecast)', +1),
            (r'raises? (?:outlook|forecast|guidance)', +1),
            (r'(?:upbeat|positive|strong|optimistic) (?:outlook|guidance|forecast)', +1),
            (r'boosts? forecast', +1),
            (r'lifts? guidance', +1),
            (r'above consensus', +1),
            # Bearish
            (r'(?:lowers?|cuts?|reduces?) (?:full[- ]year|annual|quarterly) (?:outlook|guidance|forecast)', -1),
            (r'(?:lowers?|cuts?) (?:outlook|forecast|guidance)', -1),
            (r'(?:cautious|weak|tepid|disappointing) (?:outlook|guidance|forecast)', -1),
            (r'below consensus', -1),
            (r'warns? on\b', -1),
        ],
    },
    'INSIDER': {
        'weight': 0.8,
        'patterns': [
            # Bullish
            (r'buyback (?:announced|authorized|expanded)', +1),
            (r'share (?:buyback|repurchase)', +1),
            (r'dividend (?:increase|hike|raise|boost)', +1),
            (r'insider (?:buying|purchase|buys)', +1),
            (r'(?:ceo|cfo|director|officer) buys?\b', +1),
            (r'\bform 4\b.*\bbuy\b', +1),
            # Bearish
            (r'dividend (?:cut|slash|suspend|eliminat)', -1),
            (r'insider (?:selling|sale|sells)', -1),
            (r'(?:ceo|cfo|director|officer) sells?\b', -1),
            (r'\bform 4\b.*\bsell\b', -1),
        ],
    },
}

# ---------------------------------------------------------------------------
# Compiled patterns — built once at import
# ---------------------------------------------------------------------------
_COMPILED_CATEGORIES = {}
_TONE_AVAILABLE = False


def _compile_all():
    """Compile all regex patterns."""
    global _TONE_AVAILABLE
    for cat_name, cat_def in _CATEGORY_DEFS.items():
        compiled = []
        for pattern_str, polarity in cat_def['patterns']:
            try:
                compiled.append((re.compile(pattern_str, re.IGNORECASE), polarity))
            except re.error as e:
                logger.warning("tone_scorer: failed to compile '%s': %s", pattern_str, e)
        _COMPILED_CATEGORIES[cat_name] = {
            'weight': cat_def['weight'],
            'patterns': compiled,
        }
    _TONE_AVAILABLE = True


try:
    _compile_all()
except Exception as e:
    logger.error("tone_scorer: initialization failed: %s", e)
    _TONE_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _has_negation_before(text_lower: str, match_start: int) -> bool:
    """Check if negation token appears in the 3 tokens before match position."""
    prefix = text_lower[:match_start].strip()
    if not prefix:
        return False
    tokens = prefix.split()
    window = tokens[-3:] if len(tokens) >= 3 else tokens
    return any(t.rstrip('.,;:!?') in _NEGATION_TOKENS for t in window)


def score_title_tone(title: str, source: str = "", anti_bot_score: float = 0.0) -> float:
    """Score a single news title for directional tone.

    Returns float in [-1.0, +1.0]. Returns 0.0 for:
      - Non-OPEN_NEWS sources (SEC_EDGAR, OPEN_SOCIAL excluded)
      - High anti_bot_score (> 0.5)
      - Titles with < 3 tokens
      - No keyword matches
      - Module initialization failure
    """
    if not _TONE_AVAILABLE:
        return 0.0

    # Source filter: only OPEN_NEWS
    if source and source != 'OPEN_NEWS':
        return 0.0

    # Anti-bot filter
    if anti_bot_score > 0.5:
        return 0.0

    # Min tokens (3 filters junk like "424B2 BAC" while keeping "Goldman downgrades MSFT")
    text_lower = title.lower().strip()
    tokens = text_lower.split()
    if len(tokens) < 3:
        return 0.0

    # Score each category (first match per category wins)
    total = 0.0
    for cat_info in _COMPILED_CATEGORIES.values():
        for regex, polarity in cat_info['patterns']:
            m = regex.search(text_lower)
            if m:
                negated = _has_negation_before(text_lower, m.start())
                effective = -polarity if negated else polarity
                total += effective * cat_info['weight']
                break  # one match per category

    return max(-1.0, min(1.0, total))
