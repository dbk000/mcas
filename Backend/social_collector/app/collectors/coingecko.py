import logging
from datetime import datetime, timezone

import requests

from db import batch_insert_posts
from symbol_matcher import SymbolMatcher

logger = logging.getLogger("coingecko")

TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"


class CoinGeckoCollector:
    def __init__(self, matcher: SymbolMatcher):
        self.matcher = matcher
        self.last_trending_ids = set()

    def collect_cycle(self) -> int:
        """Poll CoinGecko trending endpoint. Returns number of posts ingested."""
        try:
            resp = requests.get(TRENDING_URL, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error("CoinGecko API error: %s", e)
            return 0

        coins = data.get("coins", [])
        if not coins:
            return 0

        batch_id = f"coingecko_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        posts = []
        current_ids = set()

        for item in coins:
            coin = item.get("item", {})
            coin_id = coin.get("id", "")
            symbol = coin.get("symbol", "").upper()
            name = coin.get("name", "")
            market_cap_rank = coin.get("market_cap_rank") or 0
            score = coin.get("score", 0)

            current_ids.add(coin_id)

            # Build synthetic text for symbol matching
            text = f"{symbol} {name} trending on CoinGecko"
            matched_symbols = self.matcher.extract_symbols(text)

            if not matched_symbols:
                continue

            source_id = f"cg_trending_{coin_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H')}"

            content = (
                f"Trending #{score + 1} on CoinGecko. "
                f"Symbol: {symbol}, Name: {name}, "
                f"Market cap rank: {market_cap_rank}."
            )

            posts.append({
                "source": "coingecko",
                "source_id": source_id,
                "channel": "trending",
                "author": None,
                "title": f"{name} ({symbol}) trending on CoinGecko",
                "content": content,
                "post_ts": datetime.now(timezone.utc),
                "engagement_score": max(0, 100 - score * 10),  # Higher rank = higher score
                "symbols": matched_symbols,
                "batch_id": batch_id,
            })

        if posts:
            n = batch_insert_posts(posts)
            logger.info("CoinGecko: ingested %d trending coins with symbol matches", n)

        self.last_trending_ids = current_ids
        return len(posts)
