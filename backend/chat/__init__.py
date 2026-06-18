"""The chat turn: orchestration, system-block assembly, model selection,
titles, and follow-up suggestions. Re-exports the router for app wiring.
"""

from chat.routes import router

__all__ = ["router"]
