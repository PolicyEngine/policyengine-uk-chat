"""Data API access tests against disposable PostgreSQL databases.

Each test builds a database the way Supabase does: the anon, authenticated and
service_role roles, default privileges that grant them every new object in
public, a stand-in auth schema, the billing migrations, and the Alembic tables.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ProgrammingError
from sqlmodel import Session, select

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
    DatabaseSchemaError,
    data_api_exposure,
    expected_schema_revision,
    verify_database_schema,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = REPO_ROOT / "backend/alembic.ini"
SUPABASE_MIGRATIONS = REPO_ROOT / "supabase/migrations"
LOCKDOWN = SUPABASE_MIGRATIONS / "005_lock_down_data_api.sql"
POSTGRES_URL = os.environ.get("CAPABILITY_TEST_POSTGRES_URL")

pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="CAPABILITY_TEST_POSTGRES_URL is not configured for disposable PostgreSQL tests",
)

ROLES = ("anon", "authenticated", "service_role")
BILLING_TABLES = {"token_usage", "user_credits"}
UNMANAGED_TABLE = "analysis_workflows"
VIEW = "conversation_titles"
ALL_TABLES = MANAGED_TABLES | BILLING_TABLES | {"alembic_version", UNMANAGED_TABLE}
FIRST_USER = "00000000-0000-0000-0000-00000000000a"
SECOND_USER = "00000000-0000-0000-0000-00000000000b"
USER_WITHOUT_CREDITS = "00000000-0000-0000-0000-00000000000c"

SUPABASE_BOOTSTRAP = """
CREATE SCHEMA auth;
CREATE TABLE auth.users (id uuid PRIMARY KEY);
CREATE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
  $$ SELECT nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON ROUTINES TO anon, authenticated, service_role;
"""

SEED = f"""
INSERT INTO auth.users
  VALUES ('{FIRST_USER}'), ('{SECOND_USER}'), ('{USER_WITHOUT_CREDITS}');
INSERT INTO user_credits (user_id, balance_gbp)
  VALUES ('{FIRST_USER}', 5), ('{SECOND_USER}', 7);
INSERT INTO token_usage (user_id, session_id)
  VALUES ('{FIRST_USER}', 'first'), ('{SECOND_USER}', 'second');
INSERT INTO chat_conversations (session_id, title, messages, created_at, updated_at)
  VALUES ('seeded', 'Seeded', '[]', now(), now());
CREATE TABLE {UNMANAGED_TABLE} (id int PRIMARY KEY);
INSERT INTO {UNMANAGED_TABLE} VALUES (1);
CREATE VIEW {VIEW} AS SELECT title FROM chat_conversations;
CREATE FUNCTION touches_no_table() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$;
"""


def _run_sql(engine: Engine, sql: str) -> None:
    """Run a whole file through the driver's simple protocol, as psql would."""
    connection = engine.raw_connection()
    try:
        connection.driver_connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute(sql)
        cursor.close()
    finally:
        connection.close()


def _admin_engine() -> Engine:
    assert POSTGRES_URL is not None
    return create_engine(
        POSTGRES_URL,
        isolation_level="AUTOCOMMIT",
        poolclass=pool.NullPool,
    )


@contextmanager
def _temporary_database() -> Iterator[Engine]:
    assert POSTGRES_URL is not None
    name = f"uk_chat_lockdown_{uuid4().hex[:12]}"
    admin = _admin_engine()
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(
        make_url(POSTGRES_URL).set(database=name),
        poolclass=pool.NullPool,
    )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def data_api_roles() -> Iterator[None]:
    """Create the Supabase roles for one test, keeping any that already exist."""
    admin = _admin_engine()
    with admin.connect() as connection:
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
        with admin.connect() as connection:
            for role in created:
                connection.execute(text(f"DROP ROLE {role}"))
        admin.dispose()


