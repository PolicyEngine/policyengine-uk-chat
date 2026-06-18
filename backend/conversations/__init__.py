"""Stored chat history: persistence, sharing, and reporting.

Re-exports the router (for app wiring) and ensure_table (called at startup).
"""

from conversations.models import ensure_table
from conversations.routes import router

__all__ = ["router", "ensure_table"]
