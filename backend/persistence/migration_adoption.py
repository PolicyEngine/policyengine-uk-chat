"""Guarded adoption of the exact pre-Alembic conversation schema."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
from typing import Any

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import MetaData, inspect
from sqlalchemy.engine import Connection, Engine

from conversations.models import ChatConversation
from persistence.database_namespace import (
    configured_database_schema,
    ensure_database_schema,
    namespaced_engine,
)
from persistence.rows import (
    CapabilityArtifactRow,
    CapabilityCallReceiptRow,
    ConversationContextRow,
    InvocationTraceRow,
    TurnReceiptRow,
    WaitingCapabilityInvocationRow,
)


BASELINE_REVISION = "0001"
BASELINE_TABLE = ChatConversation.__tablename__
MANAGED_TABLES = frozenset(
    {
        BASELINE_TABLE,
        CapabilityArtifactRow.__tablename__,
        WaitingCapabilityInvocationRow.__tablename__,
        InvocationTraceRow.__tablename__,
        TurnReceiptRow.__tablename__,
        CapabilityCallReceiptRow.__tablename__,
        ConversationContextRow.__tablename__,
    }
)


class AdoptionKind(StrEnum):
    EMPTY = "empty"
    EXACT_BASELINE = "exact_baseline"
    VERSIONED = "versioned"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class AdoptionInspection:
    kind: AdoptionKind
    schema: str
    revision: str | None = None
    managed_tables: tuple[str, ...] = ()
    differences: tuple[str, ...] = ()

    def debug_summary(self) -> dict[str, Any]:
        """Return structural metadata only; never include table row values."""
        return {
            "kind": self.kind.value,
            "schema": self.schema,
            "revision": self.revision,
            "managed_tables": list(self.managed_tables),
            "differences": list(self.differences),
        }


class DatabaseAdoptionError(RuntimeError):
    """Raised when an unversioned database is not the exact baseline."""


def _baseline_metadata() -> MetaData:
    metadata = MetaData()
    ChatConversation.__table__.to_metadata(metadata)
    return metadata


def _format_difference(difference: Any) -> str:
    """Render one Alembic structural difference without database row data."""
    if isinstance(difference, tuple) and difference:
        operation = str(difference[0])
        if operation in {"add_table", "remove_table"}:
            table = difference[1]
            return f"{operation}:{getattr(table, 'name', '<unknown>')}"
        if operation in {"add_index", "remove_index"}:
            index = difference[1]
            return f"{operation}:{getattr(index, 'name', '<unknown>')}"
        return ":".join(str(item) for item in difference[:4])
    return str(difference)


def _baseline_differences(connection: Connection, schema: str) -> tuple[str, ...]:
    database_inspector = inspect(connection)
    if database_inspector.default_schema_name != schema:
        return (
            "configured schema is not the connection's default schema: "
            f"{database_inspector.default_schema_name}",
        )
    baseline = _baseline_metadata()

    def include_object(
        object_: Any,
        name: str | None,
        type_: str,
        reflected: bool,
        compare_to: Any,
    ) -> bool:
        del object_, reflected, compare_to
        if type_ == "table":
            return name == BASELINE_TABLE
        return True

    context = MigrationContext.configure(
        connection,
        opts={
            "target_metadata": baseline,
            "include_object": include_object,
            "include_schemas": False,
            "compare_type": True,
            "compare_server_default": True,
            "version_table_schema": schema,
        },
    )
    return tuple(
        _format_difference(difference)
        for difference in compare_metadata(context, baseline)
    )


def inspect_database_for_adoption(
    engine: Engine,
    *,
    schema: str | None = None,
) -> AdoptionInspection:
    """Inspect revision and SQLModel-owned structure without reading any rows."""
    with engine.connect() as connection:
        database_inspector = inspect(connection)
        selected_schema = schema or database_inspector.default_schema_name
        migration_context = MigrationContext.configure(
            connection,
            opts={"version_table_schema": schema},
        )
        revision = migration_context.get_current_revision()
        tables = set(database_inspector.get_table_names(schema=selected_schema))
        managed_tables = tuple(sorted(tables & MANAGED_TABLES))
        if revision is not None:
            return AdoptionInspection(
                kind=AdoptionKind.VERSIONED,
                schema=selected_schema,
                revision=revision,
                managed_tables=managed_tables,
            )
        if not managed_tables:
            return AdoptionInspection(
                kind=AdoptionKind.EMPTY,
                schema=selected_schema,
            )
        if managed_tables != (BASELINE_TABLE,):
            return AdoptionInspection(
                kind=AdoptionKind.INCOMPATIBLE,
                schema=selected_schema,
                managed_tables=managed_tables,
                differences=(
                    "unversioned managed tables do not equal the baseline table set",
                ),
            )
        differences = _baseline_differences(connection, selected_schema)
        return AdoptionInspection(
            kind=(
                AdoptionKind.EXACT_BASELINE
                if not differences
                else AdoptionKind.INCOMPATIBLE
            ),
            schema=selected_schema,
            managed_tables=managed_tables,
            differences=differences,
        )


def migration_config(config_path: str | Path) -> Config:
    return Config(str(config_path))


def upgrade_deployed_database(config_path: str | Path) -> AdoptionInspection:
    """Adopt only an exact legacy baseline, then upgrade the database to head."""
    database_url = os.environ.get("ALEMBIC_DATABASE_URL", "")
    if not database_url:
        raise DatabaseAdoptionError(
            "ALEMBIC_DATABASE_URL is required for database migrations"
        )
    schema = configured_database_schema()
    ensure_database_schema(database_url, schema)
    engine = namespaced_engine(database_url, schema)
    try:
        inspection = inspect_database_for_adoption(engine, schema=schema)
    finally:
        engine.dispose()

    if inspection.kind is AdoptionKind.INCOMPATIBLE:
        details = ", ".join(inspection.differences) or "unknown structural difference"
        raise DatabaseAdoptionError(
            "Unversioned SQLModel schema does not match revision 0001: " + details
        )

    config = migration_config(config_path)
    if inspection.kind is AdoptionKind.EXACT_BASELINE:
        command.stamp(config, BASELINE_REVISION)
    command.upgrade(config, "head")

    verification_engine = namespaced_engine(database_url, schema)
    try:
        upgraded = inspect_database_for_adoption(
            verification_engine,
            schema=schema,
        )
    finally:
        verification_engine.dispose()
    expected_heads = ScriptDirectory.from_config(config).get_heads()
    if len(expected_heads) != 1 or upgraded.revision != expected_heads[0]:
        raise DatabaseAdoptionError(
            "Database upgrade did not reach the repository Alembic head"
        )
    return inspection
