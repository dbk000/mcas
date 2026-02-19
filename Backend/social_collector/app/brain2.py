"""
Brain 2 — Anomaly detection + Haiku extraction.

Runs at :10 past each hour. Checks freshly-written social_features_1h rows
for trigger conditions. For each triggered symbol, fetches recent posts and
calls Claude Haiku for structured signal extraction.

Cost controls:
  - Max 100 Haiku calls per hour (hard cap)
  - 4-hour dedup per symbol+trigger_type
  - Circuit breaker: 5 consecutive API errors → 30 min backoff
  - Daily cost tracking with alert threshold
"""

import json
import time
import logging
from datetime import datetime, timedelta, timezone

import config
from db import get_connection, insert_social_signal

logger = logging.getLogger("brain2")

# Module-level state for circuit breaker and cost tracking
_consecutive_errors = 0
_backoff_until = 0.0
_hourly_call_count = 0
_hourly_reset_ts = 0.0
_daily_input_tokens = 0
_daily_output_tokens = 0
_daily_reset_date = None

# Haiku pricing (per 1M tokens)
HAIKU_INPUT_PRICE_PER_M = 0.80
HAIKU_OUTPUT_PRICE_PER_M = 4.00

EXTRACTION_PROMPT = """You are analysing social media posts about a cryptocurrency token to extract structured signals for a trading research system.

Token: {symbol}
Trigger: {trigger_type} at {trigger_ts}

Posts (ordered by engagement, most recent first):
---
{formatted_posts}
---

Extract the following as JSON. Use null for any field you cannot determine with reasonable confidence.

{{
  "catalyst_detected": bool,
  "catalyst_type": "exchange_listing_rumor|partnership|upgrade|airdrop|influencer|whale|none",
  "catalyst_timeline": "imminent_24h|near_term_7d|speculative|none",
  "catalyst_specificity": float 0.0-1.0,
  "catalyst_verifiability": float 0.0-1.0,
  "narrative_category": "AI|RWA|meme|DeFi|gaming|L2|privacy|none",
  "narrative_novelty": float 0.0-1.0,
  "cascade_stage": "emergence|acceleration|amplification|saturation|decay",
  "discussion_quality": float 0.0-1.0,
  "author_credibility": float 0.0-1.0,
  "source_diversity": float 0.0-1.0,
  "coordination_probability": float 0.0-1.0,
  "controversy_level": float 0.0-1.0,
  "confidence": float 0.0-1.0,
  "raw_summary": "One sentence: what is the internet saying about this token right now?"
}}

Respond with ONLY the JSON object. No preamble, no markdown fences."""


def run_brain2_cycle(hour_start: datetime) -> int:
    """Run Brain 2 for a given hour. Returns number of signals produced."""
    global _hourly_call_count, _hourly_reset_ts

    # Reset hourly counter if needed
    now = time.time()
    if now - _hourly_reset_ts > 3600:
        _hourly_call_count = 0
        _hourly_reset_ts = now

    # Check circuit breaker
    if _is_circuit_open():
        logger.warning("Brain 2: circuit breaker open, skipping cycle")
        return 0

    # Check for API key
    if not config.ANTHROPIC_API_KEY:
        logger.info("Brain 2: ANTHROPIC_API_KEY not configured, skipping")
        return 0

    triggers = find_triggers(hour_start)
    if not triggers:
        logger.info("Brain 2: no triggers for %s", hour_start)
        return 0

    # Sort by z-score magnitude (highest first) for priority
    triggers.sort(key=lambda t: t.get("mention_count", 0), reverse=True)

    signals_produced = 0
    for trigger in triggers:
        if _hourly_call_count >= config.BRAIN2_MAX_CALLS_PER_HOUR:
            logger.warning("Brain 2: hourly call limit reached (%d), stopping",
                           config.BRAIN2_MAX_CALLS_PER_HOUR)
            break

        if _is_circuit_open():
            logger.warning("Brain 2: circuit breaker tripped mid-cycle")
            break

        symbol = trigger["symbol"]
        for trigger_type in trigger["trigger_types"]:
            # Dedup check
            if _already_triggered(symbol, trigger_type, hour_start):
                continue

            # Fetch posts for this symbol
            posts = _fetch_recent_posts(symbol, hour_start)
            if not posts:
                continue

            # Call Haiku
            result = _call_haiku(symbol, trigger_type, hour_start, posts)
            if result:
                signals_produced += 1

    return signals_produced


