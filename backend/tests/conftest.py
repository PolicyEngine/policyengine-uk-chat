"""Pytest configuration shared by all backend tests.

Runs before any test module imports `main`, so env vars set here flow
through to module-level constants (e.g. the slowapi limits in
`rate_limit.py`). Without this, the existing chat tests would trip the
production 5/minute limit since each class makes more than 5 POSTs to
`/chat/message`.
"""

import os

# Bump rate limits well above test workload. Real production limits are
# 5/min and 60/hour for chat — these are intentionally absurd so the
# limiter never fires during normal pytest runs.
os.environ.setdefault("RATE_LIMIT_CHAT_PER_MIN", "10000")
os.environ.setdefault("RATE_LIMIT_CHAT_PER_HOUR", "100000")
os.environ.setdefault("RATE_LIMIT_CHAT_IP_PER_MIN", "10000")
