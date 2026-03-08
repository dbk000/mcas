#!/usr/bin/env python3
"""
Workstream 1: Know Your Bots — Source Census & Template Detection

Produces a full census of every (source, channel) pair in social_posts,
with random sample posts and structural template detection to classify
each source as bot_templated / bot_semi / human_mixed / human_organic.

Output: scripts/output/social_source_census_{ts}.json

Usage:
    docker compose -f docker-compose.yml run --rm -T worker \
        python3 /scripts/social_source_census.py
"""

import json
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

import psycopg

# ---------------------------------------------------------------------------
# Path detection (Docker vs host)
# ---------------------------------------------------------------------------
if os.path.isdir("/app"):
    sys.path.insert(0, "/app")
    sys.path.insert(0, "/scripts")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://trading:trading@postgres:5432/trading",
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
if os.path.isdir("/scripts/output"):
    OUTPUT_DIR = "/scripts/output"

# ---------------------------------------------------------------------------
# Skeleton extraction for template detection
# ---------------------------------------------------------------------------

# Matches numbers with optional decimals, commas, percentage signs, +/- prefix
NUM_RE = re.compile(r"[+-]?\d[\d,]*\.?\d*%?")

# Matches uppercase ticker-like strings (3-10 chars), optionally preceded by $
TICKER_RE = re.compile(r"\$?[A-Z][A-Z0-9]{2,9}")

# Matches URLs
URL_RE = re.compile(r"https?://\S+")

# Matches emoji sequences
EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"  # misc symbols & pictographs
    "\U00002702-\U000027B0"  # dingbats
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "\U000025A0-\U000025FF"  # geometric shapes
    "\U00002600-\U000026FF"  # misc symbols
    "]+",
    flags=re.UNICODE,
)