def find_triggers(hour_start: datetime) -> list[dict]:
    """Check social_features_1h for anomalies. Returns list of trigger dicts."""
    triggers = []

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT symbol, mention_count, unique_authors, platform_count,
                       mention_velocity, author_novelty_ratio, first_mention
                FROM social_features_1h
                WHERE bar_start_ts = %s
            """, (hour_start,))

            for row in cur:
                symbol, count, authors, platforms, velocity, novelty, first = row
                symbol_triggers = []

                # 1. Volume z-score spike
                if count and count > 0:
                    cur.execute("""
                        SELECT avg(mention_count), stddev(mention_count)
                        FROM social_features_1h
                        WHERE symbol = %s
                          AND bar_start_ts >= %s
                          AND bar_start_ts < %s
                    """, (symbol, hour_start - timedelta(hours=24), hour_start))
                    stats = cur.fetchone()
                    if stats and stats[0] is not None and stats[1] is not None and stats[1] > 0:
                        z = (count - stats[0]) / stats[1]
                        if z > 3.0:
                            symbol_triggers.append("volume_z")

                # 2. First mention
                if first:
                    symbol_triggers.append("first_mention")

                # 3. Author velocity
                if novelty is not None and novelty > 0.7 and authors is not None and authors >= 3:
                    symbol_triggers.append("author_velocity")

                # 4. Cross-platform
                if platforms is not None and platforms >= 2 and count is not None and count >= 5:
                    symbol_triggers.append("cross_platform")

                if symbol_triggers:
                    triggers.append({
                        "symbol": symbol,
                        "trigger_types": symbol_triggers,
                        "mention_count": count,
                    })

    return triggers


def _already_triggered(symbol: str, trigger_type: str, hour_start: datetime) -> bool:
    """Check if same symbol+trigger_type fired within dedup window."""
    dedup_cutoff = hour_start - timedelta(hours=config.BRAIN2_DEDUP_HOURS)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM social_signals
                WHERE symbol = %s AND trigger_type = %s AND trigger_ts > %s
                LIMIT 1
            """, (symbol, trigger_type, dedup_cutoff))
            return cur.fetchone() is not None


