"""Data API access tests against disposable PostgreSQL databases.

Each test builds a database the way Supabase does. A superuser, standing in for
Supabase's administrator, creates the anon, authenticated and service_role
roles, a stand-in auth schema, and an owner role that isn't a superuser. Like
Supabase's postgres role, the owner belongs to all three roles and owns the
database, and its default privileges grant the three roles every new object in
public. The owner runs the billing migrations, the Alembic upgrade and the
lockdown, and creates one object of each other kind. The owner has BYPASSRLS,
as Supabase's postgres does; the tests that depend on it also run with an owner
that lacks it, as on plain PostgreSQL.

These tests create and drop server-wide roles, so the configured server must
hold nothing valuable. Its user must be a superuser: the fixtures create roles
with BYPASSRLS, install file_fdw and hand objects between roles.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from select import select as wait_until_readable
from typing import Any, NamedTuple
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from hypothesis import HealthCheck, Phase, assume, example, given, settings
from hypothesis import strategies as st
from psycopg2 import sql
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Connection, Engine, make_url
from sqlalchemy.exc import ProgrammingError
from sqlmodel import Session, select

from conftest import requires_test_postgres
from conversations.models import ChatConversation
from conversation_context.models import ConversationContext
from persistence.context_repository import SQLConversationContextRepository
from persistence.database_namespace import namespaced_engine
from persistence.idempotency import (
    IdempotencyDecision,
    SQLIdempotencyRepository,
    request_fingerprint,
)
from persistence.migration_adoption import MANAGED_TABLES
from persistence.schema import (
    LOCKDOWN_MIGRATION,
    DatabaseSchemaError,
    DataApiExposure,
    RowSecurityGap,
    data_api_exposure,
    expected_schema_revision,
    row_security_gaps,
    verify_database_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = REPO_ROOT / "backend/alembic.ini"
SUPABASE_MIGRATIONS = REPO_ROOT / "supabase/migrations"
LOCKDOWN = REPO_ROOT / LOCKDOWN_MIGRATION
TESTS_WORKFLOW = REPO_ROOT / ".github/workflows/tests.yml"
POSTGRES_URL = os.environ.get("CAPABILITY_TEST_POSTGRES_URL")

pytestmark = requires_test_postgres

DATA_API_ROLES = ("anon", "authenticated")
ROLES = (*DATA_API_ROLES, "service_role")
BILLING_TABLES = {"token_usage", "user_credits"}
UNMANAGED_TABLE = "analysis_workflows"
PARTITIONED_TABLE = "analysis_runs"
PARTITION = "analysis_runs_2026"
VIEW = "conversation_titles"
MATERIALIZED_VIEW = "conversation_count"
FOREIGN_TABLE = "remote_rows"
DEFINER_FUNCTION = "count_conversations()"
ALL_TABLES = MANAGED_TABLES | BILLING_TABLES | {
    "alembic_version",
    UNMANAGED_TABLE,
    PARTITIONED_TABLE,
    PARTITION,
}
# The startup check's name for each kind of relation.
KIND_NAMES = {
    "r": "table",
    "p": "table",
    "v": "view",
    "m": "materialized view",
    "f": "foreign table",
}
COLUMN_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
FIRST_USER = "00000000-0000-0000-0000-00000000000a"
SECOND_USER = "00000000-0000-0000-0000-00000000000b"
USER_WITHOUT_CREDITS = "00000000-0000-0000-0000-00000000000c"


def _superuser_bootstrap(owner: str) -> str:
    """What Supabase's administrator sets up before its postgres role acts."""
    return f"""
    CREATE SCHEMA auth;
    CREATE TABLE auth.users (id uuid PRIMARY KEY);
    CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
      $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
    INSERT INTO auth.users
      VALUES ('{FIRST_USER}'), ('{SECOND_USER}'), ('{USER_WITHOUT_CREDITS}');
    GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role, "{owner}";
    GRANT REFERENCES ON auth.users TO "{owner}";
    GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

    -- Only a superuser may point file_fdw at a file, so the administrator
    -- creates the foreign table, hands it to the owner and grants it the way
    -- the owner's default privileges would.
    CREATE SCHEMA fixture_extensions;
    CREATE EXTENSION file_fdw SCHEMA fixture_extensions;
    CREATE SERVER fixture_server FOREIGN DATA WRAPPER file_fdw;
    CREATE FOREIGN TABLE public.{FOREIGN_TABLE} (id int)
      SERVER fixture_server OPTIONS (filename '/dev/null');
    ALTER FOREIGN TABLE public.{FOREIGN_TABLE} OWNER TO "{owner}";
    GRANT ALL ON public.{FOREIGN_TABLE} TO anon, authenticated, service_role;
    """


OWNER_BOOTSTRAP = """
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON ROUTINES TO anon, authenticated, service_role;
"""

SEED = f"""
INSERT INTO user_credits (user_id, balance_gbp)
  VALUES ('{FIRST_USER}', 5), ('{SECOND_USER}', 7);
INSERT INTO token_usage (user_id, session_id)
  VALUES ('{FIRST_USER}', 'first'), ('{SECOND_USER}', 'second');
INSERT INTO chat_conversations (session_id, title, messages, created_at, updated_at)
  VALUES ('seeded', 'Seeded', '[]', now(), now());
CREATE TABLE {UNMANAGED_TABLE} (id int PRIMARY KEY);
INSERT INTO {UNMANAGED_TABLE} VALUES (1);
CREATE TABLE {PARTITIONED_TABLE} (id int, ran_on date NOT NULL)
  PARTITION BY RANGE (ran_on);
CREATE TABLE {PARTITION} PARTITION OF {PARTITIONED_TABLE}
  FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
INSERT INTO {PARTITIONED_TABLE} VALUES (1, '2026-11-03');
CREATE VIEW {VIEW} AS SELECT title FROM chat_conversations;
CREATE MATERIALIZED VIEW {MATERIALIZED_VIEW} AS
  SELECT count(*) AS conversations FROM chat_conversations;
CREATE SEQUENCE standalone_sequence;
CREATE FUNCTION touches_no_table() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;
CREATE FUNCTION {DEFINER_FUNCTION} RETURNS bigint LANGUAGE sql
  SECURITY DEFINER AS $$ SELECT count(*) FROM public.chat_conversations $$;
CREATE PROCEDURE do_nothing() LANGUAGE plpgsql AS $$ BEGIN END $$;
CREATE FUNCTION "Echo"(value text) RETURNS text LANGUAGE sql AS $$ SELECT value $$;
CREATE FUNCTION "Echo"(value int) RETURNS int LANGUAGE sql AS $$ SELECT value $$;
CREATE FUNCTION total(VARIADIC numbers int[]) RETURNS int LANGUAGE sql
  AS $$ SELECT sum(n)::int FROM unnest(numbers) AS n $$;
CREATE AGGREGATE longest(text) (SFUNC = text_larger, STYPE = text);
-- Grants to PUBLIC that someone might add by hand.
GRANT SELECT ON {UNMANAGED_TABLE} TO PUBLIC;
GRANT UPDATE (title) ON chat_conversations TO PUBLIC;
GRANT USAGE ON SEQUENCE standalone_sequence TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE ON SEQUENCES TO PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON ROUTINES TO PUBLIC;
"""

