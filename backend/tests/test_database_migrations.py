"""Alembic ownership, revision validation, and PostgreSQL lifecycle tests."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.schema import DropSchema
from sqlmodel import SQLModel

from conversations.models import ChatConversation
import persistence.rows  # noqa: F401
from persistence.schema import (
    DatabaseSchemaError,
    expected_schema_revision,
    verify_database_schema,
)
from persistence.database_namespace import (
    DatabaseNamespaceError,
    drop_preview_schema,
    ensure_database_schema,
    namespaced_engine,
    validate_database_schema,
)
from persistence.migration_adoption import (
    AdoptionKind,
    DatabaseAdoptionError,
    inspect_database_for_adoption,
    upgrade_deployed_database,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = REPO_ROOT / "backend/alembic.ini"
MIGRATION_POSTGRES_URL = os.environ.get("CAPABILITY_TEST_POSTGRES_URL")
HEAD_REVISION = "9526d8c80914"

MANAGED_TABLES = {
    "chat_conversations",
    "capability_artifacts",
    "waiting_capability_invocations",
    "capability_invocation_traces",
    "capability_turn_receipts",
    "capability_call_receipts",
    "conversation_contexts",
}


def _drop_test_schema(database_url: str, schema: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
    finally:
        engine.dispose()


def test_revision_history_is_linear_and_starts_with_pre_branch_baseline():
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_CONFIG)))

    assert script.get_heads() == [HEAD_REVISION]
    assert expected_schema_revision(ALEMBIC_CONFIG) == HEAD_REVISION
    assert script.get_revision("0001").down_revision is None
    assert script.get_revision("0002").down_revision == "0001"
    assert script.get_revision("d97a20592837").down_revision == "0002"
    assert script.get_revision(HEAD_REVISION).down_revision == "d97a20592837"


def test_sqlmodel_metadata_contains_only_declared_alembic_tables():
    assert set(SQLModel.metadata.tables) == MANAGED_TABLES
    assert ChatConversation.__tablename__ in MANAGED_TABLES


def test_startup_schema_validation_is_read_only():
    model_source = (REPO_ROOT / "backend/conversations/models.py").read_text()
    main_source = (REPO_ROOT / "backend/api/main.py").read_text()

    assert "metadata.create_all" not in model_source
    assert "ALTER TABLE" not in model_source
    assert "CREATE INDEX" not in model_source
    assert "verify_database_schema()" in main_source


def test_sqlite_is_reserved_for_isolated_fixture_schema():
    engine = create_engine("sqlite://")
    try:
        assert verify_database_schema(engine) is None
    finally:
        engine.dispose()


def test_database_schema_names_are_strictly_validated():
    assert validate_database_schema("uk_chat_pr_261") == "uk_chat_pr_261"
    for invalid in ("public;drop schema public", "PR-261", "", "9preview"):
        with pytest.raises(DatabaseNamespaceError):
            validate_database_schema(invalid)


def test_preview_cleanup_rejects_non_preview_schema():
    with pytest.raises(DatabaseNamespaceError, match="non-preview"):
        drop_preview_schema("postgresql://unused", "public")


def test_multiple_repository_heads_fail_validation(monkeypatch):
    monkeypatch.setattr(
        "persistence.schema.ScriptDirectory.get_heads",
        lambda _self: ["one", "two"],
    )

    with pytest.raises(DatabaseSchemaError, match="exactly one"):
        expected_schema_revision(ALEMBIC_CONFIG)


@pytest.mark.skipif(
    not MIGRATION_POSTGRES_URL,
    reason="CAPABILITY_TEST_POSTGRES_URL is not configured for disposable PostgreSQL tests",
)
def test_postgres_fresh_upgrade_check_downgrade_and_unmanaged_exclusion(
    monkeypatch,
):
    """Exercise the complete lifecycle on an explicitly disposable database."""
    assert MIGRATION_POSTGRES_URL is not None
    schema = "migration_lifecycle"
    _drop_test_schema(MIGRATION_POSTGRES_URL, schema)
    ensure_database_schema(MIGRATION_POSTGRES_URL, schema)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", MIGRATION_POSTGRES_URL)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    config = Config(str(ALEMBIC_CONFIG))
    engine = namespaced_engine(MIGRATION_POSTGRES_URL, schema)
    unmanaged_table = "analysis_migration_test_sentinel"

    try:
        command.downgrade(config, "base")
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE TABLE {unmanaged_table} "
                    "(sentinel_id INTEGER PRIMARY KEY)"
                )
            )

        command.upgrade(config, "head")
        command.check(config)

        database_inspector = inspect(engine)
        assert MANAGED_TABLES <= set(database_inspector.get_table_names())
        assert unmanaged_table in database_inspector.get_table_names()
        assert verify_database_schema(engine) == HEAD_REVISION
        trace_columns = {
            column["name"]
            for column in database_inspector.get_columns(
                "capability_invocation_traces"
            )
        }
        assert {"debug_input_json", "debug_output_json"} <= trace_columns
        context_columns = {
            column["name"]
            for column in database_inspector.get_columns("conversation_contexts")
        }
        assert {
            "conversation_id",
            "schema_version",
            "revision",
            "payload_json",
            "created_at",
            "updated_at",
        } == context_columns

        for table_name in MANAGED_TABLES - {"chat_conversations"}:
            assert database_inspector.get_foreign_keys(table_name) == []

        command.downgrade(config, "0001")
        baseline_tables = set(inspect(engine).get_table_names())
        assert "chat_conversations" in baseline_tables
        assert unmanaged_table in baseline_tables
        assert not (
            MANAGED_TABLES - {"chat_conversations"}
        ) & baseline_tables
        with pytest.raises(
            DatabaseSchemaError,
            match=f"expected {HEAD_REVISION}, found 0001",
        ):
            verify_database_schema(engine)

        command.upgrade(config, "head")
        assert verify_database_schema(engine) == HEAD_REVISION
    finally:
        engine.dispose()
        _drop_test_schema(MIGRATION_POSTGRES_URL, schema)


@pytest.mark.skipif(
    not MIGRATION_POSTGRES_URL,
    reason="CAPABILITY_TEST_POSTGRES_URL is not configured for disposable PostgreSQL tests",
)
def test_postgres_adopts_exact_pre_alembic_schema_without_rewriting_rows(
    monkeypatch,
):
    assert MIGRATION_POSTGRES_URL is not None
    schema = "migration_adoption_exact"
    _drop_test_schema(MIGRATION_POSTGRES_URL, schema)
    ensure_database_schema(MIGRATION_POSTGRES_URL, schema)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", MIGRATION_POSTGRES_URL)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    engine = namespaced_engine(MIGRATION_POSTGRES_URL, schema)
    try:
        ChatConversation.__table__.create(engine)
        with engine.begin() as connection:
            connection.execute(
                ChatConversation.__table__.insert().values(
                    session_id="adoption-preserves-this-row",
                    title="Existing conversation",
                    messages=[],
                    created_at=datetime(2026, 8, 30),
                    updated_at=datetime(2026, 8, 30),
                )
            )

        before = inspect_database_for_adoption(engine, schema=schema)
        assert before.kind is AdoptionKind.EXACT_BASELINE
        assert before.revision is None

        adopted = upgrade_deployed_database(ALEMBIC_CONFIG)
        assert adopted.kind is AdoptionKind.EXACT_BASELINE
        after = inspect_database_for_adoption(engine, schema=schema)
        assert after.kind is AdoptionKind.VERSIONED
        assert after.revision == HEAD_REVISION
        with engine.connect() as connection:
            assert connection.scalar(
                select(func.count()).select_from(ChatConversation)
            ) == 1
            assert connection.scalar(
                select(ChatConversation.session_id)
            ) == "adoption-preserves-this-row"
    finally:
        engine.dispose()
        _drop_test_schema(MIGRATION_POSTGRES_URL, schema)


@pytest.mark.skipif(
    not MIGRATION_POSTGRES_URL,
    reason="CAPABILITY_TEST_POSTGRES_URL is not configured for disposable PostgreSQL tests",
)
def test_postgres_rejects_unversioned_schema_drift_before_stamping(monkeypatch):
    assert MIGRATION_POSTGRES_URL is not None
    schema = "migration_adoption_mismatch"
    _drop_test_schema(MIGRATION_POSTGRES_URL, schema)
    ensure_database_schema(MIGRATION_POSTGRES_URL, schema)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", MIGRATION_POSTGRES_URL)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    engine = namespaced_engine(MIGRATION_POSTGRES_URL, schema)
    try:
        ChatConversation.__table__.create(engine)
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE chat_conversations ADD COLUMN unexpected TEXT")
            )

        inspection = inspect_database_for_adoption(engine, schema=schema)
        assert inspection.kind is AdoptionKind.INCOMPATIBLE
        assert inspection.differences
        with pytest.raises(DatabaseAdoptionError, match="does not match revision 0001"):
            upgrade_deployed_database(ALEMBIC_CONFIG)
        assert inspect_database_for_adoption(engine, schema=schema).revision is None
    finally:
        engine.dispose()
        _drop_test_schema(MIGRATION_POSTGRES_URL, schema)


@pytest.mark.skipif(
    not MIGRATION_POSTGRES_URL,
    reason="CAPABILITY_TEST_POSTGRES_URL is not configured for disposable PostgreSQL tests",
)
def test_postgres_preview_schema_is_isolated_and_removable(monkeypatch):
    assert MIGRATION_POSTGRES_URL is not None
    schema = "uk_chat_pr_900001"
    _drop_test_schema(MIGRATION_POSTGRES_URL, schema)
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", MIGRATION_POSTGRES_URL)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    try:
        result = upgrade_deployed_database(ALEMBIC_CONFIG)
        assert result.kind is AdoptionKind.EMPTY
        engine = namespaced_engine(MIGRATION_POSTGRES_URL, schema)
        try:
            assert set(inspect(engine).get_table_names(schema=schema)) >= MANAGED_TABLES
            assert inspect_database_for_adoption(
                engine,
                schema=schema,
            ).revision == HEAD_REVISION
        finally:
            engine.dispose()

        drop_preview_schema(MIGRATION_POSTGRES_URL, schema)
        admin_engine = create_engine(MIGRATION_POSTGRES_URL)
        try:
            assert schema not in inspect(admin_engine).get_schema_names()
        finally:
            admin_engine.dispose()
    finally:
        _drop_test_schema(MIGRATION_POSTGRES_URL, schema)
