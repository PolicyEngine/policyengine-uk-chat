"""Private SQL row definitions owned by :mod:`analysis.persistence`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, Index, String, Text, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from analysis.common import WORKFLOW_SCHEMA_VERSION


class AnalysisWorkflowRow(SQLModel, table=True):
    __tablename__ = "analysis_workflows"

    session_id: str = Field(primary_key=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    state_version: int = Field(default=0, index=True)
    phase: str = Field(index=True)
    active_bound_request_id: str | None = Field(default=None, index=True)
    active_execution_id: str | None = Field(default=None, index=True)
    pending_plan_id: str | None = Field(default=None, index=True)
    snapshot_json: str = Field(sa_column=Column(Text, nullable=False))
    updated_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisRequestRevisionRow(SQLModel, table=True):
    __tablename__ = "analysis_request_revisions"
    __table_args__ = (
        UniqueConstraint(
            "session_id", "revision_number", name="uq_analysis_revision_number"
        ),
    )

    revision_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    revision_number: int = Field(index=True)
    turn_id: str = Field(index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisBoundRequestRow(SQLModel, table=True):
    __tablename__ = "analysis_bound_requests"

    bound_request_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    request_revision_id: str = Field(index=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    capability_version: str = Field(index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisClarificationRow(SQLModel, table=True):
    __tablename__ = "analysis_clarifications"

    question_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    request_revision_id: str = Field(index=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisClarificationResolutionRow(SQLModel, table=True):
    __tablename__ = "analysis_clarification_resolutions"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "question_id",
            name="uq_analysis_clarification_resolution",
        ),
    )

    resolution_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    question_id: str = Field(index=True)
    request_revision_id: str = Field(index=True)
    resolving_turn_id: str = Field(index=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    outcome: str = Field(index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisPlanRow(SQLModel, table=True):
    __tablename__ = "analysis_plans"

    plan_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    request_revision_id: str = Field(index=True)
    bound_request_id: str = Field(index=True)
    schema_version: int
    plan_hash: str = Field(index=True)
    status: str = Field(default="ready", index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisExecutionAttemptRow(SQLModel, table=True):
    __tablename__ = "analysis_execution_attempts"
    __table_args__ = (
        Index(
            "uq_analysis_active_attempt_session",
            "session_id",
            unique=True,
            postgresql_where=text(
                "status IN ('claimed', 'running', 'cancellation_requested')"
            ),
            sqlite_where=text(
                "status IN ('claimed', 'running', 'cancellation_requested')"
            ),
        ),
    )

    execution_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    request_revision_id: str = Field(index=True)
    bound_request_id: str = Field(index=True)
    plan_id: str = Field(index=True)
    plan_hash: str = Field(index=True)
    token_hash: str = Field(sa_column=Column(String, nullable=False))
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    status: str = Field(index=True)
    worker_id: str = Field(index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    claimed_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    heartbeat_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
    lease_expires_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False, index=True)
    )
    completed_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )


class AnalysisExecutionRow(SQLModel, table=True):
    """Read-only version-one execution metadata."""

    __tablename__ = "analysis_executions"

    execution_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    plan_id: str = Field(index=True)
    schema_version: int = Field(default=1)
    status: str = Field(index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisTurnReceiptRow(SQLModel, table=True):
    __tablename__ = "analysis_turn_receipts"

    session_id: str = Field(primary_key=True)
    turn_id: str = Field(primary_key=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    request_hash: str = Field(index=True)
    state_version: int
    status: str = Field(index=True)
    outcome_category: str | None = Field(default=None, index=True)
    response_content: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    response_metadata_json: str = Field(
        default="{}",
        sa_column=Column(Text, nullable=False, server_default="{}"),
    )
    usage_id: str | None = Field(default=None, index=True)
    response_checksum: str | None = None
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisModelUsageRow(SQLModel, table=True):
    __tablename__ = "analysis_model_usage"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "turn_id",
            "operation",
            "usage_entry_id",
            name="uq_analysis_model_usage_entry",
        ),
    )

    usage_entry_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    turn_id: str = Field(index=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    operation: str = Field(index=True)
    model: str = Field(index=True)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )


class AnalysisBillingIntentRow(SQLModel, table=True):
    __tablename__ = "analysis_billing_intents"
    __table_args__ = (
        UniqueConstraint("session_id", "turn_id", name="uq_analysis_billing_turn"),
    )

    billing_intent_id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    turn_id: str = Field(index=True)
    user_id: str | None = Field(default=None, index=True)
    schema_version: int = Field(default=WORKFLOW_SCHEMA_VERSION)
    status: str = Field(index=True)
    payload_json: str = Field(sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), nullable=False)
    )
