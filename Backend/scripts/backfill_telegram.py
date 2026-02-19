#!/usr/bin/env python3
"""
Telegram Historical Backfill
=============================

Fetches up to 1 year of historical messages from the 25 configured Telegram
channels, matches symbols via symbol_aliases, and inserts into social_posts.

Two-step process:
  1) FIRST AUTH (interactive, one-time):
     cd /srv/mcas/Backend && docker compose -f docker-compose.yml run -it social_collector \
         python -c "
     from telethon.sync import TelegramClient
     c = TelegramClient('/app/sessions/mcas', 31396919, '2a80770764355d5d6c0e87736584f8fb')
     c.start()
     print('Auth complete — session saved')
     c.disconnect()
     "

  2) BACKFILL (detached, in tmux):
     tmux new -s tg-backfill
     cd /srv/mcas/Backend && docker compose -f docker-compose.yml run --rm -it \
         social_collector python /app/backfill_telegram.py --days 365

Note: This script runs INSIDE the social_collector container (not worker), because
that's where the Telethon session file and dependencies live.
"""

import os
import re
import sys
import json
import time
import asyncio
import logging
import argparse
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from pathlib import Path

import psycopg

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("tg_backfill")

# ─── Crypto context words (for ambiguous ticker matching) ─────────────────────

CRYPTO_CONTEXT_WORDS = {
    "token", "coin", "crypto", "listing", "binance", "exchange", "pump",
    "dump", "chart", "buy", "sell", "moon", "bullish", "bearish", "hodl",
    "defi", "dex", "cex", "airdrop", "staking", "yield", "tvl", "nft",
    "blockchain", "wallet", "whale", "altcoin", "usdt", "usdc", "btc",
    "eth", "trading", "futures", "leverage", "long", "short", "liquidation",
    "mcap", "market cap", "volume", "price", "ath", "support", "resistance",
    "breakout", "dip", "rally", "accumulation", "distribution",
}

# ─── SymbolMatcher ────────────────────────────────────────────────────────────

class SymbolMatcher:
    def __init__(self, aliases: dict):
        self.aliases = aliases
        self.cashtag_re = re.compile(r'\$([A-Z]{2,10})\b')
        self.hashtag_re = re.compile(r'#([A-Z]{2,10})\b')
        self.ticker_re = re.compile(r'\b([A-Z]{2,10})\b')
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
        if not text:
            return []
        matched = set()
        text_lower = text.lower()
        for m in self.cashtag_re.finditer(text):
            ticker = m.group(1)
            key = '$' + ticker.lower()
            if key in self.aliases:
                for symbol, _, _ in self.aliases[key]:
                    matched.add(symbol)
            key2 = ticker.lower()
            if key2 in self.aliases:
                for symbol, _, _ in self.aliases[key2]:
                    matched.add(symbol)
        for m in self.hashtag_re.finditer(text):
            ticker = m.group(1)
            key = ticker.lower()
            if key in self.aliases:
                for symbol, _, _ in self.aliases[key]:
                    matched.add(symbol)
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.unambiguous_tickers:
                key = ticker.lower()
                if key in self.aliases:
                    for symbol, alias_type, _ in self.aliases[key]:
                        if alias_type == 'ticker':
                            matched.add(symbol)
        for m in self.ticker_re.finditer(text):
            ticker = m.group(1)
            if ticker in self.ambiguous_tickers:
                start = max(0, m.start() - 100)
                end = min(len(text), m.end() + 100)
                context = text_lower[start:end]
                if any(cw in context for cw in CRYPTO_CONTEXT_WORDS):
                    key = ticker.lower()
                    if key in self.aliases:
                        for symbol, alias_type, _ in self.aliases[key]:
                            if alias_type == 'ticker':
                                matched.add(symbol)
        for alias_lower, entries in self.aliases.items():
            if len(alias_lower) >= 5 and not alias_lower.startswith('$'):
                for symbol, alias_type, _ in entries:
                    if alias_type in ('name', 'common'):
                        if re.search(r'\b' + re.escape(alias_lower) + r'\b', text_lower):
                            matched.add(symbol)
        return sorted(matched)


# ─── DB Helpers ───────────────────────────────────────────────────────────────

def load_symbol_aliases(database_url: str) -> dict:
    aliases = {}
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, alias, alias_type, is_ambiguous FROM symbol_aliases")
            for symbol, alias, alias_type, is_ambiguous in cur:
                key = alias.lower()
                if key not in aliases:
                    aliases[key] = []
                aliases[key].append((symbol, alias_type, is_ambiguous))
    return aliases


def batch_insert_posts(database_url: str, posts: list[dict]) -> int:
    if not posts:
        return 0
    sql = """
        INSERT INTO social_posts (source, source_id, channel, author, title, content,
                                  post_ts, engagement_score, symbols, batch_id)
        VALUES (%(source)s, %(source_id)s, %(channel)s, %(author)s, %(title)s, %(content)s,
                %(post_ts)s, %(engagement_score)s, %(symbols)s, %(batch_id)s)
        ON CONFLICT (source, source_id) DO NOTHING
    """
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, posts)
        conn.commit()
    return len(posts)


