"""Additive SQLModel rows for capability state and invocation metadata."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, Index, String
from sqlmodel import Field, SQLModel


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _conversation_column() -> Column:
    return Column(
        String,
        nullable=False,
    )


class CapabilityArtifactRow(SQLModel, table=True):
    __tablename__ = "capability_artifacts"
    __table_args__ = (
        Index("idx_capability_artifacts_conversation", "conversation_id"),
        Index("idx_capability_artifacts_type", "artifact_type"),
    )

    artifact_id: str = Field(primary_key=True)
    conversation_id: str = Field(sa_column=_conversation_column())
    artifact_type: str
    schema_version: str
    payload_json: str
    created_at: datetime = Field(default_factory=_now)


class ConversationContextRow(SQLModel, table=True):
    __tablename__ = "conversation_contexts"
    __table_args__ = (
        Index("idx_conversation_context_updated", "updated_at"),
    )

    conversation_id: str = Field(primary_key=True)
    schema_version: str
    revision: int
    payload_json: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class WaitingCapabilityInvocationRow(SQLModel, table=True):
    __tablename__ = "waiting_capability_invocations"
    __table_args__ = (
        Index("idx_waiting_capability_conversation", "conversation_id"),
        Index("idx_waiting_capability_id", "capability_id"),
    )

    invocation_id: str = Field(primary_key=True)
    conversation_id: str = Field(sa_column=_conversation_column())
    capability_id: str
    capability_version: str
    input_schema_version: str
    partial_input_json: str
    source_turn_id: str
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class InvocationTraceRow(SQLModel, table=True):
    __tablename__ = "capability_invocation_traces"
    __table_args__ = (
        Index(
            "idx_capability_trace_conversation_sequence",
            "conversation_id",
            "sequence",
        ),
        Index("idx_capability_trace_turn", "turn_id"),
        Index("idx_capability_trace_parent", "parent_invocation_id"),
        Index("idx_capability_trace_identifier", "identifier"),
    )

    invocation_id: str = Field(primary_key=True)
    conversation_id: str = Field(sa_column=_conversation_column())
    turn_id: str
    parent_invocation_id: str | None = None
    sequence: int
    kind: str
    identifier: str
    version: str
    visibility: str
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = None
    status: str
    summary: str
    debug_input_json: str | None = None
    debug_output_json: str | None = None


class TurnReceiptRow(SQLModel, table=True):
    __tablename__ = "capability_turn_receipts"
    __table_args__ = (
        Index("idx_capability_turn_conversation", "conversation_id"),
    )

    turn_id: str = Field(primary_key=True)
    conversation_id: str = Field(sa_column=_conversation_column())
    request_fingerprint: str
    status: str
    public_outcome_json: str | None = None
    billing_claimed: bool = False
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class CapabilityCallReceiptRow(SQLModel, table=True):
    __tablename__ = "capability_call_receipts"
    __table_args__ = (
        Index("idx_capability_call_conversation", "conversation_id"),
        Index("idx_capability_call_turn", "turn_id"),
        Index("idx_capability_call_operation", "operation_id"),
    )

    call_id: str = Field(primary_key=True)
    conversation_id: str = Field(sa_column=_conversation_column())
    turn_id: str
    operation_id: str
    request_fingerprint: str
    status: str
    outcome_json: str | None = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
