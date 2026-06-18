"""Share-link endpoints: mint a share token and read a shared conversation."""

import json
import uuid

from fastapi import APIRouter, HTTPException
from sqlmodel import Session, select

from conversations.models import ChatConversation, get_engine
from conversations.schemas import SharedConversationDetail

router = APIRouter(prefix="/conversations", tags=["conversations"])


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