# Default privileges in public that grant PUBLIC, anon or authenticated anything.
_DEFAULT_GRANTS = text(
    """
    SELECT DISTINCT
      d.defaclobjtype::text AS object_type,
      CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE a.grantee::regrole::text END
        AS grantee
    FROM pg_default_acl AS d
    CROSS JOIN LATERAL aclexplode(d.defaclacl) AS a
    WHERE d.defaclnamespace = 'public'::regnamespace
      AND (
        a.grantee = 0
        OR a.grantee IN (
          SELECT oid FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
        )
      )
    ORDER BY 1, 2
    """
)

# Each privilege type the server defines for each object in public, and whether
# anon or authenticated holds it. The has_*_privilege functions count grants to
# PUBLIC, column grants and NULL (default) ACLs. acldefault lists every
# privilege an owner gets, which is every privilege the server defines for that
# kind of object, so the list follows the server version.
_OBJECT_PRIVILEGES = text(
    """
    WITH objects AS (
      SELECT c.oid, c.relkind::text AS kind, c.oid::regclass::text AS object,
             CASE WHEN c.relkind = 'S' THEN 's' ELSE 'r' END AS acl_kind,
             c.relowner AS owner
      FROM pg_class AS c
      WHERE c.relnamespace = 'public'::regnamespace
        AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S')
      UNION ALL
      SELECT p.oid, 'routine', p.oid::regprocedure::text, 'f', p.proowner
      FROM pg_proc AS p
      WHERE p.pronamespace = 'public'::regnamespace
    )
    SELECT
      r.rolname::text AS role,
      o.kind,
      o.object,
      p.privilege_type::text AS privilege,
      CASE o.acl_kind
        WHEN 's' THEN has_sequence_privilege(r.oid, o.oid, p.privilege_type)
        WHEN 'f' THEN has_function_privilege(r.oid, o.oid, p.privilege_type)
        WHEN 'r' THEN CASE
          WHEN p.privilege_type IN ('SELECT', 'INSERT', 'UPDATE', 'REFERENCES')
          THEN has_any_column_privilege(r.oid, o.oid, p.privilege_type)
          ELSE has_table_privilege(r.oid, o.oid, p.privilege_type)
        END
      END AS held
    FROM objects AS o
    CROSS JOIN LATERAL (
      SELECT DISTINCT privilege_type
      FROM aclexplode(acldefault(o.acl_kind::"char", o.owner))
    ) AS p
    JOIN pg_roles AS r ON r.rolname IN ('anon', 'authenticated')
    ORDER BY role, o.kind, o.object, privilege
    """
)


class Privilege(NamedTuple):
    role: str
    kind: str
    object: str
    privilege: str


class Probe(NamedTuple):
    """A statement that needs one privilege, and the grant that supplies it."""

    label: str
    statement: str
    grant: str


@contextmanager
def _connect(bind: Engine | Connection) -> Iterator[Connection]:
    if isinstance(bind, Connection):
        yield bind
    else:
        with bind.connect() as connection:
            yield connection


def _object_privileges(bind: Engine | Connection) -> list[tuple[Privilege, bool]]:
    with _connect(bind) as connection:
        return [
            (Privilege(row.role, row.kind, row.object, row.privilege), row.held)
            for row in connection.execute(_OBJECT_PRIVILEGES)
        ]


def _data_api_access(bind: Engine | Connection) -> list[Privilege]:
    """Every privilege anon or authenticated holds on an object in public."""
    return [privilege for privilege, held in _object_privileges(bind) if held]


def _privilege_types(bind: Engine | Connection, acl_kind: str) -> set[str]:
    """The privilege types this server defines for tables, sequences or routines."""
    with _connect(bind) as connection:
        return set(
            connection.scalars(
                text(
                    "SELECT DISTINCT privilege_type::text FROM aclexplode("
                    'acldefault(CAST(:kind AS "char"), CAST(current_user AS regrole)))'
                ),
                {"kind": acl_kind},
            )
        )


def _run_sql(engine: Engine, statements: str | sql.Composable) -> list[str]:
    """Run a whole file through the driver's simple protocol, as psql would.

    Returns the notices and warnings PostgreSQL sent while it ran.
    """
    connection = engine.raw_connection()
    try:
        driver: Any = connection.driver_connection
        driver.autocommit = True
        # psycopg2 keeps only the last 50 notices in its default list.
        driver.notices = deque()
        cursor = connection.cursor()
        cursor.execute(statements)
        cursor.close()
        return [notice.strip() for notice in driver.notices]
    finally:
        connection.close()


def _warnings(notices: list[str]) -> list[str]:
    return [notice for notice in notices if notice.startswith("WARNING:")]


