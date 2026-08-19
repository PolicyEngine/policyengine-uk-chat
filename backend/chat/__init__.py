"""Lazy chat package exports that keep event and schema imports lightweight."""

__all__ = ["router"]


def __getattr__(name):
    if name == "router":
        from chat.routes import router

        return router
    raise AttributeError(name)
