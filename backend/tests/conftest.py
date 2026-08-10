"""Pytest configuration shared by all backend tests.

Runs before any test module imports `main`, so env vars set here flow
through to module-level constants (e.g. the slowapi limits in
`rate_limit.py`). Without this, the existing chat tests would trip the
production 5/minute limit since each class makes more than 5 POSTs to
`/chat/message`.
"""

import importlib.util
import os
from pathlib import Path

import pytest

# Bump rate limits well above test workload. Real production limits are
# 5/min and 60/hour for chat — these are intentionally absurd so the
# limiter never fires during normal pytest runs.
os.environ.setdefault("RATE_LIMIT_CHAT_PER_MIN", "10000")
os.environ.setdefault("RATE_LIMIT_CHAT_PER_HOUR", "100000")
os.environ.setdefault("RATE_LIMIT_CHAT_IP_PER_MIN", "10000")

# Production billing is opt-in and defaults off. The test suite enables it so
# existing credit-accounting and chat-usage tests continue to exercise the
# enabled path; individual feature-flag tests override this explicitly.
os.environ.setdefault("BILLING_ENABLED", "true")

# main.py reads HOSTNAMES at import to build the CORS allowlist, and now fails
# closed (blocks all origins) when it is unset. Declare an allowed origin here
# so CORS-preflight tests exercise the real allowlisted path.
os.environ.setdefault("HOSTNAMES", "https://policyengine-uk-chat.vercel.app")

if not os.environ.get("DATABASE_URL"):
    test_db = Path(os.environ.get("PYTEST_SQLITE_DB", "/tmp/policyengine_uk_chat_tests.sqlite"))
    try:
        test_db.unlink()
    except FileNotFoundError:
        pass
    os.environ["DATABASE_URL"] = f"sqlite:///{test_db}"

POLICYENGINE_PY_AVAILABLE = (
    importlib.util.find_spec("policyengine") is not None
    and importlib.util.find_spec("policyengine_uk") is not None
)

# Tests needing the policyengine.py UK model skip locally when it is not
# installed, but always run in CI so a broken engine install fails loudly
# instead of skipping the suite green.
requires_policyengine_py = pytest.mark.skipif(
    os.environ.get("CI") != "true" and not POLICYENGINE_PY_AVAILABLE,
    reason="policyengine.py UK packages are not installed",
)


@pytest.fixture
def isolated_conversations_table(tmp_path, monkeypatch):
    """Give every test a fresh conversations table without touching Postgres."""
    from sqlmodel import SQLModel, create_engine
    from conversations import models as conversations

    engine = create_engine(
        f"sqlite:///{tmp_path / 'conversations.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(conversations, "_engine", engine)

    yield

    SQLModel.metadata.drop_all(engine)
    engine.dispose()
