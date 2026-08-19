"""Atomic persistence for analysis lifecycle records and execution attempts."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, assert_never

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    delete,
    text,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Field, Session, SQLModel, select

from analysis.common import (
    AnalysisError,
    AnalysisErrorCode,
    PLAN_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION,
    canonical_hash,
    stable_identifier,
)
from analysis.lifecycle import (
    AttemptOutcomeEvent,
    LifecycleReducer,
    PlanClaimedEvent,
    RecoveryEvent,
)
from analysis.models import (
    AnalysisSessionState,
    BillingIntent,
    BillingIntentStatus,
    BoundRequest,
    ClarificationResolution,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCompletion,
    ExecutionPlan,
    ExecutionStatusChange,
    FinalizationIntent,
    ModelUsageEntry,
    PendingClarification,
    PlanStatusChange,
    PersistedExecutionMetadata,
    SemanticRequestRevision,
    TransitionStatusChange,
    TurnReceipt,
    TurnReceiptStatus,
    WorkflowPhase,
    WorkflowTransition,
)
from analysis.store import ClaimedExecution, LoadedAnalysisState, TurnStart


ACTIVE_ATTEMPT_STATUSES = (
    ExecutionAttemptStatus.CLAIMED.value,
    ExecutionAttemptStatus.RUNNING.value,
    ExecutionAttemptStatus.CANCELLATION_REQUESTED.value,
)
DEFAULT_EXECUTION_LEASE_SECONDS = 180
DEFAULT_EXECUTION_HEARTBEAT_SECONDS = 15
DEFAULT_PROCESSING_RECEIPT_TIMEOUT_SECONDS = 600

_LEGACY_RESULT_TYPES = {
    "get_parameter": "parameter",
    "validate_reform": "reform_validation",
    "validate_household": "household_validation",
    "run_household_simulation": "household_simulation",
    "run_society_simulation": "society_simulation",
    "compute_budgetary_impact": "budgetary_impact",
    "compute_program_breakdown": "program_breakdown",
    "compute_decile_impacts": "decile_impacts",
    "compute_winners_losers": "winners_losers",
    "compute_poverty_metrics": "poverty_metrics",
    "compute_inequality_metrics": "inequality_metrics",
    "aggregate_result": "aggregate_result",
    "generate_chart": "chart",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _upgrade_payload(model_type, payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt version-one documents without creating historical result data."""

    upgraded = dict(payload)
    upgraded["schema_version"] = WORKFLOW_SCHEMA_VERSION
    if model_type is SemanticRequestRevision:
        upgraded.pop("readiness", None)
    elif model_type is AnalysisSessionState:
        upgraded.setdefault("active_bound_request_id", None)
        upgraded.setdefault("active_execution_id", None)
        upgraded.setdefault("pending_plan_id", None)
    elif model_type is PendingClarification:
        upgraded.setdefault("target_contract", "legacy")
        upgraded.setdefault(
            "choice_mode",
            "advisory" if upgraded.get("permitted_choices") else "open",
        )
    elif model_type is ExecutionPlan:
        upgraded["schema_version"] = PLAN_SCHEMA_VERSION
        upgraded.setdefault(
            "bound_request_id",
            f"bound_legacy_{upgraded.get('request_revision_id', 'unknown')}",
        )
        upgraded.setdefault("capability_version", "1")
        upgraded_steps = []
        for raw_step in upgraded.get("steps", ()):
            step = dict(raw_step)
            step.setdefault(
                "result_type",
                _LEGACY_RESULT_TYPES.get(
                    step.get("operation"),
                    step.get("result_binding", "unknown"),
                ),
            )
            upgraded_steps.append(step)
        upgraded["steps"] = upgraded_steps
        upgraded_constraints = []
        for raw_constraint in upgraded.get("operation_constraints", ()):
            constraint = dict(raw_constraint)
            constraint.setdefault("permitted_dependency_types", ())
            upgraded_constraints.append(constraint)
        upgraded["operation_constraints"] = upgraded_constraints
    return upgraded


def _parse_persisted(model_type, payload_json: str):
    expected_version = (
        PLAN_SCHEMA_VERSION if model_type is ExecutionPlan else WORKFLOW_SCHEMA_VERSION
    )
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError(
            AnalysisErrorCode.STATE_UNAVAILABLE,
            "persisted analysis state is invalid",
        ) from exc
    actual_version = payload.get("schema_version") if isinstance(payload, dict) else None
    if actual_version == 1 and expected_version == 2:
        payload = _upgrade_payload(model_type, payload)
    elif actual_version != expected_version:
        raise AnalysisError(
            AnalysisErrorCode.UNSUPPORTED_SCHEMA,
            (
                f"persisted {model_type.__name__} schema version "
                f"{actual_version!r} is unsupported; expected {expected_version}"
            ),
        )
    try:
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise AnalysisError(
            AnalysisErrorCode.STATE_UNAVAILABLE,
            "persisted analysis state is invalid",
        ) from exc


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
    """Read-only compatibility with version-one execution metadata."""

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


def ensure_analysis_tables(engine=None) -> None:
    try:
        if engine is None:
            from conversations.models import get_engine

            engine = get_engine()
        SQLModel.metadata.create_all(engine)
    except SQLAlchemyError as exc:
        raise AnalysisError(
            AnalysisErrorCode.STATE_UNAVAILABLE,
            "analysis state persistence is unavailable",
            retryable=True,
        ) from exc


