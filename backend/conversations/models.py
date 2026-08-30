"""The SQLModel conversation table and runtime database engine."""

import os
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, Index, Text
from sqlmodel import Field, SQLModel

from persistence.database_namespace import namespaced_engine


class ChatConversation(SQLModel, table=True):
    __tablename__ = "chat_conversations"
    __table_args__ = (
        Index(
            "idx_chat_conversations_session_id_unique",
            "session_id",
            unique=True,
        ),
        Index("idx_chat_conversations_share_token", "share_token"),
        Index("ix_conversations_session", "session_id"),
        Index("ix_conversations_user", "user_id"),
        Index("ix_conversations_updated", "updated_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str
    title: str
    messages: list[dict[str, Any]] = Field(sa_type=JSON)
    user_id: Optional[str] = None
    user_email: Optional[str] = Field(default=None, sa_type=Text)
    share_token: Optional[str] = Field(default=None, sa_type=Text)
    created_at: datetime
    updated_at: datetime


_engine = None


def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            raise RuntimeError("DATABASE_URL not set")
        _engine = namespaced_engine(url)
    return _engine
