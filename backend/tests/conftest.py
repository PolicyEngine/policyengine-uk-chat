"""Pytest configuration shared by all backend tests.

Runs before any test module imports `main`, so env vars set here flow
through to module-level constants (e.g. the slowapi limits in
`rate_limit.py`). Without this, the existing chat tests would trip the
production 5/minute limit since each class makes more than 5 POSTs to
`/chat/message`.
"""

import os
from pathlib import Path

import pytest

# Bump rate limits well above test workload. Real production limits are
# 5/min and 60/hour for chat — these are intentionally absurd so the
# limiter never fires during normal pytest runs.
os.environ.setdefault("RATE_LIMIT_CHAT_PER_MIN", "10000")
os.environ.setdefault("RATE_LIMIT_CHAT_PER_HOUR", "100000")
os.environ.setdefault("RATE_LIMIT_CHAT_IP_PER_MIN", "10000")

if not os.environ.get("DATABASE_URL"):
    test_db = Path(os.environ.get("PYTEST_SQLITE_DB", "/tmp/policyengine_uk_chat_tests.sqlite"))
    try:
        test_db.unlink()
    except FileNotFoundError:
        pass
    os.environ["DATABASE_URL"] = f"sqlite:///{test_db}"


@pytest.fixture
def isolated_conversations_table(tmp_path, monkeypatch):
    """Give every test a fresh conversations table without touching Postgres."""
    from sqlmodel import SQLModel, create_engine
    import routes.conversations as conversations

    engine = create_engine(
        f"sqlite:///{tmp_path / 'conversations.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(conversations, "_engine", engine)

    yield

    SQLModel.metadata.drop_all(engine)
    engine.dispose()
