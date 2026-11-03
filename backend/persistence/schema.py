"""Read-only validation of the deployed schema revision and Data API access."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine

from conversations.models import get_engine
from persistence.database_namespace import configured_database_schema

logger = logging.getLogger(__name__)

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"

# Catalog functions only, so this reads no table rows. Signed-in users may read
# their own billing rows, which requires row-level security on those tables.
_DATA_API_EXPOSURE = text(
    """
    SELECT r.rolname, c.relname
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    JOIN pg_roles AS r ON r.rolname IN ('anon', 'authenticated')
    WHERE n.nspname = :schema
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND has_schema_privilege(r.oid, n.oid, 'USAGE')
      AND (
        has_table_privilege(r.oid, c.oid, 'DELETE,TRUNCATE')
        OR has_any_column_privilege(
          r.oid,
          c.oid,
          CASE
            WHEN r.rolname = 'authenticated'
              AND c.relname IN ('token_usage', 'user_credits')
              AND c.relrowsecurity
            THEN 'INSERT,UPDATE'
            ELSE 'SELECT,INSERT,UPDATE'
          END
        )
      )
    ORDER BY r.rolname, c.relname
    """
)


class DatabaseSchemaError(RuntimeError):
    """Raised when a deployed database fails a startup schema check."""


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


def data_api_exposure(engine: Engine) -> list[tuple[str, str]]:
    """List each role and table pair the Supabase Data API could reach.

    PostgreSQL without Supabase's anon and authenticated roles has no such
    pairs, and neither does a schema those roles cannot use.
    """
    with engine.connect() as connection:
        schema = configured_database_schema() or connection.scalar(
            text("SELECT current_schema()")
        )
        rows = connection.execute(_DATA_API_EXPOSURE, {"schema": schema})
        return [(row.rolname, row.relname) for row in rows]


def verify_database_schema(engine: Engine | None = None) -> str | None:
    """Require deployed PostgreSQL to match the repository Alembic revision.

    It must also keep the Supabase Data API out of application tables.

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

    exposed = data_api_exposure(database_engine)
    if exposed:
        pairs = ", ".join(f"{role} on {table}" for role, table in exposed)
        raise DatabaseSchemaError(
            f"The Supabase Data API can reach application tables: {pairs}. "
            "Run supabase/migrations/005_lock_down_data_api.sql before starting "
            "the backend."
        )

    logger.info("Database schema revision %s verified", current)
    return current
