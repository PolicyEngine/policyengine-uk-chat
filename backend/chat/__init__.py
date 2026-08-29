"""Chat package with a lazily imported FastAPI adapter."""

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from fastapi import APIRouter

    router: APIRouter


def __getattr__(name: str):
    if name != "router":
        raise AttributeError(name)
    from chat.routes import router

    return router


__all__ = ["router"]
