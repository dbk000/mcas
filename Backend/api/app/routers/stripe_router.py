"""
Stripe Billing Router
=====================

Handles checkout sessions, webhook events, subscription status,
and billing portal sessions. Stripe keys are external credentials
read via os.getenv() — not governed by TypedConfigRegistry.

Webhook endpoint is unauthenticated (signature-verified by Stripe).
All other endpoints require a valid session.
"""

import hashlib
import logging
import os
import time
from datetime import datetime, timezone

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from api.app.db_pool import get_conn as _pool_get_conn
from api.app.routers.session import (
    SESSION_COOKIE_NAME,
    has_valid_session,
)
from governance.bootstrap import get_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stripe", tags=["stripe"])

# ---------------------------------------------------------------------------
# Stripe configuration (external credentials, not TypedConfigRegistry)
# ---------------------------------------------------------------------------

def _get_stripe_key():
    key = os.getenv("STRIPE_SECRET_KEY", "")
    if not key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    return key


def _get_webhook_secret():
    return os.getenv("STRIPE_WEBHOOK_SECRET", "")


def _get_price_ids():
    return {
        "monthly": os.getenv("STRIPE_PRICE_MONTHLY", ""),
        "annual": os.getenv("STRIPE_PRICE_ANNUAL", ""),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def envelope(data, compute_start: float) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "data": data,
        "meta": {
            "timestamp": now,
            "as_of_ts": now,
            "version": "v1",
            "compute_ms": round((time.time() - compute_start) * 1000, 1),
        },
    }