class SqlAnalysisStore:
    """SQL implementation of the atomic analysis persistence contract."""

    def __init__(self, engine=None):
        if engine is None:
            from conversations.models import get_engine

            engine = get_engine()
        self.engine = engine

    @staticmethod
    def _state_row(state: AnalysisSessionState) -> AnalysisWorkflowRow:
        return AnalysisWorkflowRow(
            session_id=state.session_id,
            schema_version=state.schema_version,
            state_version=state.state_version,
            phase=state.phase.value,
            active_bound_request_id=state.active_bound_request_id,
            active_execution_id=state.active_execution_id,
            pending_plan_id=state.pending_plan_id,
            snapshot_json=state.model_dump_json(),
            updated_at=state.updated_at,
        )

    def create_session(
        self,
        session_id: str,
        *,
        at: datetime | None = None,
    ) -> AnalysisSessionState:
        state = AnalysisSessionState(session_id=session_id, updated_at=at or _now())
        try:
            with Session(self.engine) as db:
                db.add(self._state_row(state))
                db.commit()
            return state
        except IntegrityError:
            return self.load_state(session_id)
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def load_state(self, session_id: str) -> AnalysisSessionState:
        try:
            with Session(self.engine) as db:
                row = db.get(AnalysisWorkflowRow, session_id)
            if row is None:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "analysis session does not exist",
                )
            return _parse_persisted(AnalysisSessionState, row.snapshot_json)
        except AnalysisError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise self._unavailable(exc) from exc

    def load_or_create(self, session_id: str) -> LoadedAnalysisState:
        try:
            state = self.load_state(session_id)
        except AnalysisError as exc:
            if exc.code != AnalysisErrorCode.STATE_PRECONDITION_FAILED:
                raise
            state = self.create_session(session_id)
        return self.load(session_id, state=state)

    def load(
        self,
        session_id: str,
        *,
        state: AnalysisSessionState | None = None,
    ) -> LoadedAnalysisState:
        state = state or self.load_state(session_id)
        try:
            with Session(self.engine) as db:
                revision_row = (
                    db.get(AnalysisRequestRevisionRow, state.active_revision_id)
                    if state.active_revision_id
                    else None
                )
                bound_row = (
                    db.get(AnalysisBoundRequestRow, state.active_bound_request_id)
                    if state.active_bound_request_id
                    else None
                )
                clarification_row = (
                    db.get(AnalysisClarificationRow, state.active_clarification_id)
                    if state.active_clarification_id
                    else None
                )
                clarification_resolution = (
                    db.exec(
                        select(AnalysisClarificationResolutionRow).where(
                            AnalysisClarificationResolutionRow.session_id == session_id,
                            AnalysisClarificationResolutionRow.question_id
                            == state.active_clarification_id,
                        )
                    ).first()
                    if state.active_clarification_id
                    else None
                )
                plan_row = (
                    db.get(AnalysisPlanRow, state.active_plan_id)
                    if state.active_plan_id
                    else None
                )
                attempt_row = (
                    db.get(AnalysisExecutionAttemptRow, state.active_execution_id)
                    if state.active_execution_id
                    else None
                )
                attempt_rows = db.exec(
                    select(AnalysisExecutionAttemptRow)
                    .where(AnalysisExecutionAttemptRow.session_id == session_id)
                    .order_by(AnalysisExecutionAttemptRow.claimed_at.desc())
                    .limit(20)
                ).all()
                legacy_rows = db.exec(
                    select(AnalysisExecutionRow)
                    .where(AnalysisExecutionRow.session_id == session_id)
                    .order_by(AnalysisExecutionRow.created_at.desc())
                    .limit(20)
                ).all()
            attempts = {
                row.execution_id: _parse_persisted(ExecutionAttempt, row.payload_json)
                for row in attempt_rows
            }
            executions: dict[str, ExecutionAttempt | PersistedExecutionMetadata] = {
                **{
                    row.execution_id: _parse_persisted(
                        PersistedExecutionMetadata, row.payload_json
                    )
                    for row in legacy_rows
                },
                **attempts,
            }
            return LoadedAnalysisState(
                state=state,
                active_revision=(
                    _parse_persisted(SemanticRequestRevision, revision_row.payload_json)
                    if revision_row
                    else None
                ),
                active_bound_request=(
                    _parse_persisted(BoundRequest, bound_row.payload_json)
                    if bound_row
                    else None
                ),
                active_clarification=(
                    _parse_persisted(PendingClarification, clarification_row.payload_json)
                    if clarification_row and clarification_resolution is None
                    else None
                ),
                active_plan=(
                    _parse_persisted(ExecutionPlan, plan_row.payload_json)
                    if plan_row
                    else None
                ),
                active_attempt=(
                    _parse_persisted(ExecutionAttempt, attempt_row.payload_json)
                    if attempt_row
                    else None
                ),
                executions=executions,
            )
        except AnalysisError:
            raise
        except (SQLAlchemyError, ValueError) as exc:
            raise self._unavailable(exc) from exc

    def _validate_transition_records(
        self,
        db: Session,
        transition: WorkflowTransition,
    ) -> None:
        state = transition.next_state
        session_id = state.session_id
        expected_next_version = (
            transition.expected_state_version
            if transition.finalization_intent == FinalizationIntent.RECEIPT_ONLY
            else transition.expected_state_version + 1
        )
        if state.state_version != expected_next_version:
            raise AnalysisError(
                AnalysisErrorCode.STATE_CONFLICT,
                "next state version does not match the transition finalization intent",
                retryable=True,
            )
        if any(record.session_id != session_id for record in transition.revisions):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "transition contains a revision from another session",
            )
        revisions = {record.revision_id: record for record in transition.revisions}
        bounds = {record.bound_request_id: record for record in transition.bound_requests}
        plans = {record.plan_id: record for record in transition.plans}
        attempts = {
            record.execution_id: record for record in transition.execution_attempts
        }
        clarifications = {
            record.question_id: record for record in transition.clarifications
        }
        if any(record.session_id != session_id for record in bounds.values()):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "transition contains a bound request from another session",
            )
        if any(record.session_id != session_id for record in plans.values()):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "transition contains a plan from another session",
            )
        if any(record.session_id != session_id for record in attempts.values()):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "transition contains an execution attempt from another session",
            )
        for clarification in clarifications.values():
            if clarification.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "transition contains a clarification from another session",
                )
            revision = revisions.get(clarification.request_revision_id)
            if revision is None:
                row = db.get(
                    AnalysisRequestRevisionRow,
                    clarification.request_revision_id,
                )
                if row is None or row.session_id != session_id:
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "clarification references an unavailable revision",
                    )
        for resolution in transition.clarification_resolutions:
            if resolution.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "transition contains a clarification resolution from another session",
                )
            clarification = clarifications.get(resolution.question_id)
            if clarification is None:
                row = db.get(AnalysisClarificationRow, resolution.question_id)
                if row is None or row.session_id != session_id:
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "clarification resolution references an unavailable question",
                    )
                clarification_revision_id = row.request_revision_id
            else:
                clarification_revision_id = clarification.request_revision_id
            if clarification_revision_id != resolution.request_revision_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "clarification resolution and question revisions differ",
                )
        for bound in bounds.values():
            revision = revisions.get(bound.request_revision_id)
            if revision is None:
                row = db.get(AnalysisRequestRevisionRow, bound.request_revision_id)
                if row is None or row.session_id != session_id:
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "bound request references an unavailable revision",
                    )
            elif revision.session_id != bound.session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "bound request and revision sessions differ",
                )
        for plan in plans.values():
            bound = bounds.get(plan.bound_request_id)
            if bound is None:
                row = db.get(AnalysisBoundRequestRow, plan.bound_request_id)
                if row is None or row.session_id != session_id:
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "plan references an unavailable bound request",
                    )
                if row.request_revision_id != plan.request_revision_id:
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "plan and bound request revisions differ",
                    )
            elif bound.request_revision_id != plan.request_revision_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "plan and bound request revisions differ",
                )
        for attempt in attempts.values():
            plan = plans.get(attempt.plan_id)
            if plan is None:
                row = db.get(AnalysisPlanRow, attempt.plan_id)
                if row is None or row.session_id != session_id:
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "execution attempt references an unavailable plan",
                    )
                if (
                    row.plan_hash != attempt.plan_hash
                    or row.bound_request_id != attempt.bound_request_id
                    or row.request_revision_id != attempt.request_revision_id
                ):
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "execution attempt does not match its plan",
                    )
        for receipt in transition.turn_receipts:
            if receipt.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "transition contains a turn receipt from another session",
                )
        receipt_turn_ids = {str(item.turn_id) for item in transition.turn_receipts}
        for usage in transition.usage_entries:
            if usage.session_id != session_id or (
                receipt_turn_ids and str(usage.turn_id) not in receipt_turn_ids
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "model usage does not belong to the finalized session and turn",
                )
        usage_ids = {str(item.usage_entry_id) for item in transition.usage_entries}
        usage_by_id = {
            str(item.usage_entry_id): item for item in transition.usage_entries
        }
        for intent in transition.billing_intents:
            if (
                intent.session_id != session_id
                or (receipt_turn_ids and str(intent.turn_id) not in receipt_turn_ids)
                or not set(intent.usage_entry_ids).issubset(usage_ids)
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "billing intent does not match finalized usage",
                )
            for charge_input in intent.charge_inputs:
                usage = usage_by_id.get(charge_input.usage_entry_id)
                if usage is None or any(
                    (
                        charge_input.operation != usage.operation,
                        charge_input.model != usage.model,
                        charge_input.input_tokens != usage.input_tokens,
                        charge_input.output_tokens != usage.output_tokens,
                        charge_input.cache_creation_input_tokens
                        != usage.cache_creation_input_tokens,
                        charge_input.cache_read_input_tokens
                        != usage.cache_read_input_tokens,
                        charge_input.cost_gbp < 0,
                    )
                ):
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "billing charge inputs do not match finalized usage",
                    )

        def require_revision(record_id: str | None) -> AnalysisRequestRevisionRow | SemanticRequestRevision | None:
            if record_id is None:
                return None
            record = revisions.get(record_id) or db.get(
                AnalysisRequestRevisionRow, record_id
            )
            if record is None or record.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "session state references an unavailable revision",
                )
            return record

        def require_bound(record_id: str | None) -> AnalysisBoundRequestRow | BoundRequest | None:
            if record_id is None:
                return None
            record = bounds.get(record_id) or db.get(AnalysisBoundRequestRow, record_id)
            if record is None or record.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "session state references an unavailable bound request",
                )
            return record

        def require_plan(record_id: str | None) -> AnalysisPlanRow | ExecutionPlan | None:
            if record_id is None:
                return None
            record = plans.get(record_id) or db.get(AnalysisPlanRow, record_id)
            if record is None or record.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "session state references an unavailable plan",
                )
            return record

        active_revision = require_revision(state.active_revision_id)
        active_bound = require_bound(state.active_bound_request_id)
        active_plan = require_plan(state.active_plan_id)
        pending_plan = require_plan(state.pending_plan_id)
        if state.active_clarification_id is not None:
            clarification = clarifications.get(state.active_clarification_id) or db.get(
                AnalysisClarificationRow,
                state.active_clarification_id,
            )
            if (
                clarification is None
                or clarification.session_id != session_id
                or clarification.request_revision_id != state.active_revision_id
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "session state references an invalid active clarification",
                )
        if state.active_execution_id is not None:
            attempt = attempts.get(state.active_execution_id) or db.get(
                AnalysisExecutionAttemptRow,
                state.active_execution_id,
            )
            if (
                attempt is None
                or attempt.session_id != session_id
                or attempt.plan_id != state.active_plan_id
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "session state references an invalid active execution attempt",
                )
        if state.phase == WorkflowPhase.READY:
            if active_revision is None or active_bound is None or active_plan is None:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "ready state requires revision, bound request, and plan identifiers",
                )
            if (
                active_bound.request_revision_id != state.active_revision_id
                or active_plan.request_revision_id != state.active_revision_id
                or active_plan.bound_request_id != state.active_bound_request_id
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "ready state record identities disagree",
                )
        if pending_plan is not None and (
            active_revision is None
            or active_bound is None
            or pending_plan.request_revision_id != state.active_revision_id
            or pending_plan.bound_request_id != state.active_bound_request_id
        ):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "pending plan does not belong to the current bound request",
            )
        if state.phase == WorkflowPhase.READY and not all(
            (
                state.active_revision_id,
                state.active_bound_request_id,
                state.active_plan_id,
            )
        ):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "ready state requires revision, bound request, and plan identifiers",
            )
        if state.phase == WorkflowPhase.EXECUTING and not all(
            (state.active_plan_id, state.active_execution_id)
        ):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "executing state requires active plan and attempt identifiers",
            )
        if (
            state.phase == WorkflowPhase.AWAITING_CLARIFICATION
            and not all((state.active_revision_id, state.active_clarification_id))
        ):
            raise AnalysisError(
                AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                "clarification state requires revision and clarification identifiers",
            )

    @staticmethod
    def _attempt_with_status(
        attempt: ExecutionAttempt,
        status: ExecutionAttemptStatus,
        *,
        completion: ExecutionCompletion | None = None,
        completed_at: datetime | None = None,
    ) -> ExecutionAttempt:
        update_values: dict[str, Any] = {"status": status}
        if completion is not None:
            update_values.update(
                {
                    "operations": completion.operations,
                    "error_code": completion.error_code,
                    "completed_at": completed_at or _now(),
                }
            )
        return ExecutionAttempt.model_validate(
            {
                **attempt.model_dump(),
                **update_values,
            }
        )

    def _apply_status_change(
        self,
        db: Session,
        transition: WorkflowTransition,
        change: TransitionStatusChange,
    ) -> None:
        if isinstance(change, PlanStatusChange):
            query = update(AnalysisPlanRow).where(
                AnalysisPlanRow.plan_id == change.plan_id,
                AnalysisPlanRow.session_id == transition.next_state.session_id,
            )
            if change.expected_status is not None:
                query = query.where(
                    AnalysisPlanRow.status == change.expected_status.value
                )
            result = db.exec(query.values(status=change.next_status.value))
            if result.rowcount != 1:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_CONFLICT,
                    "plan status changed before the transition committed",
                    retryable=True,
                )
            return
        if isinstance(change, ExecutionStatusChange):
            row = db.get(AnalysisExecutionAttemptRow, change.execution_id)
            if row is None or row.session_id != transition.next_state.session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "execution status update references an unavailable attempt",
                )
            if (
                change.expected_status is not None
                and row.status != change.expected_status.value
            ):
                raise AnalysisError(
                    AnalysisErrorCode.STATE_CONFLICT,
                    "execution status changed before the transition committed",
                    retryable=True,
                )
            attempt = _parse_persisted(ExecutionAttempt, row.payload_json)
            completion = next(
                (
                    item
                    for item in transition.execution_completions
                    if item.execution_id == attempt.execution_id
                ),
                None,
            )
            updated = self._attempt_with_status(
                attempt,
                change.next_status,
                completion=completion,
                completed_at=transition.next_state.updated_at,
            )
            result = db.exec(
                self._execution_status_update_query(
                    change=change,
                    observed_status=row.status,
                )
                .values(
                    status=change.next_status.value,
                    payload_json=updated.model_dump_json(),
                    completed_at=updated.completed_at,
                )
            )
            if result.rowcount != 1:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_CONFLICT,
                    "execution status or lease changed before the transition committed",
                    retryable=True,
                )
            return
        assert_never(change)

    @staticmethod
    def _execution_status_update_query(
        *,
        change: ExecutionStatusChange,
        observed_status: str,
    ):
        query = update(AnalysisExecutionAttemptRow).where(
            AnalysisExecutionAttemptRow.execution_id == change.execution_id,
            AnalysisExecutionAttemptRow.status == observed_status,
        )
        if change.expected_lease_expires_at is not None:
            query = query.where(
                AnalysisExecutionAttemptRow.lease_expires_at
                == change.expected_lease_expires_at
            )
        return query

    def commit_transition(
        self,
        transition: WorkflowTransition,
    ) -> AnalysisSessionState:
        try:
            with Session(self.engine) as db:
                workflow = db.get(
                    AnalysisWorkflowRow,
                    transition.next_state.session_id,
                )
                if workflow is None:
                    raise AnalysisError(
                        AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                        "analysis session does not exist",
                    )
                current = _parse_persisted(
                    AnalysisSessionState,
                    workflow.snapshot_json,
                )
                receipt_only = (
                    transition.finalization_intent == FinalizationIntent.RECEIPT_ONLY
                )
                if not receipt_only and (
                    workflow.state_version != transition.expected_state_version
                    or current.state_version != transition.expected_state_version
                    or current.phase != transition.current_phase
                ):
                    raise AnalysisError(
                        AnalysisErrorCode.STATE_CONFLICT,
                        "analysis session changed before the transition committed",
                        retryable=True,
                    )
                if receipt_only:
                    receipt = (
                        transition.turn_receipts[0]
                        if len(transition.turn_receipts) == 1
                        else None
                    )
                    if (
                        transition.revisions
                        or transition.bound_requests
                        or transition.clarifications
                        or transition.clarification_resolutions
                        or transition.plans
                        or transition.execution_attempts
                        or transition.execution_completions
                        or transition.billing_intents
                        or transition.status_changes
                        or receipt is None
                        or transition.next_state.session_id != current.session_id
                        or receipt.session_id != current.session_id
                        or any(
                            usage.session_id != current.session_id
                            or usage.turn_id != receipt.turn_id
                            for usage in transition.usage_entries
                        )
                    ):
                        raise AnalysisError(
                            AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                            "receipt-only finalization contains lifecycle mutations",
                        )
                else:
                    self._validate_transition_records(db, transition)

                for revision in transition.revisions:
                    db.add(
                        AnalysisRequestRevisionRow(
                            revision_id=revision.revision_id,
                            session_id=revision.session_id,
                            schema_version=revision.schema_version,
                            revision_number=revision.revision_number,
                            turn_id=revision.turn_id,
                            payload_json=revision.model_dump_json(),
                            created_at=revision.created_at,
                        )
                    )
                if transition.revisions:
                    db.flush()
                for bound in transition.bound_requests:
                    db.add(
                        AnalysisBoundRequestRow(
                            bound_request_id=bound.bound_request_id,
                            session_id=bound.session_id,
                            request_revision_id=bound.request_revision_id,
                            schema_version=bound.schema_version,
                            capability_version=bound.capability_version,
                            payload_json=bound.model_dump_json(),
                            created_at=bound.created_at,
                        )
                    )
                if transition.bound_requests:
                    db.flush()
                for clarification in transition.clarifications:
                    db.add(
                        AnalysisClarificationRow(
                            question_id=clarification.question_id,
                            session_id=clarification.session_id,
                            request_revision_id=clarification.request_revision_id,
                            schema_version=clarification.schema_version,
                            payload_json=clarification.model_dump_json(),
                            created_at=clarification.created_at,
                        )
                    )
                if transition.clarifications:
                    db.flush()
                for resolution in transition.clarification_resolutions:
                    db.add(
                        AnalysisClarificationResolutionRow(
                            resolution_id=resolution.resolution_id,
                            session_id=resolution.session_id,
                            question_id=resolution.question_id,
                            request_revision_id=resolution.request_revision_id,
                            resolving_turn_id=resolution.resolving_turn_id,
                            schema_version=resolution.schema_version,
                            outcome=resolution.outcome.value,
                            payload_json=resolution.model_dump_json(),
                            created_at=resolution.created_at,
                        )
                    )
                if transition.clarification_resolutions:
                    db.flush()
                for plan in transition.plans:
                    db.add(
                        AnalysisPlanRow(
                            plan_id=plan.plan_id,
                            session_id=plan.session_id,
                            request_revision_id=plan.request_revision_id,
                            bound_request_id=plan.bound_request_id,
                            schema_version=plan.schema_version,
                            plan_hash=plan.plan_hash,
                            status="ready",
                            payload_json=plan.model_dump_json(),
                            created_at=plan.created_at,
                        )
                    )
                if transition.plans:
                    db.flush()
                for attempt in transition.execution_attempts:
                    active = db.exec(
                        select(AnalysisExecutionAttemptRow).where(
                            AnalysisExecutionAttemptRow.session_id == attempt.session_id,
                            AnalysisExecutionAttemptRow.status.in_(ACTIVE_ATTEMPT_STATUSES),
                        )
                    ).first()
                    if active is not None:
                        raise AnalysisError(
                            AnalysisErrorCode.EXECUTION_CONFLICT,
                            "another execution attempt is already active",
                            retryable=True,
                        )
                    db.add(
                        AnalysisExecutionAttemptRow(
                            execution_id=attempt.execution_id,
                            session_id=attempt.session_id,
                            request_revision_id=attempt.request_revision_id,
                            bound_request_id=attempt.bound_request_id,
                            plan_id=attempt.plan_id,
                            plan_hash=attempt.plan_hash,
                            token_hash=attempt.token_hash,
                            schema_version=attempt.schema_version,
                            status=attempt.status.value,
                            worker_id=attempt.worker_id,
                            payload_json=attempt.model_dump_json(),
                            claimed_at=attempt.claimed_at,
                            heartbeat_at=attempt.heartbeat_at,
                            lease_expires_at=attempt.lease_expires_at,
                            completed_at=attempt.completed_at,
                        )
                    )
                if transition.execution_attempts:
                    db.flush()

                for change in transition.status_changes:
                    self._apply_status_change(db, transition, change)

                for receipt in transition.turn_receipts:
                    result = db.exec(
                        update(AnalysisTurnReceiptRow)
                        .where(
                            AnalysisTurnReceiptRow.session_id == receipt.session_id,
                            AnalysisTurnReceiptRow.turn_id == receipt.turn_id,
                            AnalysisTurnReceiptRow.request_hash == receipt.request_hash,
                            AnalysisTurnReceiptRow.status
                            == TurnReceiptStatus.PROCESSING.value,
                        )
                        .values(
                            schema_version=receipt.schema_version,
                            state_version=(
                                current.state_version
                                if receipt_only
                                else receipt.state_version
                            ),
                            status=receipt.status.value,
                            outcome_category=receipt.outcome_category,
                            response_content=receipt.response_content,
                            response_metadata_json=json.dumps(
                                receipt.response_metadata,
                                ensure_ascii=False,
                            ),
                            usage_id=receipt.usage_id,
                            response_checksum=receipt.response_checksum,
                        )
                    )
                    if result.rowcount != 1:
                        raise AnalysisError(
                            AnalysisErrorCode.STATE_CONFLICT,
                            "turn receipt changed before finalization",
                            retryable=True,
                        )
                for usage in transition.usage_entries:
                    db.add(
                        AnalysisModelUsageRow(
                            usage_entry_id=usage.usage_entry_id,
                            session_id=usage.session_id,
                            turn_id=usage.turn_id,
                            schema_version=usage.schema_version,
                            operation=usage.operation,
                            model=usage.model,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            cache_creation_input_tokens=usage.cache_creation_input_tokens,
                            cache_read_input_tokens=usage.cache_read_input_tokens,
                            created_at=usage.created_at,
                        )
                    )
                for intent in transition.billing_intents:
                    db.add(
                        AnalysisBillingIntentRow(
                            billing_intent_id=intent.billing_intent_id,
                            session_id=intent.session_id,
                            turn_id=intent.turn_id,
                            user_id=intent.user_id,
                            schema_version=intent.schema_version,
                            status=intent.status.value,
                            payload_json=intent.model_dump_json(),
                            created_at=intent.created_at,
                        )
                    )

                if receipt_only:
                    db.commit()
                    return current

                state = transition.next_state
                result = db.exec(
                    update(AnalysisWorkflowRow)
                    .where(
                        AnalysisWorkflowRow.session_id == state.session_id,
                        AnalysisWorkflowRow.state_version
                        == transition.expected_state_version,
                        AnalysisWorkflowRow.phase == transition.current_phase.value,
                    )
                    .values(
                        schema_version=state.schema_version,
                        state_version=state.state_version,
                        phase=state.phase.value,
                        active_bound_request_id=state.active_bound_request_id,
                        active_execution_id=state.active_execution_id,
                        pending_plan_id=state.pending_plan_id,
                        snapshot_json=state.model_dump_json(),
                        updated_at=state.updated_at,
                    )
                )
                if result.rowcount != 1:
                    raise AnalysisError(
                        AnalysisErrorCode.STATE_CONFLICT,
                        "analysis session changed before the transition committed",
                        retryable=True,
                    )
                db.commit()
            return transition.next_state
        except AnalysisError:
            raise
        except IntegrityError as exc:
            raise AnalysisError(
                AnalysisErrorCode.STATE_CONFLICT,
                "atomic analysis transition violated a record identity constraint",
                retryable=True,
            ) from exc
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def claim_plan(
        self,
        *,
        session_id: str,
        plan: ExecutionPlan,
        worker_id: str,
        expected_state_version: int,
        lease_seconds: int = DEFAULT_EXECUTION_LEASE_SECONDS,
    ) -> ClaimedExecution:
        state = self.load_state(session_id)
        if state.state_version != expected_state_version:
            raise AnalysisError(
                AnalysisErrorCode.STATE_CONFLICT,
                "analysis session changed before plan claim",
                retryable=True,
            )
        token = secrets.token_urlsafe(32)
        claimed_at = _now()
        event = PlanClaimedEvent(
            plan=plan,
            execution_id=stable_identifier(
                "execution",
                session_id,
                plan.plan_id,
                secrets.token_hex(16),
            ),
            token_hash=_token_hash(token),
            worker_id=worker_id,
            claimed_at=claimed_at,
            lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
        )
        transition = LifecycleReducer.reduce(state, event)
        next_state = self.commit_transition(transition)
        attempt = transition.execution_attempts[0]
        return ClaimedExecution(state=next_state, attempt=attempt, token=token)

    def verify_attempt(
        self,
        *,
        execution_id: str,
        token: str,
        plan: ExecutionPlan | None = None,
        require_active: bool = True,
    ) -> ExecutionAttempt:
        attempt = self.load_attempt(execution_id)
        if not hmac.compare_digest(attempt.token_hash, _token_hash(token)):
            raise AnalysisError(
                AnalysisErrorCode.EXECUTION_TOKEN_INVALID,
                "execution token is unknown or invalid",
            )
        if plan is not None and (
            attempt.plan_id != plan.plan_id
            or attempt.plan_hash != plan.plan_hash
            or attempt.bound_request_id != plan.bound_request_id
        ):
            raise AnalysisError(
                AnalysisErrorCode.EXECUTION_TOKEN_INVALID,
                "execution token does not authorize this plan",
            )
        if require_active and not attempt.status.is_active:
            raise AnalysisError(
                AnalysisErrorCode.EXECUTION_TOKEN_INVALID,
                "execution attempt is no longer active",
            )
        if attempt.lease_expires_at < _now():
            raise AnalysisError(
                AnalysisErrorCode.EXECUTION_EXPIRED,
                "execution attempt lease has expired",
            )
        return attempt

    def heartbeat_attempt(
        self,
        *,
        execution_id: str,
        token: str,
        lease_seconds: int = DEFAULT_EXECUTION_LEASE_SECONDS,
    ) -> ExecutionAttempt:
        attempt = self.verify_attempt(execution_id=execution_id, token=token)
        now = _now()
        updated_attempt = attempt.model_copy(
            update={
                "heartbeat_at": now,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
            }
        )
        try:
            with Session(self.engine) as db:
                result = db.exec(
                    update(AnalysisExecutionAttemptRow)
                    .where(
                        AnalysisExecutionAttemptRow.execution_id == execution_id,
                        AnalysisExecutionAttemptRow.token_hash == attempt.token_hash,
                        AnalysisExecutionAttemptRow.status == attempt.status.value,
                    )
                    .values(
                        heartbeat_at=updated_attempt.heartbeat_at,
                        lease_expires_at=updated_attempt.lease_expires_at,
                        payload_json=updated_attempt.model_dump_json(),
                    )
                )
                if result.rowcount != 1:
                    raise AnalysisError(
                        AnalysisErrorCode.EXECUTION_CONFLICT,
                        "execution attempt changed before heartbeat",
                        retryable=True,
                    )
                db.commit()
            return updated_attempt
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def cancellation_requested(self, *, execution_id: str, token: str) -> bool:
        attempt = self.verify_attempt(
            execution_id=execution_id,
            token=token,
            require_active=False,
        )
        return attempt.status in {
            ExecutionAttemptStatus.CANCELLATION_REQUESTED,
            ExecutionAttemptStatus.CANCELLED,
            ExecutionAttemptStatus.SUPERSEDED,
            ExecutionAttemptStatus.EXPIRED,
        }

    def finish_attempt(
        self,
        *,
        state: AnalysisSessionState,
        attempt: ExecutionAttempt,
        token: str,
        completion: ExecutionCompletion,
        completed_at: datetime | None = None,
    ) -> AnalysisSessionState:
        current_attempt = self.verify_attempt(
            execution_id=attempt.execution_id,
            token=token,
            require_active=True,
        )
        current_state = self.load_state(state.session_id)
        event = AttemptOutcomeEvent(
            attempt=current_attempt,
            completion=completion,
            completed_at=completed_at or _now(),
        )
        transition = LifecycleReducer.reduce(current_state, event)
        return self.commit_transition(transition)

    def recover_expired_attempts(
        self,
        *,
        at: datetime | None = None,
        session_id: str | None = None,
    ) -> tuple[str, ...]:
        cutoff = at or _now()
        recovered: list[str] = []
        try:
            with Session(self.engine) as db:
                query = select(AnalysisExecutionAttemptRow).where(
                    AnalysisExecutionAttemptRow.status.in_(ACTIVE_ATTEMPT_STATUSES),
                    AnalysisExecutionAttemptRow.lease_expires_at <= cutoff,
                )
                if session_id is not None:
                    query = query.where(
                        AnalysisExecutionAttemptRow.session_id == session_id
                    )
                rows = db.exec(query).all()
            for row in rows:
                attempt = _parse_persisted(ExecutionAttempt, row.payload_json)
                state = self.load_state(attempt.session_id)
                if state.active_execution_id != attempt.execution_id:
                    continue
                transition = LifecycleReducer.reduce(
                    state,
                    RecoveryEvent(attempt=attempt, recovered_at=cutoff),
                )
                try:
                    self.commit_transition(transition)
                except AnalysisError as exc:
                    if exc.code == AnalysisErrorCode.STATE_CONFLICT:
                        # A heartbeat, completion, cancellation, or newer
                        # conversation transition won after the expired row was
                        # selected.  That current state is authoritative.
                        continue
                    raise
                recovered.append(attempt.execution_id)
            return tuple(recovered)
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def begin_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        request_content: object,
        state_version: int,
    ) -> TurnStart:
        request_hash = canonical_hash(request_content)
        receipt = TurnReceipt(
            session_id=session_id,
            turn_id=turn_id,
            request_hash=request_hash,
            state_version=state_version,
            status=TurnReceiptStatus.PROCESSING,
            created_at=_now(),
        )
        try:
            with Session(self.engine) as db:
                existing = db.get(AnalysisTurnReceiptRow, (session_id, turn_id))
                if existing:
                    restored = self._receipt(existing)
                    if restored.request_hash != request_hash:
                        raise AnalysisError(
                            AnalysisErrorCode.IDEMPOTENCY_CONFLICT,
                            "turn identifier was reused with different request content",
                        )
                    return TurnStart(receipt=restored, duplicate=True)
                db.add(self._receipt_row(receipt))
                db.commit()
            return TurnStart(receipt=receipt, duplicate=False)
        except AnalysisError:
            raise
        except IntegrityError:
            return self.begin_turn(
                session_id=session_id,
                turn_id=turn_id,
                request_content=request_content,
                state_version=state_version,
            )
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def load_receipt(self, session_id: str, turn_id: str) -> TurnReceipt:
        try:
            with Session(self.engine) as db:
                row = db.get(AnalysisTurnReceiptRow, (session_id, turn_id))
            if row is None:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "turn receipt does not exist",
                )
            return self._receipt(row)
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def load_revision(
        self,
        session_id: str,
        revision_id: str,
    ) -> SemanticRequestRevision:
        try:
            with Session(self.engine) as db:
                row = db.get(AnalysisRequestRevisionRow, revision_id)
            if row is None or row.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "semantic request revision is unavailable in this session",
                )
            return _parse_persisted(SemanticRequestRevision, row.payload_json)
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def load_bound_request(
        self,
        session_id: str,
        bound_request_id: str,
    ) -> BoundRequest:
        try:
            with Session(self.engine) as db:
                row = db.get(AnalysisBoundRequestRow, bound_request_id)
            if row is None or row.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "bound request is unavailable in this session",
                )
            return _parse_persisted(BoundRequest, row.payload_json)
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def load_plan(self, session_id: str, plan_id: str) -> ExecutionPlan:
        try:
            with Session(self.engine) as db:
                row = db.get(AnalysisPlanRow, plan_id)
            if row is None or row.session_id != session_id:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "execution plan is unavailable in this session",
                )
            return _parse_persisted(ExecutionPlan, row.payload_json)
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def load_attempt(self, execution_id: str) -> ExecutionAttempt:
        try:
            with Session(self.engine) as db:
                row = db.get(AnalysisExecutionAttemptRow, execution_id)
            if row is None:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "execution attempt is unavailable",
                )
            return _parse_persisted(ExecutionAttempt, row.payload_json)
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def mark_billing_recorded(self, session_id: str, turn_id: str) -> bool:
        try:
            with Session(self.engine) as db:
                row = db.exec(
                    select(AnalysisBillingIntentRow).where(
                        AnalysisBillingIntentRow.session_id == session_id,
                        AnalysisBillingIntentRow.turn_id == turn_id,
                    )
                ).first()
                if row is None:
                    return False
                if row.status == BillingIntentStatus.RECORDED.value:
                    return True
                intent = _parse_persisted(BillingIntent, row.payload_json)
                updated = intent.model_copy(
                    update={"status": BillingIntentStatus.RECORDED}
                )
                result = db.exec(
                    update(AnalysisBillingIntentRow)
                    .where(
                        AnalysisBillingIntentRow.billing_intent_id
                        == row.billing_intent_id,
                        AnalysisBillingIntentRow.status
                        == BillingIntentStatus.PENDING.value,
                    )
                    .values(
                        status=BillingIntentStatus.RECORDED.value,
                        payload_json=updated.model_dump_json(),
                    )
                )
                if result.rowcount not in {0, 1}:
                    raise AnalysisError(
                        AnalysisErrorCode.STATE_CONFLICT,
                        "billing intent changed unexpectedly",
                        retryable=True,
                    )
                db.commit()
                return True
        except AnalysisError:
            raise
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def pending_billing_intents(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> tuple[BillingIntent, ...]:
        try:
            with Session(self.engine) as db:
                rows = db.exec(
                    select(AnalysisBillingIntentRow)
                    .where(
                        AnalysisBillingIntentRow.user_id == user_id,
                        AnalysisBillingIntentRow.status
                        == BillingIntentStatus.PENDING.value,
                    )
                    .order_by(AnalysisBillingIntentRow.created_at)
                    .limit(limit)
                ).all()
            return tuple(
                _parse_persisted(BillingIntent, row.payload_json) for row in rows
            )
        except SQLAlchemyError as exc:
            raise self._unavailable(exc) from exc

    def delete_session(self, session_id: str, *, db: Session | None = None) -> None:
        owns_session = db is None
        db = db or Session(self.engine)
        try:
            for table in (
                AnalysisBillingIntentRow,
                AnalysisModelUsageRow,
                AnalysisTurnReceiptRow,
                AnalysisExecutionAttemptRow,
                AnalysisExecutionRow,
                AnalysisPlanRow,
                AnalysisClarificationResolutionRow,
                AnalysisClarificationRow,
                AnalysisBoundRequestRow,
                AnalysisRequestRevisionRow,
                AnalysisWorkflowRow,
            ):
                db.exec(delete(table).where(table.session_id == session_id))
            if owns_session:
                db.commit()
        except SQLAlchemyError as exc:
            if owns_session:
                db.rollback()
            raise self._unavailable(exc) from exc
        finally:
            if owns_session:
                db.close()

    @staticmethod
    def deterministic_turn_id(session_id: str, request_content: object) -> str:
        return stable_identifier("turn", session_id, canonical_hash(request_content))

    @staticmethod
    def _receipt(row: AnalysisTurnReceiptRow) -> TurnReceipt:
        return TurnReceipt(
            schema_version=WORKFLOW_SCHEMA_VERSION,
            session_id=row.session_id,
            turn_id=row.turn_id,
            request_hash=row.request_hash,
            state_version=row.state_version,
            status=TurnReceiptStatus(row.status),
            outcome_category=row.outcome_category,
            response_content=row.response_content,
            response_metadata=json.loads(row.response_metadata_json or "{}"),
            usage_id=row.usage_id,
            response_checksum=row.response_checksum,
            created_at=row.created_at,
        )

    @staticmethod
    def _receipt_row(receipt: TurnReceipt) -> AnalysisTurnReceiptRow:
        return AnalysisTurnReceiptRow(
            session_id=receipt.session_id,
            turn_id=receipt.turn_id,
            schema_version=receipt.schema_version,
            request_hash=receipt.request_hash,
            state_version=receipt.state_version,
            status=receipt.status.value,
            outcome_category=receipt.outcome_category,
            response_content=receipt.response_content,
            response_metadata_json=json.dumps(
                receipt.response_metadata,
                ensure_ascii=False,
            ),
            usage_id=receipt.usage_id,
            response_checksum=receipt.response_checksum,
            created_at=receipt.created_at,
        )

    @staticmethod
    def _unavailable(exc: Exception) -> AnalysisError:
        return AnalysisError(
            AnalysisErrorCode.STATE_UNAVAILABLE,
            "analysis state persistence is unavailable",
            retryable=True,
        )


# Temporary import compatibility while callers migrate to the explicit SQL name.
AnalysisStateStore = SqlAnalysisStore
