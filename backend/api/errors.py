"""Custom response class and exception handlers for the API surface."""

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded


class NaNSafeJSONResponse(JSONResponse):
    """JSON response that converts NaN/Inf to null."""
    def render(self, content) -> bytes:
        import json, math
        def convert(obj):
            if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
                return None
            if isinstance(obj, dict):
                return {k: convert(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [convert(v) for v in obj]
            return obj
        return json.dumps(convert(content)).encode("utf-8")


async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """Return 429 with a `Retry-After` header for the slowapi limiter.

    slowapi's RateLimitExceeded carries a `limit` whose underlying object
    knows the granularity. We pull the period in seconds when we can and
    fall back to 60 — the safe default given our per-minute limits.
    """
    retry_after_seconds = 60
    try:
        granularity = getattr(exc.limit.limit, "GRANULARITY", None)
        seconds = getattr(granularity, "seconds", None)
        if isinstance(seconds, int) and seconds > 0:
            retry_after_seconds = seconds
    except AttributeError:
        pass
    return JSONResponse(
        status_code=429,
        content={
            "error": "Too many requests",
            "details": str(getattr(exc, "detail", "Rate limit exceeded")),
            "retry_after_seconds": retry_after_seconds,
        },
        headers={"Retry-After": str(retry_after_seconds)},
    )
