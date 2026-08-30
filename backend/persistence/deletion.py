"""Conversation-owned capability record deletion."""

from sqlmodel import Session, delete

from persistence.rows import (
    CapabilityArtifactRow,
    CapabilityCallReceiptRow,
    ConversationContextRow,
    InvocationTraceRow,
    TurnReceiptRow,
    WaitingCapabilityInvocationRow,
)


CAPABILITY_CONVERSATION_ROWS = (
    CapabilityCallReceiptRow,
    TurnReceiptRow,
    InvocationTraceRow,
    WaitingCapabilityInvocationRow,
    CapabilityArtifactRow,
    ConversationContextRow,
)


def delete_capability_records(session: Session, conversation_id: str) -> None:
    for row_model in CAPABILITY_CONVERSATION_ROWS:
        session.exec(
            delete(row_model).where(row_model.conversation_id == conversation_id)
        )
