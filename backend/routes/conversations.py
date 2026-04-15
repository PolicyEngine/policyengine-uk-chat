"""
Conversation history — save and retrieve past chat sessions.
"""

import json
import logging
import os
from urllib.parse import urlencode
import uuid
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


class SaveConversationRequest(BaseModel):
    session_id: str
    title: str
    messages: list
    user_id: str | None = None
    user_email: str | None = None


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
            existing.user_email = request.user_email
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
                user_email=request.user_email,
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


class SharedConversationDetail(BaseModel):
    title: str
    messages: list
    author: str | None = None
    created_at: str


class ReportConversationRequest(BaseModel):
    user_id: str | None = None
    note: str | None = None
    app_url: str | None = None


class ReportConversationResponse(BaseModel):
    share_token: str
    share_url: str | None = None
    issue_title: str
    issue_body: str
    issue_url: str


def _trim_text(text: str, limit: int = 1600) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit].rstrip()}\n\n... [{omitted} characters omitted]"


def _summarise_message(message: dict, limit: int = 400) -> str:
    role = str(message.get("role", "unknown")).upper()
    content = _trim_text(str(message.get("content", "")), limit)
    events = message.get("events") or []
    tool_names = [
        event.get("data", {}).get("tool_name")
        for event in events
        if isinstance(event, dict) and event.get("type") == "tool"
    ]
    tool_names = [name for name in tool_names if name]
    summary = f"### {role}\n\n{content or '_No content saved._'}"
    if tool_names:
        summary += "\n\nTools used: " + ", ".join(tool_names[:8])
        if len(tool_names) > 8:
            summary += ", ..."
    tool_sections = []
    for event in events:
        if not isinstance(event, dict) or event.get("type") != "tool":
            continue
        data = event.get("data") or {}
        tool_name = str(data.get("tool_name") or "unknown_tool")
        tool_section = [f"#### Tool `{tool_name}`"]
        if data.get("input") is not None:
            tool_input = _trim_text(json.dumps(data["input"], indent=2, ensure_ascii=False), 2000)
            tool_section.extend(["Input:", "```json", tool_input, "```"])
        if data.get("result_summary"):
            tool_output = _trim_text(str(data["result_summary"]), 3000)
            tool_section.extend(["Output:", "```json", tool_output, "```"])
        tool_sections.append("\n".join(tool_section))
    if tool_sections:
        summary += "\n\n" + "\n\n".join(tool_sections)
    return summary


def _build_issue_body(
    row: ChatConversation,
    *,
    messages: list,
    note: str | None,
    share_url: str | None,
) -> str:
    transcript = messages[-6:]
    lines = [
        "## What looked off",
        (note or "A user reported that something in this chat thread looks incorrect or suspicious.").strip(),
        "",
        "## Thread reference",
        f"- Conversation ID: `{row.id}`",
        f"- Session ID: `{row.session_id}`",
        f"- Title: {row.title}",
        f"- Saved at: {row.updated_at.isoformat()}",
    ]
    if share_url:
        lines.append(f"- Shared thread: {share_url}")
    if row.user_email:
        lines.append(f"- Reporter email: {row.user_email}")

    lines.extend(["", "## Transcript excerpt"])
    for message in transcript:
        lines.extend([_summarise_message(message), ""])

    lines.extend(
        [
            "## Debugging notes",
            "- Open the shared thread to inspect the full exchange.",
            "- Reproduce the last relevant user request locally if needed.",
            "- Check whether the issue is in prompting, tool use, data, or the underlying simulation engine.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


@router.post("/{conversation_id}/share")
def share_conversation(conversation_id: int, user_id: str | None = None):
    engine = get_engine()
    with Session(engine) as session:
        row = session.get(ChatConversation, conversation_id)
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row.user_id and row.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not your conversation")
        if not row.share_token:
            row.share_token = str(uuid.uuid4())
            session.add(row)
            session.commit()
            session.refresh(row)
    return {"share_token": row.share_token}


@router.post("/{conversation_id}/report", response_model=ReportConversationResponse)
def report_conversation(conversation_id: int, request: ReportConversationRequest):
    engine = get_engine()
    with Session(engine) as session:
        row = session.get(ChatConversation, conversation_id)
        if not row:
            raise HTTPException(status_code=404, detail="Conversation not found")
        if row.user_id and row.user_id != request.user_id:
            raise HTTPException(status_code=403, detail="Not your conversation")
        if not row.share_token:
            row.share_token = str(uuid.uuid4())
            session.add(row)
            session.commit()
            session.refresh(row)

    messages = json.loads(row.messages) if isinstance(row.messages, str) else row.messages
    issue_repo = os.environ.get("GITHUB_REPORT_REPO", "PolicyEngine/policyengine-uk-chat")
    base_url = (request.app_url or os.environ.get("APP_URL") or "").rstrip("/")
    share_url = f"{base_url}/s/{row.share_token}" if base_url else None
    issue_title = f"Investigate chat thread: {row.title[:90]}".strip()
    issue_body = _build_issue_body(
        row,
        messages=messages,
        note=request.note,
        share_url=share_url,
    )
    query = urlencode({"title": issue_title, "body": issue_body})
    issue_url = f"https://github.com/{issue_repo}/issues/new?{query}"

    return ReportConversationResponse(
        share_token=row.share_token,
        share_url=share_url,
        issue_title=issue_title,
        issue_body=issue_body,
        issue_url=issue_url,
    )


@router.get("/shared/{share_token}", response_model=SharedConversationDetail)
def get_shared_conversation(share_token: str):
    engine = get_engine()
    with Session(engine) as session:
        row = session.exec(
            select(ChatConversation).where(ChatConversation.share_token == share_token)
        ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Shared conversation not found")
    return SharedConversationDetail(
        title=row.title,
        messages=json.loads(row.messages) if isinstance(row.messages, str) else row.messages,
        author=row.user_email,
        created_at=row.created_at.isoformat(),
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
