import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

import config
from db import batch_insert_posts
from symbol_matcher import SymbolMatcher

logger = logging.getLogger("rss")


class RssCollector:
    def __init__(self, matcher: SymbolMatcher):
        self.matcher = matcher
        self.seen_ids = set()

    def collect_cycle(self) -> int:
        """One collection cycle across all RSS feeds. Returns number of posts ingested."""
        batch_id = f"rss_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        posts = []

        for feed_cfg in config.RSS_FEEDS:
            feed_name = feed_cfg["name"]
            feed_url = feed_cfg["url"]
            try:
                feed = feedparser.parse(feed_url)
                if feed.bozo and not feed.entries:
                    logger.warning("RSS feed %s returned bozo error: %s",
                                   feed_name, feed.bozo_exception)
                    continue

                for entry in feed.entries[:30]:
                    # Build a stable source_id from entry id or link
                    raw_id = entry.get("id") or entry.get("link") or ""
                    if not raw_id:
                        continue
                    source_id = hashlib.md5(raw_id.encode()).hexdigest()

                    if source_id in self.seen_ids:
                        continue
                    self.seen_ids.add(source_id)

                    title = entry.get("title", "")
                    summary = entry.get("summary", "") or entry.get("description", "")
                    text = title + " " + summary

                    symbols = self.matcher.extract_symbols(text)
                    if not symbols:
                        continue

                    # Parse published date
                    post_ts = datetime.now(timezone.utc)
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        try:
                            post_ts = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                        except Exception:
                            pass
                    elif hasattr(entry, "published") and entry.published:
                        try:
                            post_ts = parsedate_to_datetime(entry.published)
                            if post_ts.tzinfo is None:
                                post_ts = post_ts.replace(tzinfo=timezone.utc)
                        except Exception:
                            pass

                    posts.append({
                        "source": "rss",
                        "source_id": source_id,
                        "channel": feed_name,
                        "author": entry.get("author"),
                        "title": title[:500],
                        "content": summary[:config.MAX_CONTENT_LENGTH],
                        "post_ts": post_ts,
                        "engagement_score": 0,
                        "symbols": symbols,
                        "batch_id": batch_id,
                    })
            except Exception as e:
                logger.error("RSS error on %s: %s", feed_name, e)
                continue

        if posts:
            n = batch_insert_posts(posts)
            logger.info("RSS: ingested %d posts with symbol matches from %d feeds",
                        n, len(config.RSS_FEEDS))

        # Trim seen_ids
        if len(self.seen_ids) > 50000:
            self.seen_ids = set(list(self.seen_ids)[-25000:])

        return len(posts)