@pytest.fixture
def exposed_database(monkeypatch, data_api_roles) -> Iterator[Engine]:
    """A database at the Alembic head, before the lockdown migration runs."""
    del data_api_roles
    monkeypatch.delenv("DATABASE_SCHEMA", raising=False)
    with _temporary_database() as engine:
        monkeypatch.setenv(
            "ALEMBIC_DATABASE_URL",
            engine.url.render_as_string(hide_password=False),
        )
        _run_sql(engine, SUPABASE_BOOTSTRAP)
        for migration in ("001_billing.sql", "003_token_usage_model_fields.sql"):
            _run_sql(engine, (SUPABASE_MIGRATIONS / migration).read_text())
        command.upgrade(Config(str(ALEMBIC_CONFIG)), "head")
        _run_sql(engine, SEED)
        yield engine


@pytest.fixture
def locked_database(exposed_database: Engine) -> Engine:
    _run_sql(exposed_database, LOCKDOWN.read_text())
    return exposed_database


def _public_tables(engine: Engine) -> dict[str, str]:
    """Map every table in public to one of its column names."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT c.relname, min(a.attname::text) AS column_name
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                JOIN pg_attribute AS a ON a.attrelid = c.oid
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p')
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                GROUP BY c.relname
                """
            )
        )
        return {row.relname: row.column_name for row in rows}


def _statements(table: str, column: str) -> dict[str, str]:
    return {
        "SELECT": f'SELECT * FROM public."{table}" LIMIT 1',
        "INSERT": f'INSERT INTO public."{table}" DEFAULT VALUES',
        "UPDATE": f'UPDATE public."{table}" SET "{column}" = "{column}"',
        "DELETE": f'DELETE FROM public."{table}"',
        "TRUNCATE": f'TRUNCATE public."{table}" CASCADE',
    }


def _denied(engine: Engine, role: str, statement: str, *, user: str = "") -> bool:
    """Report whether PostgreSQL refuses a statement for a role, changing nothing."""
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text("SELECT set_config('request.jwt.claim.sub', :user, true)"),
                {"user": user},
            )
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            connection.execute(text(statement))
        except ProgrammingError as error:
            if error.orig.pgcode == "42501":
                return True
            raise
        else:
            return False
        finally:
            transaction.rollback()


def _as_role(engine: Engine, role: str, statement: str, *, user: str = "") -> list:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text("SELECT set_config('request.jwt.claim.sub', :user, true)"),
                {"user": user},
            )
            connection.execute(text(f"SET LOCAL ROLE {role}"))
            return list(connection.execute(text(statement)))
        finally:
            transaction.rollback()


def _access_snapshot(engine: Engine) -> list[tuple]:
    with engine.connect() as connection:
        relations = connection.execute(
            text(
                """
                SELECT c.relname, c.relkind::text, c.relrowsecurity, c.relacl::text
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                ORDER BY c.relname
                """
            )
        )
        policies = connection.execute(
            text(
                """
                SELECT tablename, policyname, cmd, roles::text
                FROM pg_policies
                WHERE schemaname = 'public'
                ORDER BY tablename, policyname
                """
            )
        )
        return [tuple(row) for row in [*relations, *policies]]


def test_fixture_reproduces_supabase_default_grants(exposed_database):
    """Without this, the denial tests below could pass on a closed fixture."""
    tables = _public_tables(exposed_database)
    assert set(tables) == ALL_TABLES
    for table, column in tables.items():
        statements = _statements(table, column)
        for role in ("anon", "authenticated"):
            assert not _denied(exposed_database, role, statements["SELECT"])
            assert not _denied(exposed_database, role, statements["DELETE"])

    pairs = data_api_exposure(exposed_database)
    assert {("anon", table) for table in ALL_TABLES} <= set(pairs)
    with pytest.raises(DatabaseSchemaError, match="005_lock_down_data_api.sql"):
        verify_database_schema(exposed_database)


def test_anon_is_denied_every_action_on_every_table(locked_database):
    tables = _public_tables(locked_database)
    assert set(tables) == ALL_TABLES
    for table, column in tables.items():
        for action, statement in _statements(table, column).items():
            assert _denied(locked_database, "anon", statement), f"anon {action} {table}"


