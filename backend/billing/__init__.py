"""Billing: token pricing, Supabase credit accounting, and Stripe checkout.

Re-exports the feature flag, router (for app wiring), and the two credit
functions the chat route calls when billing is enabled.
"""

from billing.config import billing_enabled
from billing.credits import check_balance, record_usage
from billing.routes import router

__all__ = ["billing_enabled", "router", "check_balance", "record_usage"]