def _fetch_recent_posts(symbol: str, hour_start: datetime) -> list[dict]:
    """Fetch 15-20 most recent posts for symbol from past 4 hours."""
    cutoff = hour_start - timedelta(hours=4)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT source, channel, author, title, content, post_ts,
                       engagement_score
                FROM social_posts
                WHERE %s = ANY(symbols)
                  AND post_ts >= %s
                  AND post_ts <= %s + INTERVAL '1 hour'
                ORDER BY engagement_score DESC, post_ts DESC
                LIMIT 20
            """, (symbol, cutoff, hour_start))

            posts = []
            for row in cur:
                posts.append({
                    "source": row[0],
                    "channel": row[1],
                    "author": row[2],
                    "title": row[3],
                    "content": row[4],
                    "post_ts": row[5].isoformat() if row[5] else None,
                    "engagement_score": row[6],
                })
    return posts


def _format_posts(posts: list[dict]) -> str:
    """Format posts for the Haiku prompt."""
    lines = []
    for i, p in enumerate(posts, 1):
        parts = [f"[{i}] [{p['source']}:{p.get('channel', '')}]"]
        if p.get("author"):
            parts.append(f"by {p['author']}")
        parts.append(f"at {p.get('post_ts', '?')}")
        parts.append(f"(engagement: {p.get('engagement_score', 0)})")
        lines.append(" ".join(parts))
        if p.get("title"):
            lines.append(f"  Title: {p['title']}")
        if p.get("content"):
            lines.append(f"  {p['content'][:500]}")
        lines.append("")
    return "\n".join(lines)


def _call_haiku(symbol: str, trigger_type: str, hour_start: datetime,
                posts: list[dict]) -> dict | None:
    """Call Claude Haiku for signal extraction. Returns parsed signal dict or None."""
    global _consecutive_errors, _backoff_until, _hourly_call_count
    global _daily_input_tokens, _daily_output_tokens, _daily_reset_date

    import anthropic

    # Daily cost tracking reset
    today = datetime.now(timezone.utc).date()
    if _daily_reset_date != today:
        _daily_input_tokens = 0
        _daily_output_tokens = 0
        _daily_reset_date = today

    formatted = _format_posts(posts)
    prompt = EXTRACTION_PROMPT.format(
        symbol=symbol,
        trigger_type=trigger_type,
        trigger_ts=hour_start.isoformat(),
        formatted_posts=formatted,
    )

    try:
        client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=config.BRAIN2_MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        _consecutive_errors = 0
        _hourly_call_count += 1

        # Track tokens
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        _daily_input_tokens += input_tokens
        _daily_output_tokens += output_tokens

        # Daily cost check
        daily_cost = (
            (_daily_input_tokens / 1_000_000) * HAIKU_INPUT_PRICE_PER_M +
            (_daily_output_tokens / 1_000_000) * HAIKU_OUTPUT_PRICE_PER_M
        )
        if daily_cost > config.BRAIN2_DAILY_COST_ALERT_USD:
            logger.warning("Brain 2: daily cost $%.2f exceeds alert threshold $%.2f",
                           daily_cost, config.BRAIN2_DAILY_COST_ALERT_USD)

        # Parse response
        raw_text = response.content[0].text.strip()
        try:
            extracted = json.loads(raw_text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown fences
            if "```" in raw_text:
                json_part = raw_text.split("```")[1]
                if json_part.startswith("json"):
                    json_part = json_part[4:]
                extracted = json.loads(json_part.strip())
            else:
                logger.error("Brain 2: malformed JSON response for %s: %s",
                             symbol, raw_text[:200])
                return None

        # Build signal dict
        signal = {
            "symbol": symbol,
            "trigger_ts": hour_start,
            "trigger_type": trigger_type,
            "catalyst_detected": extracted.get("catalyst_detected"),
            "catalyst_type": extracted.get("catalyst_type"),
            "catalyst_timeline": extracted.get("catalyst_timeline"),
            "catalyst_specificity": extracted.get("catalyst_specificity"),
            "catalyst_verifiability": extracted.get("catalyst_verifiability"),
            "narrative_category": extracted.get("narrative_category"),
            "narrative_novelty": extracted.get("narrative_novelty"),
            "cascade_stage": extracted.get("cascade_stage"),
            "discussion_quality": extracted.get("discussion_quality"),
            "author_credibility": extracted.get("author_credibility"),
            "source_diversity": extracted.get("source_diversity"),
            "coordination_probability": extracted.get("coordination_probability"),
            "controversy_level": extracted.get("controversy_level"),
            "confidence": extracted.get("confidence"),
            "raw_summary": extracted.get("raw_summary"),
            "model_used": config.BRAIN2_MODEL,
            "input_token_count": input_tokens,
            "output_token_count": output_tokens,
            "input_posts": json.dumps(posts[:10]),  # Store first 10 posts as provenance
        }

        sig_id = insert_social_signal(signal)
        logger.info("Brain 2: signal %d for %s trigger=%s catalyst=%s cascade=%s conf=%.2f",
                     sig_id, symbol, trigger_type,
                     extracted.get("catalyst_type", "?"),
                     extracted.get("cascade_stage", "?"),
                     extracted.get("confidence", 0))
        return signal

    except Exception as e:
        _consecutive_errors += 1
        logger.error("Brain 2: Haiku API error for %s (%d consecutive): %s",
                     symbol, _consecutive_errors, e)

        if _consecutive_errors >= config.BRAIN2_CIRCUIT_BREAKER_FAILURES:
            _backoff_until = time.time() + config.BRAIN2_CIRCUIT_BREAKER_BACKOFF_S
            logger.warning("Brain 2: circuit breaker OPEN, backing off %d seconds",
                           config.BRAIN2_CIRCUIT_BREAKER_BACKOFF_S)
        return None


def _is_circuit_open() -> bool:
    """Check if circuit breaker is open."""
    if _backoff_until > 0 and time.time() < _backoff_until:
        return True
    return False