@contextmanager
def _admin() -> Iterator[Connection]:
    if not POSTGRES_URL:
        pytest.fail(
            "CAPABILITY_TEST_POSTGRES_REQUIRED=1, but CAPABILITY_TEST_POSTGRES_URL "
            "is not set",
            pytrace=False,
        )
    engine = create_engine(
        POSTGRES_URL,
        isolation_level="AUTOCOMMIT",
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as connection:
            yield connection
    finally:
        engine.dispose()


def _url(database: str, role: str | None = None) -> str:
    """Connect as the configured user, acting as `role` when one is given."""
    assert POSTGRES_URL is not None
    url = make_url(POSTGRES_URL).set(database=database)
    if role is not None:
        url = url.update_query_dict({"options": f"-c role={role}"})
    return url.render_as_string(hide_password=False)


def _engine(database: str, role: str | None = None) -> Engine:
    return create_engine(_url(database, role), poolclass=pool.NullPool)


@contextmanager
def _temporary_database(owner: str | None = None) -> Iterator[str]:
    name = f"uk_chat_lockdown_{uuid4().hex[:12]}"
    owned_by = f' OWNER "{owner}"' if owner else ""
    with _admin() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"{owned_by}'))
    try:
        yield name
    finally:
        with _admin() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
def data_api_roles() -> Iterator[None]:
    """Create the Supabase roles for one test, keeping any that already exist."""
    with _admin() as connection:
        existing = set(
            connection.scalars(
                text("SELECT rolname FROM pg_roles WHERE rolname = ANY(:names)"),
                {"names": list(ROLES)},
            )
        )
        created = [role for role in ROLES if role not in existing]
        for role in created:
            attributes = "BYPASSRLS" if role == "service_role" else ""
            connection.execute(text(f"CREATE ROLE {role} NOLOGIN NOINHERIT {attributes}"))
    try:
        yield
    finally:
        with _admin() as connection:
            for role in created:
                connection.execute(text(f"DROP ROLE {role}"))


@pytest.fixture
def server_roles(data_api_roles) -> Iterator[Callable[[str, str], str]]:
    """Create uniquely named roles, dropped after the test's database."""
    del data_api_roles
    created: list[str] = []

    def create(label: str, attributes: str) -> str:
        name = f"uk_chat_{label}_{uuid4().hex[:8]}"
        with _admin() as connection:
            connection.execute(text(f'CREATE ROLE "{name}" NOLOGIN {attributes}'))
        created.append(name)
        return name

    try:
        yield create
    finally:
        with _admin() as connection:
            for name in reversed(created):
                connection.execute(text(f'DROP ROLE "{name}"'))


@pytest.fixture
def owner_bypasses_rls() -> bool:
    """Supabase's postgres role has BYPASSRLS; tests may parametrize this."""
    return True


@pytest.fixture
def owner(server_roles, owner_bypasses_rls: bool) -> str:
    """The role that owns the app's objects and runs the lockdown."""
    bypass = "BYPASSRLS" if owner_bypasses_rls else "NOBYPASSRLS"
    return server_roles(
        "owner",
        f"NOSUPERUSER {bypass} IN ROLE anon, authenticated, service_role",
    )


@pytest.fixture
def exposed_database(monkeypatch, owner: str) -> Iterator[Engine]:
    """A database at the Alembic head before the lockdown, reached as the owner."""
    monkeypatch.delenv("DATABASE_SCHEMA", raising=False)
    with _temporary_database(owner) as name:
        engine = _engine(name, owner)
        administrator = _engine(name)
        try:
            monkeypatch.setenv("ALEMBIC_DATABASE_URL", _url(name, owner))
            _run_sql(administrator, _superuser_bootstrap(owner))
            _run_sql(engine, OWNER_BOOTSTRAP)
            for migration in ("001_billing.sql", "003_token_usage_model_fields.sql"):
                _run_sql(engine, (SUPABASE_MIGRATIONS / migration).read_text())
            command.upgrade(Config(str(ALEMBIC_CONFIG)), "head")
            _run_sql(engine, SEED)
            yield engine
        finally:
            engine.dispose()
            administrator.dispose()


@pytest.fixture
def superuser(exposed_database: Engine) -> Iterator[Engine]:
    """The configured server user, on the same database as the owner."""
    assert exposed_database.url.database is not None
    engine = _engine(exposed_database.url.database)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def locked_database(exposed_database: Engine) -> Engine:
    assert _warnings(_run_sql(exposed_database, LOCKDOWN.read_text())) == []
    return exposed_database


def _public_tables(bind: Engine | Connection) -> dict[str, str]:
    """Map every table in public to a column that a probe can set to DEFAULT."""
    with _connect(bind) as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname::text AS relname, min(a.attname::text) AS column_name
                FROM pg_class AS c
                JOIN pg_attribute AS a ON a.attrelid = c.oid
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relkind IN ('r', 'p')
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                  AND a.attidentity = ''
                  AND a.attgenerated = ''
                GROUP BY c.relname
                """
            )
        )
        return {row.relname: row.column_name for row in rows}


def _table_statements(table: str, column: str) -> dict[str, str]:
    """One statement per action; the writes match no row."""
    return {
        "SELECT": f'SELECT * FROM public."{table}" LIMIT 1',
        "INSERT": f'INSERT INTO public."{table}" ("{column}") SELECT NULL WHERE false',
        "UPDATE": f'UPDATE public."{table}" SET "{column}" = DEFAULT WHERE false',
        "DELETE": f'DELETE FROM public."{table}" WHERE false',
        "TRUNCATE": f'TRUNCATE public."{table}" CASCADE',
    }


def _probes(connection: Connection) -> list[Probe]:
    """Probes for every table, view, materialized view, foreign table and
    sequence in public, and for one function, definer function and procedure."""
    probes = []
    for table, column in _public_tables(connection).items():
        for action, statement in _table_statements(table, column).items():
            grant = f'GRANT {action} ON public."{table}" TO {{role}}'
            if action == "TRUNCATE":
                # CASCADE also truncates, and so needs TRUNCATE on, every
                # table that references this one.
                grant = "GRANT TRUNCATE ON ALL TABLES IN SCHEMA public TO {role}"
            probes.append(Probe(f"{action} {table}", statement, grant))
    for relation in (VIEW, MATERIALIZED_VIEW, FOREIGN_TABLE):
        probes.append(
            Probe(
                f"SELECT {relation}",
                f'SELECT * FROM public."{relation}" LIMIT 1',
                f'GRANT SELECT ON public."{relation}" TO {{role}}',
            )
        )
    sequences = connection.scalars(
        text(
            "SELECT relname::text FROM pg_class "
            "WHERE relnamespace = 'public'::regnamespace AND relkind = 'S'"
        )
    ).all()
    for sequence in sequences:
        probes.append(
            Probe(
                f"nextval {sequence}",
                f"SELECT nextval('public.\"{sequence}\"')",
                f'GRANT USAGE ON SEQUENCE public."{sequence}" TO {{role}}',
            )
        )
        probes.append(
            Probe(
                f"SELECT {sequence}",
                f'SELECT last_value FROM public."{sequence}"',
                f'GRANT SELECT ON SEQUENCE public."{sequence}" TO {{role}}',
            )
        )
    for routine, call in (
        ("touches_no_table()", "SELECT public.touches_no_table()"),
        (DEFINER_FUNCTION, f"SELECT public.{DEFINER_FUNCTION}"),
        ("do_nothing()", "CALL public.do_nothing()"),
    ):
        probes.append(
            Probe(
                f"EXECUTE {routine}",
                call,
                f"GRANT EXECUTE ON ROUTINE public.{routine} TO {{role}}",
            )
        )
    return probes


@contextmanager
def _acting_as(
    connection: Connection,
    role: str,
    *,
    user: str = "",
    grant: str | None = None,
) -> Iterator[None]:
    """Act as a role, with PostgREST's user claim, inside a savepoint.

    A grant runs first, as the connection's own role, and the rollback undoes
    it with everything else.
    """
    savepoint = connection.begin_nested()
    try:
        if grant is not None:
            connection.execute(text(grant))
        connection.execute(
            text("SELECT set_config('request.jwt.claim.sub', :user, true)"),
            {"user": user},
        )
        connection.execute(text(f"SET LOCAL ROLE {role}"))
        yield
    finally:
        savepoint.rollback()


def _denied(
    connection: Connection,
    role: str,
    statement: str,
    *,
    user: str = "",
    grant: str | None = None,
) -> bool:
    """Report whether PostgreSQL refuses a statement for a role, changing nothing."""
    try:
        with _acting_as(connection, role, user=user, grant=grant):
            connection.execute(text(statement))
    except ProgrammingError as error:
        if getattr(error.orig, "pgcode", None) == "42501":
            return True
        raise
    return False


def _rows_as(
    connection: Connection,
    role: str,
    statement: str,
    *,
    user: str = "",
    grant: str | None = None,
) -> list[Any]:
    with _acting_as(connection, role, user=user, grant=grant):
        return list(connection.execute(text(statement)))


def _access_snapshot(engine: Engine) -> dict[str, list[tuple[Any, ...]]]:
    queries = {
        "relations": """
            SELECT c.oid::regclass::text, c.relkind::text, c.relowner::regrole::text,
                   c.relrowsecurity, c.relforcerowsecurity, c.relacl::text
            FROM pg_class AS c
            WHERE c.relnamespace = 'public'::regnamespace
            ORDER BY 1
        """,
        "columns": """
            SELECT a.attrelid::regclass::text, a.attname::text, a.attacl::text
            FROM pg_attribute AS a
            JOIN pg_class AS c ON c.oid = a.attrelid
            WHERE c.relnamespace = 'public'::regnamespace AND a.attacl IS NOT NULL
            ORDER BY 1, 2
        """,
        "policies": """
            SELECT polrelid::regclass::text, polname::text, polcmd::text,
                   polpermissive, polroles::regrole[]::text,
                   pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid)
            FROM pg_policy
            ORDER BY 1, 2
        """,
        "routines": """
            SELECT p.oid::regprocedure::text, p.proowner::regrole::text, p.proacl::text
            FROM pg_proc AS p
            WHERE p.pronamespace = 'public'::regnamespace
            ORDER BY 1
        """,
        "default privileges": """
            SELECT defaclrole::regrole::text, defaclnamespace::regnamespace::text,
                   defaclobjtype::text, defaclacl::text
            FROM pg_default_acl
            ORDER BY 1, 2, 3
        """,
        "schema": "SELECT nspacl::text FROM pg_namespace WHERE nspname = 'public'",
    }
    with engine.connect() as connection:
        return {
            name: [tuple(row) for row in connection.execute(text(query))]
            for name, query in queries.items()
        }


def _head() -> str:
    return expected_schema_revision(ALEMBIC_CONFIG)


def test_privilege_lists_follow_the_server_version(exposed_database):
    """The closure checks below take their privilege lists from the server."""
    with exposed_database.connect() as connection:
        version = connection.scalar(text("SELECT current_setting('server_version_num')::int"))
        tables = _privilege_types(connection, "r")
        sequences = _privilege_types(connection, "s")
        routines = _privilege_types(connection, "f")
    expected = {"SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"}
    if version >= 170000:
        expected.add("MAINTAIN")
    assert tables == expected
    assert sequences == {"SELECT", "UPDATE", "USAGE"}
    assert routines == {"EXECUTE"}


def test_fixture_starts_open_to_both_roles(exposed_database, owner):
    """Without this, the closure tests below could pass on a closed fixture."""
    assert set(_public_tables(exposed_database)) == ALL_TABLES
    privileges = _object_privileges(exposed_database)
    assert {privilege.kind for privilege, _ in privileges} == {
        "r",
        "p",
        "v",
        "m",
        "f",
        "S",
        "routine",
    }
    assert [privilege for privilege, held in privileges if not held] == []

    with exposed_database.connect() as connection:
        relations = connection.execute(
            text(
                "SELECT relname::text, relkind::text FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace "
                "AND relkind IN ('r', 'p', 'v', 'm', 'f')"
            )
        ).all()
    expected = [
        DataApiExposure(role, KIND_NAMES[kind], name, owner)
        for role in DATA_API_ROLES
        for name, kind in relations
    ] + [
        DataApiExposure(role, "function", DEFINER_FUNCTION, owner)
        for role in DATA_API_ROLES
    ]
    assert sorted(data_api_exposure(exposed_database)) == sorted(expected)
    with pytest.raises(DatabaseSchemaError, match=LOCKDOWN_MIGRATION):
        verify_database_schema(exposed_database)

    # Default privileges for tables (r), sequences (S) and routines (f).
    with exposed_database.connect() as connection:
        assert sorted(tuple(row) for row in connection.execute(_DEFAULT_GRANTS)) == sorted(
            (object_type, grantee)
            for object_type in ("S", "f", "r")
            for grantee in ("PUBLIC", "anon", "authenticated")
        )


def test_lockdown_leaves_both_roles_no_privilege_in_public(exposed_database):
    assert _warnings(_run_sql(exposed_database, LOCKDOWN.read_text())) == []
    assert _data_api_access(exposed_database) == []
    assert data_api_exposure(exposed_database) == []
    with exposed_database.connect() as connection:
        assert connection.execute(_DEFAULT_GRANTS).all() == []
        # The lockdown changes default privileges in public only.
        assert connection.scalars(
            text("SELECT defaclobjtype::text FROM pg_default_acl WHERE defaclnamespace = 0")
        ).all() == []


def test_each_probe_needs_its_grant_and_the_lockdown_denies_it(locked_database):
    with locked_database.connect() as connection:
        probes = _probes(connection)
        probed = {probe.label.split(" ", 1)[1] for probe in probes}
        assert probed >= ALL_TABLES | {VIEW, MATERIALIZED_VIEW, FOREIGN_TABLE}
        for role in DATA_API_ROLES:
            for probe in probes:
                grant = probe.grant.format(role=role)
                assert _denied(connection, role, probe.statement), f"{role}: {probe.label}"
                assert not _denied(
                    connection, role, probe.statement, grant=grant
                ), f"{role}: {probe.label} still refused after {grant}"


def test_lockdown_turns_on_row_level_security_for_every_table(locked_database):
    with locked_database.connect() as connection:
        without_rls = connection.scalars(
            text(
                """
                SELECT c.relname
                FROM pg_class AS c
                WHERE c.relnamespace = 'public'::regnamespace
                  AND c.relkind IN ('r', 'p')
                  AND NOT c.relrowsecurity
                """
            )
        ).all()
    assert without_rls == []


def test_only_the_own_row_read_policies_remain(locked_database):
    with locked_database.connect() as connection:
        policies = connection.execute(
            text(
                """
                SELECT tablename::text, policyname::text, cmd, roles::text
                FROM pg_policies
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            )
        )
        assert [tuple(row) for row in policies] == [
            ("token_usage", "Users read own usage", "SELECT", "{public}"),
            ("user_credits", "Users read own credits", "SELECT", "{public}"),
        ]


