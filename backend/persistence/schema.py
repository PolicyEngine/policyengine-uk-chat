"""Read-only validation of the deployed schema revision and Data API access."""

from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import NamedTuple

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from conversations.models import get_engine
from persistence.database_namespace import configured_database_schema

logger = logging.getLogger(__name__)

ALEMBIC_CONFIG_PATH = Path(__file__).resolve().parents[1] / "alembic.ini"
LOCKDOWN_MIGRATION = "supabase/migrations/005_lock_down_data_api.sql"

# Both queries below read catalogs only, so neither reads a table row.
#
# A relation counts when anon or authenticated holds any privilege on it or on
# one of its columns, directly or through PUBLIC. A routine counts when it runs
# with its owner's privileges (SECURITY DEFINER) and either role can execute
# it, since it can then reach rows the caller can't. Extension members don't
# count, since their extension, not this app, defines them.
_DATA_API_EXPOSURE = text(
    """
    SELECT
      r.rolname::text AS role,
      CASE c.relkind
        WHEN 'v' THEN 'view'
        WHEN 'm' THEN 'materialized view'
        WHEN 'f' THEN 'foreign table'
        ELSE 'table'
      END AS kind,
      c.relname::text AS name,
      pg_get_userbyid(c.relowner)::text AS owner
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    JOIN pg_roles AS r ON r.rolname IN ('anon', 'authenticated')
    WHERE n.nspname = :schema
      AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
      AND has_schema_privilege(r.oid, n.oid, 'USAGE')
      AND (
        has_any_column_privilege(r.oid, c.oid, 'SELECT,INSERT,UPDATE,REFERENCES')
        OR has_table_privilege(
          r.oid,
          c.oid,
          CASE
            WHEN current_setting('server_version_num')::int >= 170000
            THEN 'DELETE,TRUNCATE,TRIGGER,MAINTAIN'
            ELSE 'DELETE,TRUNCATE,TRIGGER'
          END
        )
      )
    UNION ALL
    SELECT
      r.rolname::text,
      CASE p.prokind WHEN 'p' THEN 'procedure' ELSE 'function' END,
      p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')',
      pg_get_userbyid(p.proowner)::text
    FROM pg_proc AS p
    JOIN pg_namespace AS n ON n.oid = p.pronamespace
    JOIN pg_roles AS r ON r.rolname IN ('anon', 'authenticated')
    WHERE n.nspname = :schema
      AND p.prosecdef
      AND has_schema_privilege(r.oid, n.oid, 'USAGE')
      AND has_function_privilege(r.oid, p.oid, 'EXECUTE')
      AND NOT EXISTS (
        SELECT 1
        FROM pg_depend AS d
        WHERE d.classid = 'pg_proc'::regclass
          AND d.objid = p.oid
          AND d.deptype = 'e'
      )
    ORDER BY role, name
    """
)

# PostgreSQL's check_enable_rls exempts superusers and BYPASSRLS roles from
# row-level security, and a role with its table owner's privileges unless the
# table forces row-level security. Any other role reads only the rows a policy
# admits, and the lockdown leaves no policy for the backend.
_ROW_SECURITY_GAPS = text(
    """
    SELECT
      c.relname::text AS name,
      pg_get_userbyid(c.relowner)::text AS owner,
      c.relforcerowsecurity AS forced
    FROM pg_class AS c
    JOIN pg_namespace AS n ON n.oid = c.relnamespace
    WHERE n.nspname = :schema
      AND c.relkind IN ('r', 'p')
      AND c.relrowsecurity
      AND NOT (
        SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user
      )
      AND (
        c.relforcerowsecurity
        OR NOT pg_has_role(current_user, c.relowner, 'USAGE')
      )
    ORDER BY name
    """
)


class DatabaseSchemaError(RuntimeError):
    """Raised when a deployed database fails a startup schema check."""


class DataApiExposure(NamedTuple):
    """An object in the checked schema that a Data API role can reach."""

    role: str
    kind: str
    name: str
    owner: str


class RowSecurityGap(NamedTuple):
    """A table whose row-level security applies to the backend's own role."""

    name: str
    owner: str
    forced: bool


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


def _connect(bind: Engine | Connection) -> AbstractContextManager[Connection]:
    """Use a given connection, and its open transaction, or open a new one."""
    if isinstance(bind, Connection):
        return nullcontext(bind)
    return bind.connect()


def _checked_schema(connection: Connection) -> str | None:
    return configured_database_schema() or connection.scalar(
        text("SELECT current_schema()")
    )


def data_api_exposure(bind: Engine | Connection) -> list[DataApiExposure]:
    """List each object the Supabase Data API's roles could reach.

    PostgreSQL without Supabase's anon and authenticated roles has no such
    objects, and neither does a schema those roles cannot use.
    """
    with _connect(bind) as connection:
        rows = connection.execute(
            _DATA_API_EXPOSURE, {"schema": _checked_schema(connection)}
        )
        return [
            DataApiExposure(row.role, row.kind, row.name, row.owner) for row in rows
        ]


def row_security_gaps(bind: Engine | Connection) -> list[RowSecurityGap]:
    """List tables whose row-level security applies to the connected role."""
    with _connect(bind) as connection:
        rows = connection.execute(
            _ROW_SECURITY_GAPS, {"schema": _checked_schema(connection)}
        )
        return [RowSecurityGap(row.name, row.owner, row.forced) for row in rows]


def verify_database_schema(engine: Engine | None = None) -> str | None:
    """Require deployed PostgreSQL to match the repository Alembic revision.

    The backend's role must also escape row-level security on every table that
    has it, and the Supabase Data API must reach nothing in the schema. The
    checks read catalogs and the Alembic revision, never application rows.

    SQLite is reserved for isolated repository tests, whose fixtures construct
    temporary schemas directly from SQLModel metadata.
    """
    database_engine = engine or get_engine()
    if database_engine.dialect.name == "sqlite":
        logger.info(
            "Skipping Alembic revision validation for an isolated SQLite test database"
        )
        return None

    # Row-level security on alembic_version would hide the revision too, so
    # this check runs first and names the real cause.
    gaps = row_security_gaps(database_engine)
    if gaps:
        tables = ", ".join(
            f"{gap.name} (owner {gap.owner}{', forced' if gap.forced else ''})"
            for gap in gaps
        )
        raise DatabaseSchemaError(
            "Row-level security would hide rows from the backend's database "
            f"role in {tables}. Connect as the tables' owner or as a role with "
            "BYPASSRLS, and don't force row-level security on them."
        )

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
        objects = ", ".join(
            f"{item.role} on {item.kind} {item.name} (owner {item.owner})"
            for item in exposed
        )
        raise DatabaseSchemaError(
            f"The Supabase Data API can reach {objects}. Run {LOCKDOWN_MIGRATION} "
            "as each object's owner before starting the backend; it skips "
            "objects the running role doesn't own."
        )

    logger.info("Database schema revision %s verified", current)
    return current
