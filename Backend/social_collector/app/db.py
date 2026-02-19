import psycopg
import logging
from config import DATABASE_URL

logger = logging.getLogger("db")


def get_connection():
    return psycopg.connect(DATABASE_URL)


def batch_insert_posts(posts: list[dict]):
    """Insert social_posts batch. posts is list of dicts with keys matching columns.
    Returns count of rows in batch (actual inserts may be fewer due to ON CONFLICT)."""
    if not posts:
        return 0
    sql = """
        INSERT INTO social_posts (source, source_id, channel, author, title, content,
                                  post_ts, engagement_score, symbols, batch_id)
        VALUES (%(source)s, %(source_id)s, %(channel)s, %(author)s, %(title)s, %(content)s,
                %(post_ts)s, %(engagement_score)s, %(symbols)s, %(batch_id)s)
        ON CONFLICT (source, source_id) DO NOTHING
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, posts)
        conn.commit()
    return len(posts)


def upsert_social_features(rows: list[tuple]):
    """Upsert social_features_1h rows.
    Each tuple: (symbol, bar_start_ts, mention_count, unique_authors, platform_count,
                 engagement_sum, mention_velocity, mention_acceleration,
                 author_novelty_ratio, first_mention, mention_volume_ratio)
    """
    if not rows:
        return 0
    sql = """
        INSERT INTO social_features_1h
        (symbol, bar_start_ts, mention_count, unique_authors, platform_count,
         engagement_sum, mention_velocity, mention_acceleration,
         author_novelty_ratio, first_mention, mention_volume_ratio)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (symbol, bar_start_ts) DO UPDATE SET
            mention_count = EXCLUDED.mention_count,
            unique_authors = EXCLUDED.unique_authors,
            platform_count = EXCLUDED.platform_count,
            engagement_sum = EXCLUDED.engagement_sum,
            mention_velocity = EXCLUDED.mention_velocity,
            mention_acceleration = EXCLUDED.mention_acceleration,
            author_novelty_ratio = EXCLUDED.author_novelty_ratio,
            first_mention = EXCLUDED.first_mention,
            mention_volume_ratio = EXCLUDED.mention_volume_ratio,
            computed_at = NOW()
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, rows)
        conn.commit()
    return len(rows)


def insert_social_signal(signal: dict):
    """Insert a single social_signals row. Returns the inserted id."""
    sql = """
        INSERT INTO social_signals
        (symbol, trigger_ts, trigger_type, catalyst_detected, catalyst_type,
         catalyst_timeline, catalyst_specificity, catalyst_verifiability,
         narrative_category, narrative_novelty, cascade_stage,
         discussion_quality, author_credibility, source_diversity,
         coordination_probability, controversy_level, confidence,
         raw_summary, model_used, input_token_count, output_token_count, input_posts)
        VALUES (%(symbol)s, %(trigger_ts)s, %(trigger_type)s, %(catalyst_detected)s,
                %(catalyst_type)s, %(catalyst_timeline)s, %(catalyst_specificity)s,
                %(catalyst_verifiability)s, %(narrative_category)s, %(narrative_novelty)s,
                %(cascade_stage)s, %(discussion_quality)s, %(author_credibility)s,
                %(source_diversity)s, %(coordination_probability)s, %(controversy_level)s,
                %(confidence)s, %(raw_summary)s, %(model_used)s,
                %(input_token_count)s, %(output_token_count)s, %(input_posts)s)
        RETURNING id
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, signal)
            row = cur.fetchone()
        conn.commit()
    return row[0] if row else None


def load_symbol_aliases() -> dict:
    """Load symbol_aliases into memory.
    Returns {lowercase_alias: [(symbol, alias_type, is_ambiguous), ...]}
    """
    aliases = {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT symbol, alias, alias_type, is_ambiguous FROM symbol_aliases")
            for symbol, alias, alias_type, is_ambiguous in cur:
                key = alias.lower()
                if key not in aliases:
                    aliases[key] = []
                aliases[key].append((symbol, alias_type, is_ambiguous))
    return aliases