# ─── Channel config ──────────────────────────────────────────────────────────

def load_channels() -> list[dict]:
    """Load channel list from telegram_channels.json."""
    # Try multiple paths (inside container or host)
    candidates = [
        "/app/telegram_channels.json",
        os.path.join(os.path.dirname(__file__), "..", "social_collector", "app", "telegram_channels.json"),
        os.path.join(os.path.dirname(__file__), "telegram_channels.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            with open(path) as f:
                data = json.load(f)
            channels = data.get("channels", [])
            logger.info("Loaded %d channels from %s", len(channels), path)
            return channels
    logger.error("telegram_channels.json not found in any expected location")
    sys.exit(1)


# ─── Telegram Backfiller ─────────────────────────────────────────────────────

async def backfill_channel(client, channel_username: str, category: str,
                           start_date: datetime, matcher: SymbolMatcher,
                           database_url: str) -> dict:
    """Fetch all messages from a single channel back to start_date.

    Returns stats dict.
    """
    stats = {"fetched": 0, "matched": 0, "inserted": 0, "errors": 0}

    try:
        entity = await client.get_entity(channel_username)
    except Exception as e:
        logger.error("  Could not resolve @%s: %s", channel_username, e)
        stats["errors"] = 1
        return stats

    channel_title = getattr(entity, 'title', channel_username)
    logger.info("  Resolved @%s -> \"%s\"", channel_username, channel_title)

    batch = []
    batch_num = 0
    last_log_time = time.time()

    try:
        async for message in client.iter_messages(entity, reverse=False,
                                                   offset_date=None):
            # reverse=False means newest first; stop when we pass start_date
            if message.date and message.date.replace(tzinfo=timezone.utc) < start_date:
                break

            stats["fetched"] += 1

            text = message.text or ""
            if not text or len(text) < 3:
                continue

            symbols = matcher.extract_symbols(text)
            if not symbols:
                continue

            msg_date = message.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)

            batch.append({
                "source": "telegram_historical",
                "source_id": f"tg_{message.id}_{entity.id}",
                "channel": channel_username,
                "author": None,
                "title": None,
                "content": text[:2000],
                "post_ts": msg_date,
                "engagement_score": message.views or 0,
                "symbols": symbols,
                "batch_id": f"tg_backfill_{channel_username}_{batch_num}",
            })

            stats["matched"] += 1

            # Incremental writes — flush every 500 matched posts
            if len(batch) >= 500:
                n = batch_insert_posts(database_url, batch)
                stats["inserted"] += n
                batch_num += 1
                batch = []

            # Progress log every 30 seconds
            now = time.time()
            if now - last_log_time > 30:
                logger.info("    @%s progress: fetched=%d matched=%d, at %s",
                            channel_username, stats["fetched"], stats["matched"],
                            msg_date.strftime("%Y-%m-%d"))
                last_log_time = now

    except Exception as e:
        logger.error("  Error iterating @%s: %s", channel_username, e)
        stats["errors"] += 1

    # Flush remaining
    if batch:
        n = batch_insert_posts(database_url, batch)
        stats["inserted"] += n

    return stats


async def run_backfill(args):
    """Main async backfill routine."""
    from telethon import TelegramClient

    database_url = args.db_url or os.environ.get("DATABASE_URL")
    if not database_url:
        logger.error("No DATABASE_URL. Set env var or pass --db-url")
        sys.exit(1)

    api_id = args.api_id or os.environ.get("TELEGRAM_API_ID")
    api_hash = args.api_hash or os.environ.get("TELEGRAM_API_HASH")
    session_path = args.session or os.environ.get("TELEGRAM_SESSION_PATH", "/app/sessions/mcas")

    if not api_id or not api_hash:
        logger.error("TELEGRAM_API_ID and TELEGRAM_API_HASH required (env or --api-id/--api-hash)")
        sys.exit(1)

    end_date = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    logger.info("=" * 70)
    logger.info("TELEGRAM HISTORICAL BACKFILL")
    logger.info("=" * 70)
    logger.info("Window: %s to %s (%d days)", start_date.date(), end_date.date(), args.days)
    logger.info("Session: %s", session_path)

    # Load symbol matcher
    aliases = load_symbol_aliases(database_url)
    if not aliases:
        logger.error("FATAL: symbol_aliases table is empty. Run populate_symbol_aliases.py first.")
        sys.exit(1)
    matcher = SymbolMatcher(aliases)
    logger.info("Loaded %d symbol aliases (%d unambiguous, %d ambiguous tickers)",
                len(aliases), len(matcher.unambiguous_tickers), len(matcher.ambiguous_tickers))

    # Load channels
    channels = load_channels()
    if args.channels:
        filter_set = set(args.channels)
        channels = [c for c in channels if c["username"] in filter_set]
        logger.info("Filtered to %d channels: %s", len(channels),
                     [c["username"] for c in channels])

    # Connect to Telegram
    session_file = session_path + ".session"
    if not os.path.exists(session_file):
        logger.error(
            "Session file not found at %s. You must do first-auth interactively:\n"
            "  docker compose -f docker-compose.yml run -it social_collector python -c \"\n"
            "    from telethon.sync import TelegramClient\n"
            "    c = TelegramClient('/app/sessions/mcas', %s, '%s')\n"
            "    c.start()\n"
            "    print('Auth complete')\n"
            "    c.disconnect()\n"
            "  \"",
            session_file, api_id, api_hash
        )
        sys.exit(1)

    client = TelegramClient(session_path, int(api_id), api_hash)
    await client.start()
    me = await client.get_me()
    logger.info("Authenticated as: %s (%s)", me.first_name, me.phone)

    # Backfill each channel
    total_stats = {"fetched": 0, "matched": 0, "inserted": 0, "errors": 0}
    t0 = time.time()
    failed_channels = []

    for i, ch in enumerate(channels):
        username = ch["username"]
        category = ch.get("category", "unknown")
        priority = ch.get("priority", "medium")

        logger.info("")
        logger.info("[%d/%d] @%s (category=%s, priority=%s)",
                     i + 1, len(channels), username, category, priority)

        stats = await backfill_channel(client, username, category,
                                        start_date, matcher, database_url)

        for k in total_stats:
            total_stats[k] += stats.get(k, 0)

        if stats["errors"]:
            failed_channels.append(username)

        logger.info("  @%s done: fetched=%d matched=%d inserted=%d",
                     username, stats["fetched"], stats["matched"], stats["inserted"])

        elapsed = time.time() - t0
        channels_done = i + 1
        channels_remaining = len(channels) - channels_done
        if channels_done > 0 and channels_remaining > 0:
            avg_per_channel = elapsed / channels_done
            eta_min = (avg_per_channel * channels_remaining) / 60
            logger.info("  ETA: ~%.0f min remaining (%d channels left)", eta_min, channels_remaining)

    await client.disconnect()

    # ── Summary ──
    elapsed = time.time() - t0
    logger.info("")
    logger.info("=" * 70)
    logger.info("TELEGRAM BACKFILL SUMMARY")
    logger.info("=" * 70)
    logger.info("Channels processed: %d / %d", len(channels) - len(failed_channels), len(channels))
    logger.info("Total fetched:      %d", total_stats["fetched"])
    logger.info("Total matched:      %d (with symbol)", total_stats["matched"])
    logger.info("Total inserted:     %d", total_stats["inserted"])
    logger.info("Elapsed:            %.1f minutes", elapsed / 60)

    if failed_channels:
        logger.warning("Failed channels: %s", failed_channels)

    # ── Verification ──
    logger.info("")
    logger.info("Verification:")
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source, count(*), min(post_ts)::date, max(post_ts)::date
                FROM social_posts
                WHERE source = 'telegram_historical'
                GROUP BY source
            """)
            for row in cur:
                logger.info("  %s: %d posts (%s to %s)", row[0], row[1], row[2], row[3])

            cur.execute("""
                SELECT channel, count(*), min(post_ts)::date, max(post_ts)::date
                FROM social_posts
                WHERE source = 'telegram_historical'
                GROUP BY channel
                ORDER BY count DESC
            """)
            logger.info("")
            logger.info("Posts per channel:")
            for channel, count, earliest, latest in cur:
                logger.info("  %-35s %6d posts  (%s to %s)", channel, count, earliest, latest)

            cur.execute("""
                SELECT unnest(symbols) as sym, count(*)
                FROM social_posts
                WHERE source = 'telegram_historical'
                GROUP BY sym
                ORDER BY count DESC
                LIMIT 20
            """)
            logger.info("")
            logger.info("Top 20 mentioned symbols (Telegram):")
            for sym, count in cur:
                logger.info("  %-12s %d mentions", sym, count)

    logger.info("")
    logger.info("TELEGRAM BACKFILL COMPLETE in %.1f minutes", elapsed / 60)
    logger.info("")
    logger.info("Next step: re-materialize social features to include Telegram data:")
    logger.info("  docker compose -f docker-compose.yml run --rm -T worker \\")
    logger.info("    python3 /scripts/backfill_social_posts.py --days %d --source all --no-materialize", args.days)
    logger.info("  (or run materialization separately)")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Telegram Historical Backfill")
    parser.add_argument("--days", type=int, default=365,
                        help="Number of days to backfill (default: 365)")
    parser.add_argument("--channels", nargs="+", default=None,
                        help="Specific channel usernames to backfill (default: all)")
    parser.add_argument("--api-id", default=None,
                        help="Telegram API ID (default: TELEGRAM_API_ID env)")
    parser.add_argument("--api-hash", default=None,
                        help="Telegram API Hash (default: TELEGRAM_API_HASH env)")
    parser.add_argument("--session", default=None,
                        help="Session file path (default: TELEGRAM_SESSION_PATH env or /app/sessions/mcas)")
    parser.add_argument("--db-url", default=None,
                        help="PostgreSQL URL (default: DATABASE_URL env)")
    args = parser.parse_args()

    asyncio.run(run_backfill(args))


if __name__ == "__main__":
    main()
