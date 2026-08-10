"""HTTP endpoints for billing: balance, usage history, Stripe checkout/webhook."""

from fastapi import APIRouter, Depends, HTTPException, Request

from billing.config import billing_enabled
from billing.credits import get_balance_summary, get_supabase
from billing.stripe_integration import CheckoutRequest, apply_webhook, create_checkout_session


def require_billing_enabled() -> None:
    """Hide the dormant billing API unless it is explicitly enabled."""

    if not billing_enabled():
        raise HTTPException(status_code=404, detail="Not found")


router = APIRouter(
    prefix="/billing",
    tags=["billing"],
    dependencies=[Depends(require_billing_enabled)],
)


@router.get("/balance")
def get_balance(user_id: str):
    return get_balance_summary(user_id)


@router.get("/usage")
def get_usage(user_id: str, limit: int = 50):
    sb = get_supabase()
    result = (
        sb.table("token_usage")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data


@router.post("/checkout")
def create_checkout(request: CheckoutRequest):
    return {"url": create_checkout_session(request)}


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")
    apply_webhook(payload, sig)
    return {"status": "ok"}
