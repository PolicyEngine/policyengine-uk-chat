from pathlib import Path
import pytest

from conversations import models


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_get_engine_requires_database_url(monkeypatch):
    monkeypatch.setattr(models, "_engine", None)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        models.get_engine()


def test_baseline_migration_never_modifies_conversation_rows():
    migration = (
        REPO_ROOT
        / "backend/migrations/versions/0001_pre_branch_conversation_schema_baseline.py"
    ).read_text()
    normalized = " ".join(migration.upper().split())

    assert "CREATE_INDEX" in normalized
    assert "DELETE FROM" not in normalized
    assert "UPDATE CHAT_CONVERSATIONS" not in normalized


def test_get_engine_creates_and_caches_engine(monkeypatch):
    engine = object()
    calls = []
    monkeypatch.setattr(models, "_engine", None)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///test.sqlite")
    monkeypatch.setattr(
        models,
        "create_engine",
        lambda url: calls.append(url) or engine,
    )

    assert models.get_engine() is engine
    assert models.get_engine() is engine
    assert calls == ["sqlite:///test.sqlite"]
def test_conversation_model_declares_stable_migration_indexes():
    indexes = {
        index.name: (tuple(column.name for column in index.columns), index.unique)
        for index in models.ChatConversation.__table__.indexes
    }

    assert indexes == {
        "idx_chat_conversations_session_id_unique": (("session_id",), True),
        "idx_chat_conversations_share_token": (("share_token",), False),
    }
