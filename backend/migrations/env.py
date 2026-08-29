"""Alembic environment for SQLModel-owned conversation and capability tables."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
from sqlmodel import SQLModel
from sqlmodel.sql.sqltypes import AutoString

from conversations.models import ChatConversation  # noqa: F401
import persistence.rows  # noqa: F401,E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata
managed_table_names = frozenset(target_metadata.tables)


def _database_url() -> str:
    url = os.environ.get("ALEMBIC_DATABASE_URL")
    if not url:
        raise RuntimeError(
            "ALEMBIC_DATABASE_URL is required for database migrations"
        )
    if make_url(url).get_backend_name() != "postgresql":
        raise RuntimeError(
            "Alembic migrations require a disposable or deployed PostgreSQL database"
        )
    return url


def _include_object(
    object_, name: str | None, type_: str, reflected: bool, compare_to
) -> bool:
    del compare_to
    if type_ == "table":
        return name in managed_table_names
    table = getattr(object_, "table", None)
    return table is None or table.name in managed_table_names


def _render_item(type_: str, object_, _autogen_context):
    if type_ == "type" and isinstance(object_, AutoString):
        return "sa.String()"
    return False


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=_include_object,
        render_item=_render_item,
        compare_type=True,
        compare_server_default=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """Run migrations without opening a database connection."""
    _configure(
        url=_database_url(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against the configured PostgreSQL database."""
    connectable = create_engine(_database_url(), poolclass=pool.NullPool)

    with connectable.connect() as connection:
        _configure(connection=connection)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
