import praw
import logging
from datetime import datetime, timezone

import config
from db import batch_insert_posts
from symbol_matcher import SymbolMatcher

logger = logging.getLogger("reddit")


class RedditCollector:
    def __init__(self, matcher: SymbolMatcher):
        self.matcher = matcher
        self.reddit = None
        self.seen_ids = set()
        self._init_client()

    def _init_client(self):
        if not config.REDDIT_CLIENT_ID:
            logger.warning("Reddit credentials not configured, collector disabled")
            return
        self.reddit = praw.Reddit(
            client_id=config.REDDIT_CLIENT_ID,
            client_secret=config.REDDIT_CLIENT_SECRET,
            username=config.REDDIT_USERNAME,
            password=config.REDDIT_PASSWORD,
            user_agent=config.REDDIT_USER_AGENT,
        )
        logger.info("Reddit client initialised")

    def collect_cycle(self) -> int:
        """One collection cycle. Returns number of posts ingested."""
        if not self.reddit:
            return 0

        batch_id = f"reddit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        posts = []

        for sub_name in config.REDDIT_SUBREDDITS:
            try:
                subreddit = self.reddit.subreddit(sub_name)
                for submission in subreddit.new(limit=50):
                    if submission.id in self.seen_ids:
                        continue
                    self.seen_ids.add(submission.id)

                    text = (submission.title or "") + " " + (submission.selftext or "")
                    symbols = self.matcher.extract_symbols(text)

                    if not symbols:
                        continue

                    posts.append({
                        "source": "reddit",
                        "source_id": submission.id,
                        "channel": sub_name,
                        "author": str(submission.author) if submission.author else None,
                        "title": (submission.title or "")[:500],
                        "content": (submission.selftext or "")[:config.MAX_CONTENT_LENGTH],
                        "post_ts": datetime.fromtimestamp(submission.created_utc, tz=timezone.utc),
                        "engagement_score": submission.score,
                        "symbols": symbols,
                        "batch_id": batch_id,
                    })
            except Exception as e:
                logger.error("Reddit error on r/%s: %s", sub_name, e)
                continue

        if posts:
            n = batch_insert_posts(posts)
            logger.info("Reddit: ingested %d posts with symbol matches from %d subs",
                        n, len(config.REDDIT_SUBREDDITS))

        # Trim seen_ids to prevent unbounded growth (keep last 25K)
        if len(self.seen_ids) > 50000:
            self.seen_ids = set(list(self.seen_ids)[-25000:])

        return len(posts)
