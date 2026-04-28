"""
Authentication helpers for Supabase-backed routes.
"""

from fastapi import HTTPException, Request, status
from pydantic import BaseModel

from routes.supabase_client import get_supabase


class AuthenticatedUser(BaseModel):
    id: str
    email: str | None = None


def _get_attr_or_item(value, key: str):
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _extract_bearer_token(request: Request) -> str:
    auth_header = request.headers.get("authorization", "")
    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token.strip()


def require_user(request: Request) -> AuthenticatedUser:
    token = _extract_bearer_token(request)
    try:
        response = get_supabase().auth.get_user(token)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is not configured",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = _get_attr_or_item(response, "user")
    user_id = _get_attr_or_item(user, "id")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return AuthenticatedUser(
        id=user_id,
        email=_get_attr_or_item(user, "email"),
    )