def test_own_row_policies_limit_a_select_grant_added_back(locked_database, owner):
    with locked_database.connect() as connection:
        for table in BILLING_TABLES:
            statement = f"SELECT user_id FROM public.{table}"
            for user in (FIRST_USER, SECOND_USER):
                rows = _rows_as(
                    connection,
                    "authenticated",
                    statement,
                    user=user,
                    grant=f"GRANT SELECT ON public.{table} TO authenticated",
                )
                assert [str(row.user_id) for row in rows] == [user]
            for role in DATA_API_ROLES:
                rows = _rows_as(
                    connection,
                    role,
                    statement,
                    grant=f"GRANT SELECT ON public.{table} TO {role}",
                )
                assert rows == []

    _run_sql(locked_database, "GRANT SELECT ON public.user_credits TO authenticated")
    assert data_api_exposure(locked_database) == [
        DataApiExposure("authenticated", "table", "user_credits", owner)
    ]


def test_service_role_keeps_the_billing_access_the_backend_uses(locked_database):
    """billing/credits.py and billing/routes.py, through the service role key."""
    with locked_database.connect() as connection:
        with _acting_as(connection, "service_role"):
            credits = connection.execute(
                text(f"SELECT * FROM public.user_credits WHERE user_id = '{FIRST_USER}'")
            ).one()
            assert credits.balance_gbp == 5
            inserted = connection.execute(
                text(
                    "INSERT INTO public.user_credits (user_id) "
                    f"VALUES ('{USER_WITHOUT_CREDITS}') RETURNING user_id"
                )
            ).one()
            assert str(inserted.user_id) == USER_WITHOUT_CREDITS
            updated = connection.execute(
                text(
                    "UPDATE public.user_credits SET balance_gbp = 9 "
                    f"WHERE user_id = '{FIRST_USER}' RETURNING balance_gbp"
                )
            ).one()
            assert updated.balance_gbp == 9
            connection.execute(
                text(
                    "INSERT INTO public.token_usage (user_id, session_id, model) "
                    f"VALUES ('{FIRST_USER}', 'service', 'claude')"
                )
            )
            usage = connection.execute(
                text(
                    "SELECT * FROM public.token_usage "
                    f"WHERE user_id = '{FIRST_USER}' "
                    "ORDER BY created_at DESC LIMIT 50"
                )
            ).all()
            assert [row.session_id for row in usage] == ["service", "first"]


