import os

# Database
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# Reddit
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET", "")
REDDIT_USERNAME = os.getenv("REDDIT_USERNAME", "")
REDDIT_PASSWORD = os.getenv("REDDIT_PASSWORD", "")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "mcas-research/1.0")

# Telegram
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID", "")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_SESSION_PATH = os.getenv("TELEGRAM_SESSION_PATH", "/app/sessions/mcas")

# Discord
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
DISCORD_ENABLED = os.getenv("DISCORD_ENABLED", "false").lower() in ("true", "1", "yes")

# Anthropic (Brain 2)
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
BRAIN2_MODEL = "claude-haiku-4-5-20251001"
BRAIN2_MAX_CALLS_PER_HOUR = 100
BRAIN2_DEDUP_HOURS = 4
BRAIN2_DAILY_COST_ALERT_USD = 10.0
BRAIN2_CIRCUIT_BREAKER_FAILURES = 5
BRAIN2_CIRCUIT_BREAKER_BACKOFF_S = 1800

# Collection intervals
REDDIT_POLL_INTERVAL_S = 900       # 15 min
RSS_POLL_INTERVAL_S = 300          # 5 min
COINGECKO_POLL_INTERVAL_S = 1800   # 30 min
BRAIN1_DELAY_AFTER_HOUR_S = 300    # 5 min past the hour
BRAIN2_DELAY_AFTER_HOUR_S = 600    # 10 min past the hour

# Reddit subreddits to monitor
REDDIT_SUBREDDITS = [
    "CryptoCurrency", "CryptoMoonShots", "altcoin", "SatoshiStreetBets",
    "CryptoMarkets", "defi", "binance", "ethtrader", "solana",
    "cardano", "algorand", "Chainlink", "Polkadot", "0xPolygon",
    "avax", "cosmosnetwork", "UniSwap", "pancakeswap",
]

# RSS feeds (crypto news)
RSS_FEEDS = [
    {"name": "CoinDesk", "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"},
    {"name": "CoinTelegraph", "url": "https://cointelegraph.com/rss"},
    {"name": "TheBlock", "url": "https://www.theblock.co/rss/all"},
    {"name": "Decrypt", "url": "https://decrypt.co/feed"},
    {"name": "CryptoSlate", "url": "https://cryptoslate.com/feed/"},
    {"name": "NewsBTC", "url": "https://www.newsbtc.com/feed/"},
    {"name": "Bitcoinist", "url": "https://bitcoinist.com/feed/"},
    {"name": "DailyHodl", "url": "https://dailyhodl.com/feed/"},
]

# Content truncation
MAX_CONTENT_LENGTH = 2000

# Crypto context words for ambiguous ticker matching
CRYPTO_CONTEXT_WORDS = {
    "token", "coin", "crypto", "listing", "binance", "exchange", "pump",
    "dump", "chart", "buy", "sell", "moon", "bullish", "bearish", "hodl",
    "defi", "dex", "cex", "airdrop", "staking", "yield", "tvl", "nft",
    "blockchain", "wallet", "whale", "altcoin", "usdt", "usdc", "btc",
    "eth", "trading", "futures", "leverage", "long", "short", "liquidation",
    "mcap", "market cap", "volume", "price", "ath", "support", "resistance",
    "breakout", "dip", "rally", "accumulation", "distribution",
}
