"""CRUD endpoints for saved conversations."""

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, select

from conversations.models import ChatConversation, get_engine
from conversations.schemas import (
    ConversationDetail,
    ConversationSearchResult,
    ConversationSummary,
    SaveConversationRequest,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _upsert_conversation(session, request, now):
    values = {
        "session_id": request.session_id,
        "title": request.title,
        "messages": json.dumps(request.messages),
        "user_id": request.user_id,
        "user_email": request.user_email,
        "created_at": now,
        "updated_at": now,
    }
    dialect = session.get_bind().dialect.name
    insert = sqlite_insert if dialect == "sqlite" else postgresql_insert
    statement = insert(ChatConversation).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=[ChatConversation.session_id],
        set_={
            "title": statement.excluded.title,
            "messages": statement.excluded.messages,
            "user_id": statement.excluded.user_id,
            "user_email": statement.excluded.user_email,
            "updated_at": statement.excluded.updated_at,
        },
    )
    session.exec(statement)
    session.commit()
    return session.exec(
        select(ChatConversation).where(
            ChatConversation.session_id == request.session_id
        )
    ).one()


def _normalise_search_text(value):
    return re.sub(r"\s+", " ", value).strip()


def _matching_snippet(messages, query):
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        clean_content = _normalise_search_text(content)
        match_at = clean_content.casefold().find(query)
        if match_at < 0:
            continue
        start = max(0, match_at - 70)
        end = min(len(clean_content), match_at + len(query) + 100)
        prefix = "…" if start else ""
        suffix = "…" if end < len(clean_content) else ""
        return f"{prefix}{clean_content[start:end]}{suffix}"
    return None


@router.post("", response_model=ConversationDetail)
def save_conversation(request: SaveConversationRequest):
    now = datetime.now(timezone.utc)
    engine = get_engine()

    with Session(engine) as session:
        row = _upsert_conversation(session, request, now)

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


@router.get("/search", response_model=list[ConversationSearchResult])
def search_conversations(query: str, user_id: str | None = None):
    clean_query = _normalise_search_text(query).casefold()
    if not clean_query:
        return []

    engine = get_engine()
    with Session(engine) as session:
        stmt = select(ChatConversation)
        if user_id:
            stmt = stmt.where(ChatConversation.user_id == user_id)
        else:
            stmt = stmt.where(ChatConversation.user_id == None)
        rows = session.exec(
            stmt.order_by(ChatConversation.updated_at.desc()).limit(100)
        ).all()

    results = []
    for row in rows:
        try:
            messages = (
                json.loads(row.messages)
                if isinstance(row.messages, str)
                else row.messages
            )
        except (TypeError, json.JSONDecodeError):
            messages = []
        snippet = _matching_snippet(messages, clean_query)
        if clean_query not in row.title.casefold() and snippet is None:
            continue
        results.append(
            ConversationSearchResult(
                id=row.id,
                session_id=row.session_id,
                title=row.title,
                snippet=snippet,
                created_at=row.created_at.isoformat(),
                updated_at=row.updated_at.isoformat(),
            )
        )
        if len(results) == 50:
            break
    return results


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
        from analysis.persistence import SqlAnalysisStore

        SqlAnalysisStore(engine).delete_session(row.session_id, db=session)
        session.delete(row)
        session.commit()
