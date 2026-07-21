from types import SimpleNamespace

import pytest

from conversations import models


def test_get_engine_requires_database_url(monkeypatch):
    monkeypatch.setattr(models, "_engine", None)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        models.get_engine()


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


class FakeConnection:
    def __init__(self, failures=()):
        self.failures = set(failures)
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if any(fragment in sql for fragment in self.failures):
            raise RuntimeError("already exists")

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_ensure_table_adds_columns_and_index(monkeypatch):
    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection)
    create_all_calls = []
    monkeypatch.setattr(models, "get_engine", lambda: engine)
    monkeypatch.setattr(
        models.SQLModel.metadata,
        "create_all",
        lambda value: create_all_calls.append(value),
    )

    models.ensure_table()

    assert create_all_calls == [engine]
    assert len(connection.statements) == 3
    assert connection.commits == 3
    assert connection.rollbacks == 0


def test_ensure_table_rolls_back_existing_columns_and_failed_index(monkeypatch):
    connection = FakeConnection(failures=("ADD COLUMN", "CREATE INDEX"))
    engine = SimpleNamespace(connect=lambda: connection)
    monkeypatch.setattr(models, "get_engine", lambda: engine)
    monkeypatch.setattr(models.SQLModel.metadata, "create_all", lambda _engine: None)

    models.ensure_table()

    assert connection.commits == 0
    assert connection.rollbacks == 3


def test_ensure_table_logs_outer_failure_without_raising(monkeypatch, caplog):
    monkeypatch.setattr(
        models,
        "get_engine",
        lambda: (_ for _ in ()).throw(RuntimeError("database unavailable")),
    )

    assert models.ensure_table() is None
    assert "database unavailable" in caplog.text
