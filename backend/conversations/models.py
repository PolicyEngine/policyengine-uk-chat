"""The SQLModel conversation table and runtime database engine."""

import os
from datetime import datetime
from typing import Optional

from sqlalchemy import Index
from sqlmodel import Field, SQLModel, create_engine


class ChatConversation(SQLModel, table=True):
    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index(
            "idx_chat_conversations_session_id_unique",
            "session_id",
            unique=True,
        ),
        Index("idx_chat_conversations_share_token", "share_token"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str
    title: str
    messages: str  # JSON string
    user_id: Optional[str] = None
    user_email: Optional[str] = None
    share_token: Optional[str] = None
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
