"""
Billing — token cost tracking, credit management, Stripe checkout.
"""

import logging
import os
from datetime import datetime, timezone
from dateutil.relativedelta import relativedelta

import stripe
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from supabase import create_client, Client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/billing", tags=["billing"])

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MARKUP_RATE = float(os.environ.get("BILLING_MARKUP_RATE", "0.0"))
FREE_TIER_GBP = float(os.environ.get("BILLING_FREE_TIER_GBP", "5.0"))
USD_TO_GBP = float(os.environ.get("BILLING_USD_TO_GBP", "0.79"))
INPUT_COST_PER_M_USD = 3.0   # Claude Sonnet 4
OUTPUT_COST_PER_M_USD = 15.0

# ---------------------------------------------------------------------------
# Supabase client (service role — bypasses RLS)
# ---------------------------------------------------------------------------

_supabase: Client | None = None


def get_supabase() -> Client:
    global _supabase
    if _supabase is None:
        url = os.environ.get("SUPABASE_URL", "")
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
        if not url or not key:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        _supabase = create_client(url, key)
    return _supabase


# ---------------------------------------------------------------------------
# Cost calculation
# ---------------------------------------------------------------------------

def calculate_cost_gbp(input_tokens: int, output_tokens: int) -> float:
    cost_usd = input_tokens * INPUT_COST_PER_M_USD / 1_000_000 + output_tokens * OUTPUT_COST_PER_M_USD / 1_000_000
    return cost_usd * USD_TO_GBP * (1 + MARKUP_RATE)


# ---------------------------------------------------------------------------
# Credit helpers (called by chatbot.py too)
# ---------------------------------------------------------------------------

def get_or_create_credits(user_id: str) -> dict:
    """Get user credits row, creating if needed. Resets free tier monthly."""
    sb = get_supabase()
    row = sb.table("user_credits").select("*").eq("user_id", user_id).execute()

    now = datetime.now(timezone.utc)

    if not row.data:
        data = {
            "user_id": user_id,
            "balance_gbp": 0,
            "free_tier_used_gbp": 0,
            "free_tier_reset_at": now.isoformat(),
        }
        try:
            result = sb.table("user_credits").insert(data).execute()
            return result.data[0]
        except Exception as e:
            logger.warning(f"Could not create credits row for {user_id}: {e}")
            # Return a default — user may not exist in auth.users yet
            return data

    credits = row.data[0]
    reset_at = datetime.fromisoformat(credits["free_tier_reset_at"].replace("Z", "+00:00"))
    if now >= reset_at + relativedelta(months=1):
        sb.table("user_credits").update({
            "free_tier_used_gbp": 0,
            "free_tier_reset_at": now.isoformat(),
        }).eq("user_id", user_id).execute()
        credits["free_tier_used_gbp"] = 0
        credits["free_tier_reset_at"] = now.isoformat()

    return credits


def check_balance(user_id: str) -> tuple[bool, dict]:
    """Returns (has_credit, credits_dict). has_credit is True if user can send a message."""
    credits = get_or_create_credits(user_id)
    free_remaining = max(0, FREE_TIER_GBP - float(credits["free_tier_used_gbp"]))
    balance = float(credits["balance_gbp"])
    return (free_remaining + balance) > 0, credits


def record_usage(user_id: str | None, session_id: str, input_tokens: int, output_tokens: int):
    """Record token usage and deduct cost from user credits."""
    cost = calculate_cost_gbp(input_tokens, output_tokens)
    sb = get_supabase()

    # Insert usage row
    try:
        sb.table("token_usage").insert({
            "user_id": user_id,
            "session_id": session_id,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_gbp": cost,
        }).execute()
    except Exception as e:
        logger.warning(f"Could not insert token_usage for {user_id}: {e}")

    if not user_id:
        return cost

    # Deduct from free tier first, then balance
    credits = get_or_create_credits(user_id)
    free_remaining = max(0, FREE_TIER_GBP - float(credits["free_tier_used_gbp"]))

    if cost <= free_remaining:
        sb.table("user_credits").update({
            "free_tier_used_gbp": float(credits["free_tier_used_gbp"]) + cost,
        }).eq("user_id", user_id).execute()
    else:
        # Use up remaining free tier, rest from balance
        overflow = cost - free_remaining
        sb.table("user_credits").update({
            "free_tier_used_gbp": FREE_TIER_GBP,
            "balance_gbp": max(0, float(credits["balance_gbp"]) - overflow),
        }).eq("user_id", user_id).execute()

    return cost


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/balance")
def get_balance(user_id: str):
    credits = get_or_create_credits(user_id)
    free_remaining = max(0, FREE_TIER_GBP - float(credits["free_tier_used_gbp"]))
    return {
        "balance_gbp": float(credits["balance_gbp"]),
        "free_tier_remaining_gbp": free_remaining,
        "total_available_gbp": float(credits["balance_gbp"]) + free_remaining,
    }


@router.get("/usage")
def get_usage(user_id: str, limit: int = 50):
    sb = get_supabase()
    result = sb.table("token_usage").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(limit).execute()
    return result.data


class CheckoutRequest(BaseModel):
    user_id: str
    amount_gbp: float = 5.0


@router.post("/checkout")
def create_checkout(request: CheckoutRequest):
    stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not stripe.api_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")

    amount_pence = int(request.amount_gbp * 100)
    base_url = os.environ.get("HOSTNAMES", "http://localhost:3006").split(",")[0]

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
    return {"url": session.url}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
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

    return {"status": "ok"}