def _hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _require_session(request: Request):
    """Validate session cookie. Raises 401 if invalid."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not has_valid_session(token):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _get_api_key_hash() -> str:
    """Hash the configured API key to look up the customer record."""
    api_key = get_config().Worker.API_KEY
    if not api_key:
        raise HTTPException(status_code=500, detail="API key not configured")
    return _hash_api_key(api_key)


def _find_or_create_customer(api_key_hash: str) -> dict:
    """Find or create a customers row. Returns {id, stripe_customer_id, email}."""
    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, stripe_customer_id, email FROM customers WHERE api_key_hash = %s",
                (api_key_hash,),
            )
            row = cur.fetchone()
            if row:
                return {"id": row[0], "stripe_customer_id": row[1], "email": row[2]}

            cur.execute(
                """INSERT INTO customers (api_key_hash)
                   VALUES (%s)
                   ON CONFLICT (api_key_hash) DO UPDATE SET updated_at = NOW()
                   RETURNING id, stripe_customer_id, email""",
                (api_key_hash,),
            )
            conn.commit()
            row = cur.fetchone()
            return {"id": row[0], "stripe_customer_id": row[1], "email": row[2]}


def _plan_from_price_id(price_id: str | None) -> str | None:
    prices = _get_price_ids()
    if price_id == prices["monthly"]:
        return "monthly"
    if price_id == prices["annual"]:
        return "annual"
    return "unknown"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class CheckoutRequest(BaseModel):
    price_id: str | None = None
    success_url: str = "https://mcasui.com/checkout/success?session_id={CHECKOUT_SESSION_ID}"
    cancel_url: str = "https://mcasui.com/checkout/cancel"


@router.post("/create-checkout-session")
async def create_checkout_session(payload: CheckoutRequest, request: Request):
    """Create a Stripe Checkout Session for subscription purchase."""
    t0 = time.time()
    _require_session(request)
    stripe.api_key = _get_stripe_key()

    # Resolve price_id: explicit from request, or fall back to monthly env var
    price_id = payload.price_id
    if not price_id:
        price_id = _get_price_ids().get("monthly")
    if not price_id:
        raise HTTPException(status_code=400, detail="No price_id provided and STRIPE_PRICE_MONTHLY not configured")

    api_key_hash = _get_api_key_hash()
    customer = _find_or_create_customer(api_key_hash)

    # Find or create Stripe Customer
    stripe_customer_id = customer["stripe_customer_id"]
    if not stripe_customer_id:
        stripe_cust = stripe.Customer.create(
            metadata={"mcas_customer_id": str(customer["id"])},
        )
        stripe_customer_id = stripe_cust.id

        with _pool_get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE customers SET stripe_customer_id = %s, updated_at = NOW() WHERE id = %s",
                    (stripe_customer_id, customer["id"]),
                )
                conn.commit()

    checkout_session = stripe.checkout.Session.create(
        customer=stripe_customer_id,
        mode="subscription",
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=payload.success_url,
        cancel_url=payload.cancel_url,
    )

    return envelope({"checkout_url": checkout_session.url}, t0)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events. No API key auth — signature-verified."""
    t0 = time.time()
    payload_bytes = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = _get_webhook_secret()

    if not webhook_secret:
        logger.error("STRIPE_WEBHOOK_SECRET not configured")
        return envelope({"received": True}, t0)

    try:
        event = stripe.Webhook.construct_event(
            payload_bytes, sig_header, webhook_secret
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception as e:
        logger.error("Webhook parse error: %s", str(e))
        raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = event["type"]
    data_object = event["data"]["object"]

    try:
        if event_type == "checkout.session.completed":
            _handle_checkout_completed(data_object)
        elif event_type == "customer.subscription.updated":
            _handle_subscription_updated(data_object)
        elif event_type == "customer.subscription.deleted":
            _handle_subscription_deleted(data_object)
        elif event_type == "invoice.payment_succeeded":
            _handle_invoice_payment_succeeded(data_object)
        elif event_type == "invoice.payment_failed":
            _handle_invoice_payment_failed(data_object)
        else:
            logger.info("Unhandled Stripe event: %s", event_type)
    except Exception:
        logger.exception("Error processing Stripe event %s", event_type)

    # Always return 200 to Stripe
    return envelope({"received": True}, t0)


@router.get("/subscription-status")
async def subscription_status(request: Request):
    """Get the current user's subscription status."""
    t0 = time.time()
    _require_session(request)

    api_key_hash = _get_api_key_hash()

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT s.status, s.stripe_price_id, s.current_period_end,
                          s.cancel_at_period_end, c.sim_runs_used, c.sim_runs_reset_at
                   FROM customers c
                   JOIN subscriptions s ON s.customer_id = c.id
                   WHERE c.api_key_hash = %s
                     AND s.status IN ('active', 'past_due')
                   ORDER BY s.current_period_end DESC
                   LIMIT 1""",
                (api_key_hash,),
            )
            row = cur.fetchone()

    if not row:
        return envelope({
            "has_subscription": False,
            "status": "none",
            "plan": None,
            "current_period_end": None,
            "cancel_at_period_end": False,
            "sim_runs_used": 0,
            "sim_runs_limit": 10,
            "sim_runs_reset_at": None,
        }, t0)

    status, price_id, period_end, cancel_at, sim_used, sim_reset = row

    return envelope({
        "has_subscription": True,
        "status": status,
        "plan": _plan_from_price_id(price_id),
        "current_period_end": period_end.isoformat() if period_end else None,
        "cancel_at_period_end": bool(cancel_at),
        "sim_runs_used": sim_used or 0,
        "sim_runs_limit": 10,
        "sim_runs_reset_at": sim_reset.isoformat() if sim_reset else None,
    }, t0)


@router.post("/create-portal-session")
async def create_portal_session(request: Request):
    """Create a Stripe Billing Portal session for subscription management."""
    t0 = time.time()
    _require_session(request)
    stripe.api_key = _get_stripe_key()

    api_key_hash = _get_api_key_hash()

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT stripe_customer_id FROM customers WHERE api_key_hash = %s",
                (api_key_hash,),
            )
            row = cur.fetchone()

    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="No billing account found")

    portal_session = stripe.billing_portal.Session.create(
        customer=row[0],
        return_url="https://mcasui.com/app",
    )

    return envelope({"portal_url": portal_session.url}, t0)


# ---------------------------------------------------------------------------
# Webhook event handlers (idempotent via stripe_subscription_id upsert)
# ---------------------------------------------------------------------------

def _handle_checkout_completed(session_obj):
    """checkout.session.completed — create/update subscription."""
    stripe_customer_id = session_obj.get("customer")
    subscription_id = session_obj.get("subscription")
    customer_email = session_obj.get("customer_details", {}).get("email")

    if not subscription_id:
        logger.info("Checkout completed without subscription (one-time?)")
        return

    stripe.api_key = _get_stripe_key()

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            # Update customer email if we got one
            if customer_email and stripe_customer_id:
                cur.execute(
                    """UPDATE customers SET email = %s, updated_at = NOW()
                       WHERE stripe_customer_id = %s""",
                    (customer_email, stripe_customer_id),
                )

            # Find internal customer_id
            cur.execute(
                "SELECT id FROM customers WHERE stripe_customer_id = %s",
                (stripe_customer_id,),
            )
            cust_row = cur.fetchone()
            if not cust_row:
                logger.warning("No customer found for stripe_customer_id=%s", stripe_customer_id)
                conn.commit()
                return

            customer_id = cust_row[0]

            # Fetch subscription details from Stripe
            sub = stripe.Subscription.retrieve(subscription_id)
            price_id = None
            if sub.get("items", {}).get("data"):
                price_id = sub["items"]["data"][0].get("plan", {}).get("id")

            cur.execute(
                """INSERT INTO subscriptions
                   (customer_id, stripe_subscription_id, stripe_price_id, status,
                    current_period_start, current_period_end, cancel_at_period_end)
                   VALUES (%s, %s, %s, %s,
                           to_timestamp(%s), to_timestamp(%s), %s)
                   ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                       status = EXCLUDED.status,
                       stripe_price_id = EXCLUDED.stripe_price_id,
                       current_period_start = EXCLUDED.current_period_start,
                       current_period_end = EXCLUDED.current_period_end,
                       cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                       updated_at = NOW()""",
                (
                    customer_id,
                    subscription_id,
                    price_id,
                    sub.get("status", "active"),
                    sub.get("current_period_start"),
                    sub.get("current_period_end"),
                    sub.get("cancel_at_period_end", False),
                ),
            )
            conn.commit()

    logger.info("Checkout completed: sub=%s customer=%s", subscription_id, stripe_customer_id)


def _handle_subscription_updated(sub_obj):
    """customer.subscription.updated — sync status, period, cancel_at."""
    sub_id = sub_obj.get("id")
    price_id = None
    if sub_obj.get("items", {}).get("data"):
        price_id = sub_obj["items"]["data"][0].get("plan", {}).get("id")

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE subscriptions SET
                       status = %s,
                       stripe_price_id = %s,
                       current_period_start = to_timestamp(%s),
                       current_period_end = to_timestamp(%s),
                       cancel_at_period_end = %s,
                       updated_at = NOW()
                   WHERE stripe_subscription_id = %s""",
                (
                    sub_obj.get("status"),
                    price_id,
                    sub_obj.get("current_period_start"),
                    sub_obj.get("current_period_end"),
                    sub_obj.get("cancel_at_period_end", False),
                    sub_id,
                ),
            )
            conn.commit()

    logger.info("Subscription updated: %s → %s", sub_id, sub_obj.get("status"))