def test_authenticated_is_denied_all_but_reading_own_billing_rows(locked_database):
    tables = _public_tables(locked_database)
    for table, column in tables.items():
        for action, statement in _statements(table, column).items():
            allowed = action == "SELECT" and table in BILLING_TABLES
            assert (
                _denied(locked_database, "authenticated", statement, user=FIRST_USER)
                is not allowed
            ), f"authenticated {action} {table}"

    for table in BILLING_TABLES:
        for user in (FIRST_USER, SECOND_USER):
            rows = _as_role(
                locked_database,
                "authenticated",
                f"SELECT user_id FROM public.{table}",
                user=user,
            )
            assert [str(row.user_id) for row in rows] == [user]
        assert (
            _as_role(
                locked_database,
                "authenticated",
                f"SELECT user_id FROM public.{table}",
            )
            == []
        )


def test_every_view_sequence_and_function_is_closed(locked_database):
    for role in ("anon", "authenticated"):
        assert _denied(locked_database, role, f"SELECT * FROM public.{VIEW}")
        assert _denied(locked_database, role, "SELECT public.touches_no_table()")
        assert _denied(
            locked_database,
            role,
            "SELECT nextval('public.chat_conversations_id_seq')",
        )


def test_only_the_own_row_read_policies_remain(locked_database):
    with locked_database.connect() as connection:
        policies = connection.execute(
            text(
                """
                SELECT tablename, policyname
                FROM pg_policies
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            )
        )
        assert [tuple(row) for row in policies] == [
            ("token_usage", "Users read own usage"),
            ("user_credits", "Users read own credits"),
        ]


def test_lockdown_turns_on_row_level_security_for_every_table(locked_database):
    with locked_database.connect() as connection:
        without_rls = connection.scalars(
            text(
                """
                SELECT c.relname
                FROM pg_class AS c
                JOIN pg_namespace AS n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p')
                  AND NOT c.relrowsecurity
                """
            )
        ).all()
    assert without_rls == []


def test_service_role_keeps_the_billing_access_the_backend_uses(locked_database):
    """billing/credits.py reads and writes user_credits and inserts token_usage."""
    assert (
        _as_role(
            locked_database,
            "service_role",
            "SELECT count(*) AS rows FROM public.user_credits",
        )[0].rows
        == 2
    )
    assert (
        _as_role(
            locked_database,
            "service_role",
            "UPDATE public.user_credits SET balance_gbp = 9 "
            f"WHERE user_id = '{FIRST_USER}' RETURNING balance_gbp",
        )[0].balance_gbp
        == 9
    )
    assert (
        str(
            _as_role(
                locked_database,
                "service_role",
                "INSERT INTO public.user_credits (user_id) "
                f"VALUES ('{USER_WITHOUT_CREDITS}') RETURNING user_id",
            )[0].user_id
        )
        == USER_WITHOUT_CREDITS
    )
    assert (
        _as_role(
            locked_database,
            "service_role",
            "INSERT INTO public.token_usage (user_id, session_id) "
            f"VALUES ('{FIRST_USER}', 'service') RETURNING session_id",
        )[0].session_id
        == "service"
    )


def test_backend_repositories_keep_working_as_the_table_owner(locked_database):
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


def test_lockdown_keeps_every_row(exposed_database):
    def counts() -> dict[str, int]:
        with exposed_database.connect() as connection:
            return {
                table: connection.scalar(
                    text(f'SELECT count(*) FROM public."{table}"')
                )
                for table in _public_tables(exposed_database)
            }

    before = counts()
    _run_sql(exposed_database, LOCKDOWN.read_text())
    assert counts() == before
    assert before["chat_conversations"] == 1
    assert before["user_credits"] == 2
    assert before[UNMANAGED_TABLE] == 1


def test_lockdown_can_run_again_without_changing_access(locked_database):
    before = _access_snapshot(locked_database)
    _run_sql(locked_database, LOCKDOWN.read_text())
    assert _access_snapshot(locked_database) == before


def test_new_table_stays_closed_until_the_rerun_turns_on_its_security(locked_database):
    _run_sql(locked_database, "CREATE TABLE added_later (id int PRIMARY KEY)")
    statements = _statements("added_later", "id")
    for role in ("anon", "authenticated"):
        for statement in statements.values():
            assert _denied(locked_database, role, statement)
    assert data_api_exposure(locked_database) == []

    _run_sql(locked_database, LOCKDOWN.read_text())
    with locked_database.connect() as connection:
        assert connection.scalar(
            text("SELECT relrowsecurity FROM pg_class WHERE relname = 'added_later'")
        )


def test_new_sequences_and_functions_receive_no_data_api_grants(locked_database):
    _run_sql(
        locked_database,
        """
        CREATE SEQUENCE added_later_sequence;
        CREATE FUNCTION added_later_function() RETURNS int LANGUAGE sql AS
          $$ SELECT 1 $$;
        """,
    )
    with locked_database.connect() as connection:
        sequence_acl = connection.scalar(
            text(
                "SELECT relacl::text FROM pg_class "
                "WHERE relname = 'added_later_sequence'"
            )
        )
        function_acl = connection.scalar(
            text(
                "SELECT proacl::text FROM pg_proc "
                "WHERE proname = 'added_later_function'"
            )
        )
    for acl in (sequence_acl, function_acl):
        assert "anon=" not in (acl or "")
        assert "authenticated=" not in (acl or "")


def test_alembic_lifecycle_runs_on_a_locked_database(locked_database):
    config = Config(str(ALEMBIC_CONFIG))
    head = expected_schema_revision(ALEMBIC_CONFIG)
    assert verify_database_schema(locked_database) == head

    command.check(config)
    command.downgrade(config, "-1")
    command.upgrade(config, "head")

    assert verify_database_schema(locked_database) == head
    assert data_api_exposure(locked_database) == []
    tables = _public_tables(locked_database)
    for table in MANAGED_TABLES:
        for statement in _statements(table, tables[table]).values():
            assert _denied(locked_database, "anon", statement)


@pytest.mark.parametrize(
    ("grant", "pair"),
    [
        ("GRANT SELECT ON chat_conversations TO anon", ("anon", "chat_conversations")),
        (
            "GRANT SELECT (title) ON chat_conversations TO anon",
            ("anon", "chat_conversations"),
        ),
        (
            "GRANT DELETE ON capability_artifacts TO authenticated",
            ("authenticated", "capability_artifacts"),
        ),
        ("GRANT INSERT ON user_credits TO authenticated", ("authenticated", "user_credits")),
        ("GRANT SELECT ON user_credits TO anon", ("anon", "user_credits")),
        (
            "ALTER TABLE token_usage DISABLE ROW LEVEL SECURITY",
            ("authenticated", "token_usage"),
        ),
        (f"GRANT SELECT ON {VIEW} TO anon", ("anon", VIEW)),
    ],
)
def test_startup_check_catches_each_way_back_in(locked_database, grant, pair):
    assert data_api_exposure(locked_database) == []
    _run_sql(locked_database, grant)
    assert pair in data_api_exposure(locked_database)
    with pytest.raises(DatabaseSchemaError, match=pair[1]):
        verify_database_schema(locked_database)


def test_preview_schema_stays_outside_the_data_api(monkeypatch, exposed_database):
    schema = "uk_chat_pr_900002"
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    _run_sql(exposed_database, f"CREATE SCHEMA {schema}")
    command.upgrade(Config(str(ALEMBIC_CONFIG)), "head")

    engine = namespaced_engine(
        exposed_database.url.render_as_string(hide_password=False),
        schema,
    )
    try:
        assert data_api_exposure(engine) == []
        assert verify_database_schema(engine) == expected_schema_revision(ALEMBIC_CONFIG)

        # A table grant alone reaches nothing until the role can use the schema.
        _run_sql(
            exposed_database,
            f"GRANT SELECT ON {schema}.chat_conversations TO anon",
        )
        assert data_api_exposure(engine) == []
        _run_sql(exposed_database, f"GRANT USAGE ON SCHEMA {schema} TO anon")
        assert data_api_exposure(engine) == [("anon", "chat_conversations")]
        _run_sql(
            exposed_database,
            f"""
            REVOKE USAGE ON SCHEMA {schema} FROM anon;
            REVOKE SELECT ON {schema}.chat_conversations FROM anon;
            """,
        )
    finally:
        engine.dispose()
    assert _denied(
        exposed_database,
        "anon",
        f"SELECT * FROM {schema}.chat_conversations",
    )


def test_startup_check_passes_without_data_api_roles():
    with _temporary_database() as engine:
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
