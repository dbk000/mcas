"""
Social Collector — Main entrypoint.

Orchestrates all subsystems:
  - Reddit collector (15-min poll cycle)
  - RSS collector (5-min poll cycle)
  - CoinGecko trending collector (30-min poll cycle)
  - Telegram listener (event-driven, if configured)
  - Brain 1 hourly aggregation (at :05 past each hour)
  - Brain 2 anomaly detection + Haiku extraction (at :10 past each hour)
  - Health check loop (every 5 min)
"""

import time
import threading
import logging
import sys
from datetime import datetime, timezone, timedelta

import config
from db import load_symbol_aliases
from symbol_matcher import SymbolMatcher
from collectors.reddit import RedditCollector
from collectors.rss import RssCollector
from collectors.coingecko import CoinGeckoCollector
from collectors.telegram import TelegramCollector
from brain1 import aggregate_hour
from brain2 import run_brain2_cycle

logger = logging.getLogger("collector")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


def collector_loop(collector, interval_s, name):
    """Generic poll loop for a collector."""
    # Initial delay to stagger startup
    time.sleep(2)
    while True:
        try:
            n = collector.collect_cycle()
            logger.info("%s: collected %d posts", name, n)
        except Exception as e:
            logger.error("%s error: %s", name, e, exc_info=True)
        time.sleep(interval_s)


def brain_scheduler():
    """Runs Brain 1 at :05 and Brain 2 at :10 past each hour."""
    while True:
        now = datetime.now(timezone.utc)

        # Calculate next :05 mark
        current_hour = now.replace(minute=0, second=0, microsecond=0)
        brain1_time = current_hour + timedelta(minutes=5)

        # If we're past :05 this hour, target next hour
        if now >= brain1_time:
            brain1_time += timedelta(hours=1)

        wait_s = (brain1_time - now).total_seconds()
        logger.info("Brain scheduler: waiting %.0f seconds until %s",
                     wait_s, brain1_time.isoformat())

        if wait_s > 0:
            time.sleep(wait_s)

        # Brain 1: aggregate the hour that just ended
        hour_start = brain1_time.replace(minute=0) - timedelta(hours=1)
        try:
            n = aggregate_hour(hour_start)
            logger.info("Brain 1: aggregated %d symbols for %s", n, hour_start)
        except Exception as e:
            logger.error("Brain 1 error: %s", e, exc_info=True)

        # Wait 5 more minutes for Brain 2
        time.sleep(300)

        # Brain 2
        try:
            n = run_brain2_cycle(hour_start)
            logger.info("Brain 2: processed %d triggers for %s", n, hour_start)
        except Exception as e:
            logger.error("Brain 2 error: %s", e, exc_info=True)


def main():
    setup_logging()
    logger.info("=== Social Collector starting ===")

    # Load symbol aliases
    logger.info("Loading symbol aliases...")
    aliases = load_symbol_aliases()
    logger.info("Loaded %d aliases", len(aliases))
    matcher = SymbolMatcher(aliases)
    logger.info("SymbolMatcher ready: %d unambiguous tickers, %d ambiguous tickers",
                len(matcher.unambiguous_tickers), len(matcher.ambiguous_tickers))

    threads = []

    # Reddit collector
    if config.REDDIT_CLIENT_ID:
        reddit = RedditCollector(matcher)
        t = threading.Thread(
            target=collector_loop,
            args=(reddit, config.REDDIT_POLL_INTERVAL_S, "Reddit"),
            daemon=True, name="reddit-collector",
        )
        threads.append(t)
        logger.info("Reddit collector enabled (interval %ds)", config.REDDIT_POLL_INTERVAL_S)
    else:
        logger.info("Reddit collector disabled (no REDDIT_CLIENT_ID)")

    # RSS collector
    rss = RssCollector(matcher)
    t = threading.Thread(
        target=collector_loop,
        args=(rss, config.RSS_POLL_INTERVAL_S, "RSS"),
        daemon=True, name="rss-collector",
    )
    threads.append(t)
    logger.info("RSS collector enabled (interval %ds)", config.RSS_POLL_INTERVAL_S)

    # CoinGecko collector
    cg = CoinGeckoCollector(matcher)
    t = threading.Thread(
        target=collector_loop,
        args=(cg, config.COINGECKO_POLL_INTERVAL_S, "CoinGecko"),
        daemon=True, name="coingecko-collector",
    )
    threads.append(t)
    logger.info("CoinGecko collector enabled (interval %ds)", config.COINGECKO_POLL_INTERVAL_S)

    # Telegram collector (event-driven, not polled)
    telegram = TelegramCollector(matcher)
    telegram.start()  # Logs its own status

    # Brain scheduler (Brain 1 + Brain 2)
    t = threading.Thread(target=brain_scheduler, daemon=True, name="brain-scheduler")
    threads.append(t)
    logger.info("Brain scheduler enabled")

    # Start all threads
    for t in threads:
        t.start()
    logger.info("Started %d threads", len(threads))

    # Health check loop
    while True:
        time.sleep(300)
        alive = sum(1 for t in threads if t.is_alive())
        logger.info("Health: %d/%d threads alive", alive, len(threads))

        # Log per-source post counts from DB
        try:
            from db import get_connection
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT source, count(*)
                        FROM social_posts
                        WHERE ingested_ts > NOW() - INTERVAL '1 hour'
                        GROUP BY source
                    """)
                    counts = {row[0]: row[1] for row in cur}
            if counts:
                parts = [f"{src}={cnt}" for src, cnt in sorted(counts.items())]
                logger.info("Posts last 1h: %s", ", ".join(parts))
        except Exception:
            pass


if __name__ == "__main__":
    main()
