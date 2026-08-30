"""Validated PostgreSQL namespace selection for isolated preview deployments."""

from __future__ import annotations

import os
import re

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.schema import CreateSchema, DropSchema


DATABASE_SCHEMA_ENV = "DATABASE_SCHEMA"
PREVIEW_SCHEMA_PREFIX = "uk_chat_pr_"
_SCHEMA_PATTERN = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PREVIEW_SCHEMA_PATTERN = re.compile(rf"^{PREVIEW_SCHEMA_PREFIX}[1-9][0-9]*$")


class DatabaseNamespaceError(RuntimeError):
    """Raised when a configured database namespace is unsafe or unsupported."""


def validate_database_schema(value: str) -> str:
    """Return a safe PostgreSQL schema identifier."""
    if not _SCHEMA_PATTERN.fullmatch(value):
        raise DatabaseNamespaceError(
            "DATABASE_SCHEMA must be a lowercase PostgreSQL identifier"
        )
    return value


def configured_database_schema() -> str | None:
    """Return the optional validated schema selected for this deployment."""
    value = os.environ.get(DATABASE_SCHEMA_ENV, "").strip()
    return validate_database_schema(value) if value else None


def postgres_connect_args(database_url: str, schema: str | None) -> dict[str, str]:
    """Build connection arguments that select one isolated PostgreSQL schema."""
    if schema is None:
        return {}
    validate_database_schema(schema)
    if make_url(database_url).get_backend_name() != "postgresql":
        raise DatabaseNamespaceError(
            "DATABASE_SCHEMA is supported only for PostgreSQL connections"
        )
    return {"options": f"-csearch_path={schema}"}


def ensure_database_schema(database_url: str, schema: str | None) -> None:
    """Create an explicitly configured deployment namespace if it is absent."""
    if schema is None:
        return
    validate_database_schema(schema)
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema, if_not_exists=True))
    finally:
        engine.dispose()


def drop_preview_schema(database_url: str, schema: str) -> None:
    """Remove only a schema whose name is in the strict PR-preview namespace."""
    if not _PREVIEW_SCHEMA_PATTERN.fullmatch(schema):
        raise DatabaseNamespaceError(
            f"Refusing to remove non-preview database schema {schema!r}"
        )
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True, if_exists=True))
    finally:
        engine.dispose()


def namespaced_engine(database_url: str, schema: str | None = None) -> Engine:
    """Create an engine whose unqualified SQLModel objects use the given schema."""
    selected_schema = schema if schema is not None else configured_database_schema()
    return create_engine(
        database_url,
        connect_args=postgres_connect_args(database_url, selected_schema),
    )
