"""Read-only validation of the deployed Alembic schema revision."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Engine

from conversations.models import get_engine
from persistence.database_namespace import configured_database_schema

logger = logging.getLogger(__name__)

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"


class DatabaseSchemaError(RuntimeError):
    """Raised when a deployed database is not at the repository revision."""


def expected_schema_revision(
    config_path: Path = ALEMBIC_CONFIG_PATH,
) -> str:
    """Return the repository's single Alembic head revision."""
    script = ScriptDirectory.from_config(Config(str(config_path)))
    heads = script.get_heads()
    if len(heads) != 1:
        raise DatabaseSchemaError(
            "Expected exactly one Alembic head revision; "
            f"found {len(heads)}"
        )
    return heads[0]


def current_schema_revision(engine: Engine) -> str | None:
    """Read the database's current Alembic revision without changing schema."""
    with engine.connect() as connection:
        return MigrationContext.configure(
            connection,
            opts={"version_table_schema": configured_database_schema()},
        ).get_current_revision()


def verify_database_schema(engine: Engine | None = None) -> str | None:
    """Require deployed PostgreSQL to match the repository Alembic revision.

    SQLite is reserved for isolated repository tests, whose fixtures construct
    temporary schemas directly from SQLModel metadata.
    """
    database_engine = engine or get_engine()
    if database_engine.dialect.name == "sqlite":
        logger.info(
            "Skipping Alembic revision validation for an isolated SQLite test database"
        )
        return None

    expected = expected_schema_revision()
    current = current_schema_revision(database_engine)
    if current != expected:
        found = current or "none"
        raise DatabaseSchemaError(
            "Database schema revision mismatch: "
            f"expected {expected}, found {found}. "
            "Run `alembic -c alembic.ini upgrade head` before starting the backend."
        )

    logger.info("Database schema revision %s verified", current)
    return current
