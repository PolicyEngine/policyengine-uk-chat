"""User credit accounting backed by Supabase (service role — bypasses RLS).

Called by the chat route (check_balance before a turn, record_usage after) and
by the Stripe webhook (top-ups).
"""

import logging
import os
from datetime import datetime, timezone
from typing import Any

from dateutil.relativedelta import relativedelta
from supabase import Client, create_client

from billing.pricing import _normalise_model_name, calculate_cost_gbp

logger = logging.getLogger(__name__)

FREE_TIER_GBP = float(os.environ.get("BILLING_FREE_TIER_GBP", "5.0"))

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


def get_balance_summary(user_id: str) -> dict[str, float]:
    credits = get_or_create_credits(user_id)
    free_remaining = max(0, FREE_TIER_GBP - float(credits["free_tier_used_gbp"]))
    balance = float(credits["balance_gbp"])
    return {
        "balance_gbp": balance,
        "free_tier_used_gbp": float(credits["free_tier_used_gbp"]),
        "free_tier_remaining_gbp": free_remaining,
        "spent_this_month_gbp": float(credits["free_tier_used_gbp"]),
        "total_available_gbp": balance + free_remaining,
    }


def record_usage(
    *,
    user_id: str | None,
    session_id: str,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> dict[str, Any]:
    """Record token usage and deduct cost from user credits."""
    cost = calculate_cost_gbp(
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )
    sb = get_supabase()

    # Insert usage row
    try:
        sb.table("token_usage").insert({
            "user_id": user_id,
            "session_id": session_id,
            "model": _normalise_model_name(model),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "cost_gbp": cost,
        }).execute()
    except Exception as e:
        logger.warning(f"Could not insert token_usage for {user_id}: {e}")

    if not user_id:
        return {
            "cost_gbp": cost,
            "model": _normalise_model_name(model),
            "balance": None,
        }

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

    return {
        "cost_gbp": cost,
        "model": _normalise_model_name(model),
        "balance": get_balance_summary(user_id),
    }