@pytest.mark.parametrize(
    "owner_bypasses_rls", [True, False], ids=["bypassrls", "nobypassrls"]
)
def test_backend_repositories_keep_working_as_the_owner(locked_database):
    assert verify_database_schema(locked_database) == _head()

    conversation_id = "owner-flow"
    disposable_id = "owner-flow-disposable"
    now = datetime.now(timezone.utc)
    with Session(locked_database) as session:
        for session_id in (conversation_id, disposable_id):
            session.add(
                ChatConversation(
                    session_id=session_id,
                    title="Owner flow",
                    messages=[],
                    created_at=now,
                    updated_at=now,
                )
            )
        session.commit()

    def conversation(session: Session, session_id: str) -> ChatConversation:
        return session.exec(
            select(ChatConversation).where(ChatConversation.session_id == session_id)
        ).one()

    with Session(locked_database) as session:
        assert conversation(session, "seeded").title == "Seeded"
        conversation(session, conversation_id).title = "Renamed"
        session.delete(conversation(session, disposable_id))
        session.commit()
    with Session(locked_database) as session:
        assert conversation(session, conversation_id).title == "Renamed"

    contexts = SQLConversationContextRepository(engine=locked_database)
    saved = contexts.save(
        ConversationContext.initial(conversation_id),
        expected_revision=0,
    )
    assert contexts.load(conversation_id) == saved

    receipts = SQLIdempotencyRepository(engine=locked_database)
    assert (
        receipts.begin_turn(
            conversation_id=conversation_id,
            turn_id="owner-flow-turn",
            fingerprint=request_fingerprint({"request": "owner-flow"}),
        ).decision
        is IdempotencyDecision.STARTED
    )


def test_lockdown_keeps_every_row(exposed_database, superuser):
    def counts() -> dict[str, int]:
        with superuser.connect() as connection:
            return {
                table: connection.scalar(text(f'SELECT count(*) FROM public."{table}"'))
                for table in _public_tables(connection)
            }

    before = counts()
    _run_sql(exposed_database, LOCKDOWN.read_text())
    assert counts() == before
    assert before["chat_conversations"] == 1
    assert before["user_credits"] == 2
    assert before[UNMANAGED_TABLE] == 1
    assert before[PARTITIONED_TABLE] == 1


def test_lockdown_can_run_again_without_changing_access(locked_database):
    before = _access_snapshot(locked_database)
    assert before["routines"]
    assert before["default privileges"]
    assert _warnings(_run_sql(locked_database, LOCKDOWN.read_text())) == []
    assert _access_snapshot(locked_database) == before


def test_objects_created_later_until_the_rerun(locked_database, owner):
    _run_sql(
        locked_database,
        """
        CREATE TABLE added_later (id serial PRIMARY KEY);
        CREATE VIEW added_later_view AS SELECT id FROM added_later;
        CREATE SEQUENCE added_later_sequence;
        CREATE FUNCTION added_later_invoker() RETURNS int LANGUAGE sql
          AS $$ SELECT 1 $$;
        CREATE FUNCTION added_later_definer() RETURNS bigint LANGUAGE sql
          SECURITY DEFINER AS $$ SELECT count(*) FROM public.chat_conversations $$;
        """,
    )
    # Relations and sequences get no grant for either role. Each new function
    # gets PostgreSQL's default EXECUTE for PUBLIC.
    assert _data_api_access(locked_database) == [
        Privilege(role, "routine", function, "EXECUTE")
        for role in DATA_API_ROLES
        for function in ("added_later_definer()", "added_later_invoker()")
    ]
    with locked_database.connect() as connection:
        assert connection.scalar(
            text("SELECT has_table_privilege('service_role', 'added_later', 'SELECT')")
        )
        # The definer function reads rows anon can't read directly.
        assert _denied(connection, "anon", "SELECT count(*) FROM public.chat_conversations")
        assert _rows_as(connection, "anon", "SELECT public.added_later_definer() AS n")[0].n == 1

    assert data_api_exposure(locked_database) == [
        DataApiExposure(role, "function", "added_later_definer()", owner)
        for role in DATA_API_ROLES
    ]
    with pytest.raises(
        DatabaseSchemaError, match=r"anon on function added_later_definer\(\)"
    ):
        verify_database_schema(locked_database)

    assert _warnings(_run_sql(locked_database, LOCKDOWN.read_text())) == []
    assert _data_api_access(locked_database) == []
    with locked_database.connect() as connection:
        assert connection.scalar(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = 'added_later'")
        )
    assert verify_database_schema(locked_database) == _head()


