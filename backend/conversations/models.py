"""The conversations table model, engine, and schema bootstrap."""

import logging
import os
from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel, create_engine

logger = logging.getLogger(__name__)


class ChatConversation(SQLModel, table=True):
    __tablename__ = "chat_conversations"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, unique=True)
    title: str
    messages: str  # JSON string
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    share_token: Optional[str] = Field(default=None, index=True)
    created_at: datetime
    updated_at: datetime


_engine = None


def _deduplicate_conversations(conn):
    """Consolidate legacy duplicate sessions before adding uniqueness.

    The newest row owns the transcript and identity fields. Preserve the
    earliest creation time and any share token so existing shared links keep
    working after the redundant rows are removed.
    """
    from sqlalchemy import text

    result = conn.execute(
        text(
            """
            SELECT id, session_id, share_token, created_at
            FROM chat_conversations
            WHERE session_id IN (
                SELECT session_id
                FROM chat_conversations
                GROUP BY session_id
                HAVING COUNT(*) > 1
            )
            ORDER BY session_id, updated_at DESC, id DESC
            """
        )
    )
    rows = result.mappings().all() if result is not None else []
    grouped = {}
    for row in rows:
        grouped.setdefault(row["session_id"], []).append(row)

    for duplicates in grouped.values():
        canonical = duplicates[0]
        share_token = next(
            (row["share_token"] for row in duplicates if row["share_token"]),
            None,
        )
        created_at = min(row["created_at"] for row in duplicates)
        conn.execute(
            text(
                """
                UPDATE chat_conversations
                SET share_token = :share_token, created_at = :created_at
                WHERE id = :canonical_id
                """
            ),
            {
                "share_token": share_token,
                "created_at": created_at,
                "canonical_id": canonical["id"],
            },
        )
        for duplicate in duplicates[1:]:
            conn.execute(
                text("DELETE FROM chat_conversations WHERE id = :duplicate_id"),
                {"duplicate_id": duplicate["id"]},
            )


def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL not set")
        _engine = create_engine(url)
    return _engine


def ensure_table():
    try:
        engine = get_engine()
        SQLModel.metadata.create_all(engine)
        # Add columns that may not exist yet on older databases
        from sqlalchemy import text
        with engine.connect() as conn:
            for col, col_type in [("share_token", "TEXT"), ("user_email", "TEXT")]:
                try:
                    conn.execute(text(f"ALTER TABLE chat_conversations ADD COLUMN {col} {col_type}"))
                    conn.commit()
                    logger.info(f"Added column {col} to chat_conversations")
                except Exception:
                    conn.rollback()  # Column already exists
            _deduplicate_conversations(conn)
            conn.commit()
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chat_conversations_share_token ON chat_conversations (share_token)"))
                conn.commit()
            except Exception:
                conn.rollback()
            try:
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_conversations_session_id_unique ON chat_conversations (session_id)"))
                conn.commit()
            except Exception:
                conn.rollback()
        logger.info("Conversations table ensured successfully")
    except Exception as e:
        logger.error(f"Could not ensure conversations table: {e}")
        import traceback; logger.error(traceback.format_exc())
