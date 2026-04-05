"""
Subscription Tier Dependency
=============================

Provides a FastAPI dependency `get_subscription_tier` that returns
'paid', 'free_subscribed', or 'free_unsubscribed' based on session
state and Stripe subscription status.

Tier is cached in Redis for 60 seconds per API key hash.
"""

import hashlib
import json
import logging
import time

from fastapi import Request, HTTPException

from api.app.db_pool import get_conn as _pool_get_conn
from api.app.routers.session import SESSION_COOKIE_NAME, has_valid_session
from governance.bootstrap import get_config

logger = logging.getLogger(__name__)

TIER_CACHE_TTL_S = 60  # Redis cache TTL for subscription tier


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _get_redis():
    """Get Redis client from governance bootstrap."""
    import redis as redis_lib
    cfg = get_config().Worker
    return redis_lib.Redis.from_url(cfg.REDIS_URL, decode_responses=True)


def get_subscription_tier(request: Request) -> str:
    """
    Determine the subscription tier for the current request.

    Returns:
        'paid' — active or past_due subscription
        'free_subscribed' — valid session but no active subscription
        'free_unsubscribed' — no valid session
    """
    # Check session
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not has_valid_session(token):
        return "free_unsubscribed"

    api_key = get_config().Worker.API_KEY
    if not api_key:
        return "free_subscribed"

    api_key_hash = _hash_api_key(api_key)

    # Check Redis cache first
    try:
        r = _get_redis()
        cached = r.get(f"sub_tier:{api_key_hash}")
        if cached:
            return cached
    except Exception:
        logger.debug("Redis cache miss for subscription tier")

    # Query DB
    tier = "free_subscribed"
    try:
        with _pool_get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s.status
                       FROM customers c
                       JOIN subscriptions s ON s.customer_id = c.id
                       WHERE c.api_key_hash = %s
                         AND s.status IN ('active', 'past_due')
                       LIMIT 1""",
                    (api_key_hash,),
                )
                row = cur.fetchone()
                if row:
                    tier = "paid"
    except Exception:
        logger.exception("Failed to query subscription tier")

    # Cache in Redis
    try:
        r = _get_redis()
        r.setex(f"sub_tier:{api_key_hash}", TIER_CACHE_TTL_S, tier)
    except Exception:
        pass

    return tier


def require_paid(request: Request):
    """FastAPI dependency that requires paid subscription. Raises 403 if not paid."""
    tier = get_subscription_tier(request)
    if tier != "paid":
        raise HTTPException(
            status_code=403,
            detail={
                "error": "subscription_required",
                "tier": tier,
                "message": "This feature requires an active subscription.",
            },
        )
    return tier


def require_subscribed(request: Request):
    """FastAPI dependency that requires at least a session (free_subscribed or paid)."""
    tier = get_subscription_tier(request)
    if tier == "free_unsubscribed":
        raise HTTPException(status_code=401, detail="Unauthorized")
    return tier