def _handle_subscription_deleted(sub_obj):
    """customer.subscription.deleted — mark canceled."""
    sub_id = sub_obj.get("id")

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE subscriptions SET status = 'canceled', updated_at = NOW()
                   WHERE stripe_subscription_id = %s""",
                (sub_id,),
            )
            conn.commit()

    logger.info("Subscription deleted (canceled): %s", sub_id)


def _handle_invoice_payment_succeeded(invoice_obj):
    """invoice.payment_succeeded — refresh active status and period."""
    sub_id = invoice_obj.get("subscription")
    if not sub_id:
        return

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE subscriptions SET
                       status = 'active',
                       current_period_start = to_timestamp(%s),
                       current_period_end = to_timestamp(%s),
                       updated_at = NOW()
                   WHERE stripe_subscription_id = %s""",
                (
                    invoice_obj.get("period_start"),
                    invoice_obj.get("period_end"),
                    sub_id,
                ),
            )
            conn.commit()

    logger.info("Invoice payment succeeded for sub=%s", sub_id)


def _handle_invoice_payment_failed(invoice_obj):
    """invoice.payment_failed — mark past_due."""
    sub_id = invoice_obj.get("subscription")
    if not sub_id:
        return

    with _pool_get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE subscriptions SET status = 'past_due', updated_at = NOW()
                   WHERE stripe_subscription_id = %s""",
                (sub_id,),
            )
            conn.commit()

    logger.info("Invoice payment failed for sub=%s", sub_id)
