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
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chat_conversations_share_token ON chat_conversations (share_token)"))
                conn.commit()
            except Exception:
                conn.rollback()
        logger.info("Conversations table ensured successfully")
    except Exception as e:
        logger.error(f"Could not ensure conversations table: {e}")
        import traceback; logger.error(traceback.format_exc())
