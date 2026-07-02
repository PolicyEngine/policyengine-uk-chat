"""Stripe integration: checkout session creation and webhook handling.

The FastAPI layer (routes.py) reads the request and returns the response; the
Stripe SDK calls and credit side-effects live here.
"""

import logging
import os

import stripe
from fastapi import HTTPException
from pydantic import BaseModel

from billing.credits import get_or_create_credits, get_supabase

logger = logging.getLogger(__name__)


class CheckoutRequest(BaseModel):
    user_id: str
    amount_gbp: float = 5.0


def _get_public_base_url() -> str:
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "").strip()
    if public_base_url:
        return public_base_url.rstrip("/")
    return os.environ.get("HOSTNAMES", "http://localhost:3006").split(",")[0].rstrip("/")


def create_checkout_session(request: CheckoutRequest) -> str:
    """Create a Stripe checkout session and return its hosted URL."""
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    amount_pence = int(request.amount_gbp * 100)
    base_url = _get_public_base_url()

    session = stripe.checkout.Session.create(
        mode="payment",
        payment_method_types=["card"],
        line_items=[{
            "price_data": {
                "currency": "gbp",
                "product_data": {"name": "PolicyEngine chat credit"},
                "unit_amount": amount_pence,
            },
            "quantity": 1,
        }],
        metadata={"user_id": request.user_id, "amount_gbp": str(request.amount_gbp)},
        success_url=f"{base_url}?topup=success",
        cancel_url=f"{base_url}?topup=cancel",
    )
    return session.url


def apply_webhook(payload: bytes, sig: str) -> None:
    """Verify a Stripe webhook and credit the user on a completed checkout."""
    webhook_secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig, webhook_secret)
    except Exception as e:
        logger.error(f"Stripe webhook error: {e}")
        raise HTTPException(status_code=400, detail="Invalid signature")

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        user_id = session.get("metadata", {}).get("user_id")
        amount_gbp = float(session.get("metadata", {}).get("amount_gbp", "0"))

        if user_id and amount_gbp > 0:
            credits = get_or_create_credits(user_id)
            sb = get_supabase()
            sb.table("user_credits").update({
                "balance_gbp": float(credits["balance_gbp"]) + amount_gbp,
            }).eq("user_id", user_id).execute()
            logger.info(f"Credited £{amount_gbp} to user {user_id}")
