"""
Conversation history — save and retrieve past chat sessions.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _get_engine():
    db_host = os.environ.get("DB_HOST", "localhost")
    db_port = os.environ.get("DB_PORT", "5432")
    db_name = os.environ.get("DB_NAME", "microsim")
    db_user = os.environ.get("DB_USERNAME", "postgres")
    db_pass = os.environ.get("DB_PASSWORD", "postgres")
    url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    return create_engine(url)


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = _get_engine()
    return _engine


class SaveConversationRequest(BaseModel):
    session_id: str
    title: str
    messages: list
    user_id: str | None = None


class ConversationSummary(BaseModel):
    id: int
    session_id: str
    title: str
    created_at: str
    updated_at: str


class ConversationDetail(ConversationSummary):
    messages: list


def _ensure_table():
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id SERIAL PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                messages JSON NOT NULL,
                user_id VARCHAR,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            )
        """))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_session ON chat_conversations (session_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_user ON chat_conversations (user_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_updated ON chat_conversations (updated_at)"))


def ensure_table():
    try:
        _ensure_table()
        logger.info("Conversations table ensured successfully")
    except Exception as e:
        logger.error(f"Could not ensure conversations table: {e}")
        import traceback; logger.error(traceback.format_exc())


@router.post("", response_model=ConversationDetail)
def save_conversation(request: SaveConversationRequest):
    now = datetime.now(timezone.utc)
    engine = get_engine()

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM chat_conversations WHERE session_id = :sid"),
            {"sid": request.session_id},
        ).fetchone()

        if existing:
            row = conn.execute(
                text("UPDATE chat_conversations SET title=:title, messages=cast(:messages as json), updated_at=:now, user_id=:uid WHERE session_id=:sid RETURNING id, session_id, title, messages, created_at, updated_at"),
                {"title": request.title, "messages": json.dumps(request.messages), "now": now, "sid": request.session_id, "uid": request.user_id},
            ).fetchone()
        else:
            row = conn.execute(
                text("INSERT INTO chat_conversations (session_id, title, messages, user_id, created_at, updated_at) VALUES (:sid, :title, cast(:messages as json), :uid, :now, :now) RETURNING id, session_id, title, messages, created_at, updated_at"),
                {"sid": request.session_id, "title": request.title, "messages": json.dumps(request.messages), "uid": request.user_id, "now": now},
            ).fetchone()

    return ConversationDetail(id=row.id, session_id=row.session_id, title=row.title, messages=row.messages, created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat())


@router.get("")
def list_conversations(user_id: str | None = None):
    engine = get_engine()
    with engine.connect() as conn:
        if user_id:
            rows = conn.execute(text("SELECT id, session_id, title, created_at, updated_at FROM chat_conversations WHERE user_id=:uid ORDER BY updated_at DESC LIMIT 100"), {"uid": user_id}).fetchall()
        else:
            rows = conn.execute(text("SELECT id, session_id, title, created_at, updated_at FROM chat_conversations WHERE user_id IS NULL ORDER BY updated_at DESC LIMIT 100")).fetchall()
    return [ConversationSummary(id=r.id, session_id=r.session_id, title=r.title, created_at=r.created_at.isoformat(), updated_at=r.updated_at.isoformat()) for r in rows]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int):
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(text("SELECT id, session_id, title, messages, created_at, updated_at FROM chat_conversations WHERE id=:id"), {"id": conversation_id}).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationDetail(id=row.id, session_id=row.session_id, title=row.title, messages=row.messages, created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat())


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int):
    engine = get_engine()
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM chat_conversations WHERE id=:id"), {"id": conversation_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