@pytest.mark.parametrize(
    "owner_bypasses_rls", [True, False], ids=["bypassrls", "nobypassrls"]
)
def test_lockdown_skips_objects_another_role_owns_and_commits_the_rest(
    exposed_database, superuser, server_roles, owner
):
    other = server_roles("other", "NOSUPERUSER")
    # The owner inherits this role's privileges, so the lockdown treats its
    # objects as the owner's own.
    delegate = server_roles("delegate", "NOSUPERUSER")
    _run_sql(
        superuser,
        f"""
        GRANT CREATE ON SCHEMA public TO "{other}", "{delegate}";
        GRANT "{delegate}" TO "{owner}";
        SET ROLE "{delegate}";
        CREATE TABLE public.delegated_notes (id int PRIMARY KEY);
        GRANT SELECT ON public.delegated_notes TO anon;
        SET ROLE "{other}";
        CREATE TABLE public.admin_notes (id int PRIMARY KEY);
        GRANT SELECT ON public.admin_notes TO anon;
        CREATE FUNCTION public.admin_definer() RETURNS int LANGUAGE sql
          SECURITY DEFINER AS $$ SELECT 1 $$;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO anon;
        RESET ROLE;
        """,
    )

    notices = _run_sql(exposed_database, LOCKDOWN.read_text())
    assert _warnings(notices) == [
        f"WARNING:  skipped admin_notes (owner {other}): the running role does not own it",
        f"WARNING:  skipped admin_definer() (owner {other}): the running role does "
        "not own it",
    ]
    # Everything the owner holds closed in the same run.
    assert {
        (privilege.role, privilege.object) for privilege in _data_api_access(exposed_database)
    } == {
        ("anon", "admin_notes"),
        ("anon", "admin_definer()"),
        ("authenticated", "admin_definer()"),
    }
    with exposed_database.connect() as connection:
        assert connection.scalars(
            text(
                "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                "AND relkind IN ('r', 'p') AND NOT relrowsecurity"
            )
        ).all() == ["admin_notes"]
    # admin_notes has no row-level security, so it hides nothing from the owner.
    assert row_security_gaps(exposed_database) == []

    assert sorted(data_api_exposure(exposed_database)) == [
        DataApiExposure("anon", "function", "admin_definer()", other),
        DataApiExposure("anon", "table", "admin_notes", other),
        DataApiExposure("authenticated", "function", "admin_definer()", other),
    ]
    with pytest.raises(DatabaseSchemaError, match=rf"admin_notes \(owner {other}\)"):
        verify_database_schema(exposed_database)

    # The lockdown changes only the running role's default privileges.
    _run_sql(superuser, f'SET ROLE "{other}"; CREATE TABLE public.admin_notes_later (id int)')
    assert DataApiExposure(
        "anon", "table", "admin_notes_later", other
    ) in data_api_exposure(exposed_database)


IDENTIFIERS = st.text(
    st.characters(exclude_categories=("Cs",), exclude_characters="\x00"),
    min_size=1,
    max_size=20,
).filter(lambda name: len(name.encode()) <= 63)


