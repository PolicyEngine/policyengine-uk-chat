"""Billing: token pricing, Supabase credit accounting, and Stripe checkout.

Re-exports the router (for app wiring) and the two credit functions the chat
route calls (check_balance before a turn, record_usage after).
"""

from billing.credits import check_balance, record_usage
from billing.routes import router

__all__ = ["router", "check_balance", "record_usage"]
