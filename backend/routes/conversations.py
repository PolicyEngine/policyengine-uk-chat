"""
Conversation history — save and retrieve past chat sessions.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, create_engine, select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ChatConversation(SQLModel, table=True):
    __tablename__ = "chat_conversations"
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str
    title: str
    messages: str  # JSON string
    user_id: Optional[str] = None
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


def ensure_table():
    try:
        SQLModel.metadata.create_all(get_engine())
        logger.info("Conversations table ensured successfully")
    except Exception as e:
        logger.error(f"Could not ensure conversations table: {e}")
        import traceback; logger.error(traceback.format_exc())


@router.post("", response_model=ConversationDetail)
def save_conversation(request: SaveConversationRequest):
    now = datetime.now(timezone.utc)
    engine = get_engine()

    with Session(engine) as session:
        existing = session.exec(
            select(ChatConversation).where(ChatConversation.session_id == request.session_id)
        ).first()

        if existing:
            existing.title = request.title
            existing.messages = json.dumps(request.messages)
            existing.updated_at = now
            existing.user_id = request.user_id
            session.add(existing)
            session.commit()
            session.refresh(existing)
            row = existing
        else:
            row = ChatConversation(
                session_id=request.session_id,
                title=request.title,
                messages=json.dumps(request.messages),
                user_id=request.user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.commit()
            session.refresh(row)

    return ConversationDetail(
        id=row.id, session_id=row.session_id, title=row.title,
        messages=json.loads(row.messages) if isinstance(row.messages, str) else row.messages,
        created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat(),
    )


@router.get("")
def list_conversations(user_id: str | None = None):
    engine = get_engine()
    with Session(engine) as session:
        stmt = select(ChatConversation)
        if user_id:
            stmt = stmt.where(ChatConversation.user_id == user_id)
        else:
            stmt = stmt.where(ChatConversation.user_id == None)
        stmt = stmt.order_by(ChatConversation.updated_at.desc()).limit(100)
        rows = session.exec(stmt).all()
    return [
        ConversationSummary(
            id=r.id, session_id=r.session_id, title=r.title,
            created_at=r.created_at.isoformat(), updated_at=r.updated_at.isoformat(),
        ) for r in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int):
    engine = get_engine()
    with Session(engine) as session:
        row = session.get(ChatConversation, conversation_id)
    if not row:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return ConversationDetail(
        id=row.id, session_id=row.session_id, title=row.title,
        messages=json.loads(row.messages) if isinstance(row.messages, str) else row.messages,
        created_at=row.created_at.isoformat(), updated_at=row.updated_at.isoformat(),
    )


@router.delete("/{conversation_id}", status_code=204)
def delete_conversation(conversation_id: int):
    engine = get_engine()
    with Session(engine) as session:
        row = session.get(ChatConversation, conversation_id)
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        session.delete(row)
        session.commit()