def extract_skeleton(text: str) -> str:
    """
    Replace variable parts of a post with placeholders to reveal template structure.
    Numbers → {NUM}, tickers → {TICKER}, URLs → {URL}, emoji → {E}
    """
    if not text:
        return ""
    s = URL_RE.sub("{URL}", text)
    s = TICKER_RE.sub("{TICKER}", s)
    s = NUM_RE.sub("{NUM}", s)
    s = EMOJI_RE.sub("{E}", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s


def classify_source(unique_skeletons: int) -> str:
    """Classify based on template diversity."""
    if unique_skeletons < 5:
        return "bot_templated"
    elif unique_skeletons < 20:
        return "bot_semi"
    elif unique_skeletons < 50:
        return "human_mixed"
    else:
        return "human_organic"


def main():
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(OUTPUT_DIR, f"social_source_census_{ts}.json")
    logger.info("Output will be written to: %s", output_path)

    logger.info("Connecting to database...")
    with psycopg.connect(DATABASE_URL) as conn:
        # -----------------------------------------------------------------
        # Step 1: Full census of (source, channel) pairs
        # -----------------------------------------------------------------
        logger.info("Step 1: Running source/channel census...")
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source, channel,
                       COUNT(*) as total_posts,
                       MIN(post_ts) as earliest,
                       MAX(post_ts) as latest,
                       ROUND(AVG(engagement_score), 1) as avg_engagement
                FROM social_posts
                GROUP BY source, channel
                ORDER BY COUNT(*) DESC
            """)
            census_rows = cur.fetchall()

            # Separate query for unique symbols (unnest would inflate counts)
            cur.execute("""
                SELECT source, channel, COUNT(DISTINCT sym) as unique_symbols
                FROM social_posts, unnest(symbols) AS sym
                GROUP BY source, channel
            """)
            sym_counts = {(r[0], r[1]): r[2] for r in cur.fetchall()}

        logger.info("Found %d source/channel pairs", len(census_rows))

        census = []
        for row in census_rows:
            source, channel, total, earliest, latest, avg_eng = row
            census.append({
                "source": source,
                "channel": channel,
                "total_posts": total,
                "unique_symbols": sym_counts.get((source, channel), 0),
                "earliest": earliest.isoformat() if earliest else None,
                "latest": latest.isoformat() if latest else None,
                "avg_engagement": float(avg_eng) if avg_eng is not None else 0.0,
            })

        # -----------------------------------------------------------------
        # Step 2: Pull 5 random samples per source/channel
        # -----------------------------------------------------------------
        logger.info("Step 2: Pulling 5 random samples per source/channel...")
        for i, entry in enumerate(census):
            src, ch = entry["source"], entry["channel"]
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT source, channel,
                           COALESCE(title, '') as title,
                           COALESCE(content, '') as content,
                           engagement_score, post_ts, symbols
                    FROM social_posts
                    WHERE source = %s AND channel = %s
                    ORDER BY random()
                    LIMIT 5
                """, (src, ch))
                samples = []
                for srow in cur.fetchall():
                    samples.append({
                        "title": srow[2][:500],
                        "content": srow[3][:500],
                        "engagement_score": srow[4],
                        "post_ts": srow[5].isoformat() if srow[5] else None,
                        "symbols": srow[6] or [],
                    })
            entry["samples"] = samples

            if (i + 1) % 20 == 0:
                logger.info("  Sampled %d/%d pairs", i + 1, len(census))

        # -----------------------------------------------------------------
        # Step 3: Template detection — 100 random posts per source/channel
        # -----------------------------------------------------------------
        logger.info("Step 3: Template detection (100 posts per source/channel)...")
        for i, entry in enumerate(census):
            src, ch = entry["source"], entry["channel"]
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT COALESCE(content, '') || ' ' || COALESCE(title, '')
                    FROM social_posts
                    WHERE source = %s AND channel = %s
                    ORDER BY random()
                    LIMIT 100
                """, (src, ch))
                posts = [row[0] for row in cur.fetchall()]

            skeletons = [extract_skeleton(p) for p in posts]
            unique = set(skeletons)
            n_unique = len(unique)
            skeleton_counts = Counter(skeletons)
            top_skeleton = skeleton_counts.most_common(1)[0] if skeleton_counts else ("", 0)

            classification = classify_source(n_unique)

            entry["n_posts_sampled"] = len(posts)
            entry["unique_skeletons"] = n_unique
            entry["classification"] = classification
            entry["top_skeleton"] = top_skeleton[0][:300]
            entry["top_skeleton_count"] = top_skeleton[1]

            if (i + 1) % 20 == 0:
                logger.info("  Analyzed %d/%d pairs", i + 1, len(census))

        # -----------------------------------------------------------------
        # Step 4: Summary statistics
        # -----------------------------------------------------------------
        class_counts = Counter(e["classification"] for e in census)
        post_counts_by_class = {}
        for e in census:
            cls = e["classification"]
            post_counts_by_class[cls] = post_counts_by_class.get(cls, 0) + e["total_posts"]

        summary = {
            "total_pairs": len(census),
            "total_posts": sum(e["total_posts"] for e in census),
            "classification_counts": dict(class_counts),
            "posts_by_classification": post_counts_by_class,
        }

        logger.info("Classification summary:")
        for cls, count in sorted(class_counts.items()):
            posts = post_counts_by_class[cls]
            logger.info("  %-16s: %3d channels, %7d posts (%.1f%%)",
                        cls, count, posts, 100.0 * posts / summary["total_posts"])

        # -----------------------------------------------------------------
        # Write output
        # -----------------------------------------------------------------
        output = {
            "metadata": {
                "generated_at": ts,
                "description": "Workstream 1: Social Source Census & Template Detection",
            },
            "summary": summary,
            "census": census,
        }

        with open(output_path, "w") as f:
            json.dump(output, f, indent=2, default=str)
        logger.info("Output written to: %s", output_path)

        # Also print a readable summary to stdout
        print("\n" + "=" * 80)
        print("SOCIAL SOURCE CENSUS — Workstream 1")
        print("=" * 80)
        print(f"\nTotal source/channel pairs: {len(census)}")
        print(f"Total posts: {summary['total_posts']:,}")
        print(f"\nClassification breakdown:")
        for cls in ["bot_templated", "bot_semi", "human_mixed", "human_organic"]:
            n = class_counts.get(cls, 0)
            p = post_counts_by_class.get(cls, 0)
            pct = 100.0 * p / summary["total_posts"] if summary["total_posts"] else 0
            print(f"  {cls:<16}: {n:3d} channels, {p:7,d} posts ({pct:5.1f}%)")

        print(f"\nTop 20 by volume:")
        print(f"{'Source':<22} {'Channel':<20} {'Posts':>8} {'Class':<16} {'Skeletons':>9}")
        print("-" * 80)
        for e in census[:20]:
            print(f"{e['source']:<22} {e['channel']:<20} {e['total_posts']:>8,} "
                  f"{e['classification']:<16} {e['unique_skeletons']:>9}")

        print(f"\nFull output: {output_path}")


if __name__ == "__main__":
    main()