@settings(
    max_examples=25,
    deadline=None,
    database=None,
    derandomize=True,
    phases=(Phase.explicit, Phase.generate),
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@example(names=["text", "Mixed Case", 'quote"inside', "select"])
@example(names=["%s", "a.b", " ", "0"])
@given(names=st.lists(IDENTIFIERS, min_size=4, max_size=4, unique=True))
def test_lockdown_closes_objects_whatever_their_names(locked_database, names):
    """The lockdown quotes names through regclass and regprocedure."""
    table, view, sequence, function = names
    with locked_database.connect() as connection:
        existing = set(
            connection.scalars(
                text(
                    "SELECT relname::text FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace"
                )
            )
        )
    assume(not {table, view, sequence} & existing)
    identifiers = {
        "table": sql.Identifier("public", table),
        "view": sql.Identifier("public", view),
        "sequence": sql.Identifier("public", sequence),
        "function": sql.Identifier("public", function),
    }
    _run_sql(
        locked_database,
        sql.SQL(
            """
            CREATE TABLE {table} (id int);
            CREATE VIEW {view} AS SELECT id FROM {table};
            CREATE SEQUENCE {sequence};
            CREATE FUNCTION {function}(item {table}) RETURNS int LANGUAGE sql
              SECURITY DEFINER AS 'SELECT 1';
            GRANT ALL ON {table}, {view} TO anon, authenticated, PUBLIC;
            GRANT ALL ON SEQUENCE {sequence} TO anon, authenticated, PUBLIC;
            GRANT ALL ON FUNCTION {function}({table}) TO anon, authenticated, PUBLIC;
            """
        ).format(**identifiers),
    )
    try:
        assert len({privilege.object for privilege in _data_api_access(locked_database)}) == 4

        assert _warnings(_run_sql(locked_database, LOCKDOWN.read_text())) == []

        assert _data_api_access(locked_database) == []
        with locked_database.connect() as connection:
            assert connection.scalar(
                text(
                    "SELECT relrowsecurity FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace AND relname = :name"
                ),
                {"name": table},
            )
    finally:
        _run_sql(
            locked_database,
            sql.SQL("DROP TABLE {table} CASCADE; DROP SEQUENCE {sequence}").format(
                **identifiers
            ),
        )


def test_alembic_lifecycle_runs_on_a_locked_database(locked_database):
    config = Config(str(ALEMBIC_CONFIG))
    assert verify_database_schema(locked_database) == _head()

    command.check(config)
    command.downgrade(config, "-1")
    command.upgrade(config, "head")

    assert verify_database_schema(locked_database) == _head()
    assert data_api_exposure(locked_database) == []
    with locked_database.connect() as connection:
        tables = _public_tables(connection)
        for table in MANAGED_TABLES:
            for statement in _table_statements(table, tables[table]).values():
                assert _denied(connection, "anon", statement)


def test_startup_check_reports_every_privilege_on_every_kind_of_relation(
    locked_database, owner
):
    relations = {
        UNMANAGED_TABLE: "table",
        PARTITIONED_TABLE: "table",
        VIEW: "view",
        MATERIALIZED_VIEW: "materialized view",
        FOREIGN_TABLE: "foreign table",
    }
    with locked_database.connect() as connection:
        table_privileges = sorted(_privilege_types(connection, "r"))
        assert data_api_exposure(connection) == []
        for relation, kind in relations.items():
            column = connection.scalar(
                text(
                    "SELECT min(attname::text) FROM pg_attribute "
                    "WHERE attrelid = CAST(:relation AS regclass) AND attnum > 0"
                ),
                {"relation": f"public.{relation}"},
            )
            grants = [
                f"GRANT {privilege} ON public.{relation} TO {{grantee}}"
                for privilege in table_privileges
            ] + [
                f"GRANT {privilege} ({column}) ON public.{relation} TO {{grantee}}"
                for privilege in COLUMN_PRIVILEGES
            ]
            for grantee in (*DATA_API_ROLES, "PUBLIC"):
                reached = DATA_API_ROLES if grantee == "PUBLIC" else (grantee,)
                for grant in grants:
                    statement = grant.format(grantee=grantee)
                    savepoint = connection.begin_nested()
                    try:
                        connection.execute(text(statement))
                        assert data_api_exposure(connection) == [
                            DataApiExposure(role, kind, relation, owner)
                            for role in reached
                        ], statement
                    finally:
                        savepoint.rollback()
        assert data_api_exposure(connection) == []


def test_startup_check_reports_definer_routines_either_role_can_execute(
    locked_database, superuser, owner
):
    _run_sql(
        locked_database,
        """
        CREATE FUNCTION later_invoker() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;
        CREATE FUNCTION later_definer() RETURNS int LANGUAGE sql
          SECURITY DEFINER AS $$ SELECT 1 $$;
        CREATE PROCEDURE later_procedure() LANGUAGE plpgsql
          SECURITY DEFINER AS $$ BEGIN END $$;
        REVOKE ALL ON ROUTINE later_invoker(), later_definer(), later_procedure()
          FROM PUBLIC;
        """,
    )
    # A definer function that belongs to an extension doesn't count, even
    # with PUBLIC holding EXECUTE.
    _run_sql(
        superuser,
        """
        CREATE FUNCTION public.extension_definer() RETURNS int LANGUAGE sql
          SECURITY DEFINER AS $$ SELECT 1 $$;
        ALTER EXTENSION plpgsql ADD FUNCTION public.extension_definer();
        """,
    )
    with superuser.connect() as connection:
        assert connection.scalar(
            text("SELECT has_function_privilege('anon', 'public.extension_definer()', 'EXECUTE')")
        )
    assert data_api_exposure(locked_database) == []

    reported = {"later_definer()": "function", "later_procedure()": "procedure"}
    with locked_database.connect() as connection:
        for grantee in (*DATA_API_ROLES, "PUBLIC"):
            reached = DATA_API_ROLES if grantee == "PUBLIC" else (grantee,)
            for routine in ("later_invoker()", *reported):
                savepoint = connection.begin_nested()
                try:
                    connection.execute(
                        text(f"GRANT EXECUTE ON ROUTINE public.{routine} TO {grantee}")
                    )
                    expected = [
                        DataApiExposure(role, reported[routine], routine, owner)
                        for role in reached
                        if routine in reported
                    ]
                    assert data_api_exposure(connection) == expected, (grantee, routine)
                finally:
                    savepoint.rollback()


# Runtime roles for the backend, by the attributes the startup check reads.
LAYOUTS: dict[str, str | None] = {
    # The layout a missing check let through: the role owns alembic_version,
    # so it reads the revision, but not the tables it serves.
    "owns_alembic_version_only": "NOSUPERUSER NOBYPASSRLS",
    "owns_nothing": "NOSUPERUSER NOBYPASSRLS",
    "noinherit_member_of_owner": "NOSUPERUSER NOBYPASSRLS NOINHERIT IN ROLE {owner}",
    "inherit_member_of_owner": "NOSUPERUSER NOBYPASSRLS INHERIT IN ROLE {owner}",
    "bypassrls_without_ownership": "NOSUPERUSER BYPASSRLS",
    "superuser": None,
}


@pytest.mark.parametrize("layout", LAYOUTS)
def test_startup_check_names_the_tables_row_level_security_hides(
    locked_database, superuser, server_roles, owner, layout
):
    attributes = LAYOUTS[layout]
    runtime = None
    if attributes is not None:
        runtime = server_roles("runtime", attributes.format(owner=f'"{owner}"'))
        _run_sql(
            superuser,
            f"""
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public
              TO "{runtime}";
            GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{runtime}";
            """,
        )
    if layout == "owns_alembic_version_only":
        _run_sql(superuser, f'ALTER TABLE public.alembic_version OWNER TO "{runtime}"')

    with superuser.connect() as connection:
        protected = set(
            connection.scalars(
                text(
                    "SELECT relname::text FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace "
                    "AND relkind IN ('r', 'p') AND relrowsecurity"
                )
            )
        )
        rows = {
            table: connection.scalar(text(f'SELECT count(*) FROM public."{table}"'))
            for table in protected
        }
    assert protected == ALL_TABLES

    expected = {
        "owns_alembic_version_only": protected - {"alembic_version"},
        "owns_nothing": protected,
        "noinherit_member_of_owner": protected,
        "inherit_member_of_owner": set(),
        "bypassrls_without_ownership": set(),
        "superuser": set(),
    }[layout]
    assert superuser.url.database is not None
    runtime_engine = _engine(superuser.url.database, runtime)
    try:
        assert sorted(row_security_gaps(runtime_engine)) == sorted(
            RowSecurityGap(table, owner, False) for table in expected
        )
        with runtime_engine.connect() as connection:
            visible = {
                table: connection.scalar(text(f'SELECT count(*) FROM public."{table}"'))
                for table in protected
            }
        # The check names exactly the tables whose rows this role can't see.
        populated = {table for table, count in rows.items() if count}
        assert len(populated) >= 5
        assert {table for table in populated if visible[table] < rows[table]} == (
            expected & populated
        )

        if expected:
            with pytest.raises(
                DatabaseSchemaError, match="Row-level security would hide rows"
            ) as raised:
                verify_database_schema(runtime_engine)
            for table in expected:
                assert f"{table} (owner {owner})" in str(raised.value)
        else:
            assert verify_database_schema(runtime_engine) == _head()
    finally:
        runtime_engine.dispose()


@pytest.mark.parametrize(
    "owner_bypasses_rls", [True, False], ids=["bypassrls", "nobypassrls"]
)
def test_forced_row_level_security_hides_rows_only_from_an_owner_without_bypassrls(
    locked_database, owner, owner_bypasses_rls
):
    _run_sql(locked_database, "ALTER TABLE public.chat_conversations FORCE ROW LEVEL SECURITY")
    with locked_database.connect() as connection:
        visible = connection.scalar(text("SELECT count(*) FROM public.chat_conversations"))
    if owner_bypasses_rls:
        assert visible == 1
        assert row_security_gaps(locked_database) == []
        assert verify_database_schema(locked_database) == _head()
    else:
        assert visible == 0
        assert row_security_gaps(locked_database) == [
            RowSecurityGap("chat_conversations", owner, True)
        ]
        with pytest.raises(
            DatabaseSchemaError, match=rf"chat_conversations \(owner {owner}, forced\)"
        ):
            verify_database_schema(locked_database)


def test_lockdown_and_startup_check_read_no_application_rows(locked_database):
    # On every relation but alembic_version, the owner revokes its own
    # privileges and those of service_role, a role it inherits from.
    _run_sql(
        locked_database,
        """
        DO $$
        DECLARE
          relation regclass;
        BEGIN
          FOR relation IN
            SELECT oid::regclass FROM pg_class
            WHERE relnamespace = 'public'::regnamespace
              AND relkind IN ('r', 'p', 'v', 'm', 'f')
              AND relname <> 'alembic_version'
          LOOP
            EXECUTE format(
              'REVOKE ALL ON TABLE %s FROM CURRENT_USER, service_role', relation
            );
          END LOOP;
        END
        $$;
        """,
    )
    with locked_database.connect() as connection:
        with pytest.raises(ProgrammingError, match="permission denied"):
            connection.execute(text("SELECT 1 FROM public.chat_conversations LIMIT 1"))
    # The startup check reads catalogs and alembic_version; the lockdown reads
    # catalogs alone.
    assert verify_database_schema(locked_database) == _head()
    assert _warnings(_run_sql(locked_database, LOCKDOWN.read_text())) == []


def test_lockdown_asks_postgrest_to_reload_its_schema(exposed_database):
    listener = exposed_database.raw_connection()
    try:
        driver: Any = listener.driver_connection
        driver.autocommit = True
        with driver.cursor() as cursor:
            cursor.execute("LISTEN pgrst")
        _run_sql(exposed_database, LOCKDOWN.read_text())
        deadline = time.monotonic() + 10
        while not driver.notifies and time.monotonic() < deadline:
            wait_until_readable([driver], [], [], 0.5)
            driver.poll()
        assert [(notice.channel, notice.payload) for notice in driver.notifies] == [
            ("pgrst", "reload schema")
        ]
    finally:
        listener.close()


def test_preview_schema_stays_outside_the_data_api(monkeypatch, exposed_database, owner):
    schema = "uk_chat_pr_900002"
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    _run_sql(exposed_database, f"CREATE SCHEMA {schema}")
    command.upgrade(Config(str(ALEMBIC_CONFIG)), "head")
    _run_sql(
        exposed_database,
        f"""
        CREATE FUNCTION {schema}.preview_definer() RETURNS int LANGUAGE sql
          SECURITY DEFINER AS $$ SELECT 1 $$;
        """,
    )

    engine = namespaced_engine(
        exposed_database.url.render_as_string(hide_password=False),
        schema,
    )
    try:
        assert data_api_exposure(engine) == []
        assert verify_database_schema(engine) == _head()

        # Grants reach nothing until the role can use the schema.
        _run_sql(
            exposed_database,
            f"GRANT SELECT ON {schema}.chat_conversations TO anon",
        )
        assert data_api_exposure(engine) == []
        _run_sql(exposed_database, f"GRANT USAGE ON SCHEMA {schema} TO anon")
        assert data_api_exposure(engine) == [
            DataApiExposure("anon", "table", "chat_conversations", owner),
            DataApiExposure("anon", "function", "preview_definer()", owner),
        ]
        _run_sql(
            exposed_database,
            f"""
            REVOKE USAGE ON SCHEMA {schema} FROM anon;
            REVOKE SELECT ON {schema}.chat_conversations FROM anon;
            """,
        )
    finally:
        engine.dispose()
    with exposed_database.connect() as connection:
        assert _denied(connection, "anon", f"SELECT * FROM {schema}.chat_conversations")


def test_startup_check_passes_without_data_api_roles():
    with _temporary_database() as name:
        engine = _engine(name)
        try:
            with engine.connect() as connection:
                existing = connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_roles "
                        "WHERE rolname IN ('anon', 'authenticated')"
                    )
                )
            if existing:
                pytest.skip("This PostgreSQL server already has Supabase's roles")
            _run_sql(engine, "CREATE TABLE plain_table (id int PRIMARY KEY)")
            assert data_api_exposure(engine) == []
            assert row_security_gaps(engine) == []
        finally:
            engine.dispose()


