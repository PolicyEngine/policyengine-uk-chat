"""Conversation-authorized projections of sanitized invocation activity."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, JsonValue
from sqlmodel import Session, select

from capabilities.tracing import InvocationKind, InvocationStatus
from conversations.models import ChatConversation, get_engine
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.contracts import Visibility


router = APIRouter(prefix="/chat", tags=["chatbot"])


class InvocationActivityItem(BaseModel):
    """The complete and exclusive field allowlist exposed by the activity API."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_id: str
    invocation_id: str
    parent_invocation_id: str | None = None
    sequence: int
    kind: InvocationKind
    identifier: str
    version: str
    visibility: Visibility
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    status: InvocationStatus
    summary: str
    debug_input: JsonValue | None = None
    debug_output: JsonValue | None = None


class ConversationActivityResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    projection: Literal["normal", "debug"]
    invocations: tuple[InvocationActivityItem, ...]


def _authorize_conversation_read(session_id: str, user_id: str | None) -> None:
    engine = get_engine()
    with Session(engine) as session:
        row = session.exec(
            select(ChatConversation).where(ChatConversation.session_id == session_id)
        ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if row.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not your conversation")


@router.get(
    "/{session_id}/activity",
    response_model=ConversationActivityResponse,
    response_model_exclude_none=True,
)
def get_conversation_activity(
    session_id: str,
    *,
    user_id: str | None = None,
    debug: bool = False,
) -> ConversationActivityResponse:
    """Return public activity normally and all sanitized activity in debug mode."""

    _authorize_conversation_read(session_id, user_id)
    records = SQLInvocationTraceRepository().list_for_conversation(
        session_id,
        include_private=debug,
    )
    return ConversationActivityResponse(
        projection="debug" if debug else "normal",
        invocations=tuple(
            InvocationActivityItem.model_validate(
                record.model_dump(
                    exclude={
                        "conversation_id",
                        *(
                            ()
                            if debug
                            else ("debug_input", "debug_output")
                        ),
                    }
                )
            )
            for record in records
        ),
    )
