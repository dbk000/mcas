import re
import logging
from config import CRYPTO_CONTEXT_WORDS

logger = logging.getLogger("symbol_matcher")


class SymbolMatcher:
    def __init__(self, aliases: dict):
        """
        aliases: {lowercase_alias: [(symbol, alias_type, is_ambiguous), ...]}
        Loaded from db.load_symbol_aliases()
        """
        self.aliases = aliases
        # Pre-compile cashtag regex: $DOGE, $BTC etc
        self.cashtag_re = re.compile(r'\$([A-Z]{2,10})\b')
        # Pre-compile hashtag ticker regex: #BTC, #ETH (Whale Alert format)
        self.hashtag_re = re.compile(r'#([A-Z]{2,10})\b')
        # Pre-compile word boundary ticker regex
        self.ticker_re = re.compile(r'\b([A-Z]{2,10})\b')

        # Build sets for fast lookup
        self.unambiguous_tickers = set()
        self.ambiguous_tickers = set()

        for alias_lower, entries in aliases.items():
            for symbol, alias_type, is_ambiguous in entries:
                if alias_type == 'ticker':
                    if is_ambiguous:
                        self.ambiguous_tickers.add(alias_lower.upper())
                    else:
                        self.unambiguous_tickers.add(alias_lower.upper())

    def extract_symbols(self, text: str) -> list[str]:
        """Extract MCAS symbol names from text. Returns deduplicated sorted list."""
        if not text:
            return []

        matched = set()
        text_lower = text.lower()

        # Tier 1: Cashtags ($DOGE) — always match
        for m in self.cashtag_re.finditer(text):
            ticker = m.group(1)
            # Try cashtag alias first
            key = '$' + ticker.lower()
            if key in self.aliases:
                for symbol, _, _ in self.aliases[key]:
                    matched.add(symbol)
            # Also try bare ticker alias
            key2 = ticker.lower()
            if key2 in self.aliases:
                for symbol, _, _ in self.aliases[key2]:
                    matched.add(symbol)

        # Tier 1b: Hashtag tickers (#BTC, #ETH) — Whale Alert format, treat same as cashtags
        for m in self.hashtag_re.finditer(text):
            ticker = m.group(1)
            key = ticker.lower()
            if key in self.aliases:
                for symbol, _, _ in self.aliases[key]:
                    matched.add(symbol)

        # Tier 2: Unambiguous tickers (DOGE, SOL, PEPE)
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.unambiguous_tickers:
                key = ticker.lower()
                if key in self.aliases:
                    for symbol, alias_type, _ in self.aliases[key]:
                        if alias_type == 'ticker':
                            matched.add(symbol)

        # Tier 3: Ambiguous tickers — only with crypto context nearby
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.ambiguous_tickers:
                # Check for crypto context within 100 chars
                start = max(0, m.start() - 100)
                end = min(len(text), m.end() + 100)
                context = text_lower[start:end]
                if any(cw in context for cw in CRYPTO_CONTEXT_WORDS):
                    key = ticker.lower()
                    if key in self.aliases:
                        for symbol, alias_type, _ in self.aliases[key]:
                            if alias_type == 'ticker':
                                matched.add(symbol)

        # Tier 4: Full names (case-insensitive word boundary)
        # Only for names >= 5 chars to avoid false positives
        for alias_lower, entries in self.aliases.items():
            if len(alias_lower) >= 5 and not alias_lower.startswith('$'):
                for symbol, alias_type, _ in entries:
                    if alias_type in ('name', 'common'):
                        if re.search(r'\b' + re.escape(alias_lower) + r'\b', text_lower):
                            matched.add(symbol)

        return sorted(matched)