def test_pull_request_ci_requires_disposable_postgres():
    workflow = TESTS_WORKFLOW.read_text()
    assert "CAPABILITY_TEST_POSTGRES_URL: postgresql://" in workflow
    assert 'CAPABILITY_TEST_POSTGRES_REQUIRED: "1"' in workflow


def test_a_missing_url_fails_these_tests_only_where_postgres_is_required():
    environment = {
        name: value
        for name, value in os.environ.items()
        if name
        not in {
            "CAPABILITY_TEST_POSTGRES_URL",
            "CAPABILITY_TEST_POSTGRES_REQUIRED",
            "PYTEST_ADDOPTS",
        }
        and not name.startswith(("COV_CORE_", "COVERAGE_"))
    }

    def run(**extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                str(Path(__file__)),
                "-q",
                "-p",
                "no:cacheprovider",
                "-k",
                "test_privilege_lists_follow_the_server_version",
            ],
            env={**environment, **extra},
            capture_output=True,
            text=True,
            timeout=300,
        )

    required = run(CAPABILITY_TEST_POSTGRES_REQUIRED="1")
    assert required.returncode == 1, required.stdout + required.stderr
    assert "CAPABILITY_TEST_POSTGRES_URL is not set" in required.stdout
    assert "1 error" in required.stdout

    optional = run()
    assert optional.returncode == 0, optional.stdout + optional.stderr
    assert "1 skipped" in optional.stdout
