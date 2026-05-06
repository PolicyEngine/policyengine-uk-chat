"""Rate limiting configuration.

Two layers protect /chat/message:
 - Per user (or per IP if anonymous): RATE_LIMIT_CHAT_PER_MIN/min and
   RATE_LIMIT_CHAT_PER_HOUR/hour. Authenticated clients send their user id
   in the `X-User-Id` header so the limiter can key by user instead of by
   shared NAT/proxy IP.
 - Per IP (defence-in-depth): RATE_LIMIT_CHAT_IP_PER_MIN/min, regardless
   of who is sending. Catches anonymous abuse and runaway clients.

Storage is the slowapi default (in-memory, per-process). With Modal's
max_containers=10 and concurrent=100, a determined attacker could spread
their requests across containers and bypass any single container's count
— the IP layer is approximate but adequate for the threat model. If we
need accuracy across containers later, point slowapi at Redis via
`storage_uri` from the environment.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


# Per-user limits on /chat/message. Defaults match the issue spec.
CHAT_PER_MIN = int(os.environ.get("RATE_LIMIT_CHAT_PER_MIN", "5"))
CHAT_PER_HOUR = int(os.environ.get("RATE_LIMIT_CHAT_PER_HOUR", "60"))

# Per-IP defence-in-depth on /chat/message.
CHAT_IP_PER_MIN = int(os.environ.get("RATE_LIMIT_CHAT_IP_PER_MIN", "30"))


def chat_key_func(request: Request) -> str:
    """Key chat requests by user id when present, otherwise client IP.

    The frontend sends `X-User-Id: <uuid>` for authenticated users. The
    header is ignored when missing or empty so anonymous traffic keys by
    IP, which prevents one anon flooder from blocking signed-in users.
    """
    user_id = request.headers.get("x-user-id")
    if user_id:
        return f"user:{user_id}"
    return f"ip:{get_remote_address(request)}"


# Default key_func is IP-based; per-route decorators override with
# `chat_key_func` when user-id keying is wanted.
limiter = Limiter(key_func=get_remote_address)


CHAT_USER_LIMIT = f"{CHAT_PER_MIN}/minute;{CHAT_PER_HOUR}/hour"
CHAT_IP_LIMIT = f"{CHAT_IP_PER_MIN}/minute"
