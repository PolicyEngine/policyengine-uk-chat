"""Lazy conversation exports for API wiring and storage-only imports."""

__all__ = ["router", "ensure_table"]


def __getattr__(name):
    if name == "router":
        from conversations.routes import router

        return router
    if name == "ensure_table":
        from conversations.models import ensure_table

        return ensure_table
    raise AttributeError(name)
