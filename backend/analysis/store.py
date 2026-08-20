"""Typed persistence boundary for stateful analysis application services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from analysis.models import (
    AnalysisSessionState,
    BillingIntent,
    BoundRequest,
    ExecutionAttempt,
    ExecutionPlan,
    PendingClarification,
    PersistedExecutionMetadata,
    SemanticRequestRevision,
    TurnReceipt,
    WorkflowTransition,
)


DEFAULT_EXECUTION_LEASE_SECONDS = 180
DEFAULT_EXECUTION_HEARTBEAT_SECONDS = 15
DEFAULT_PROCESSING_RECEIPT_TIMEOUT_SECONDS = 600


@dataclass(frozen=True)
class LoadedAnalysisState:
    state: AnalysisSessionState
    active_revision: SemanticRequestRevision | None
    active_bound_request: BoundRequest | None
    active_clarification: PendingClarification | None
    active_plan: ExecutionPlan | None
    active_attempt: ExecutionAttempt | None
    executions: dict[str, ExecutionAttempt | PersistedExecutionMetadata]


@dataclass(frozen=True)
class TurnStart:
    receipt: TurnReceipt
    duplicate: bool


@dataclass(frozen=True)
class ClaimedExecution:
    state: AnalysisSessionState
    attempt: ExecutionAttempt
    token: str


@dataclass(frozen=True)
class ClaimPlanCommand:
    """A precomputed claim transition and its request-local execution token."""

    transition: WorkflowTransition
    attempt: ExecutionAttempt
    token: str


@dataclass(frozen=True)
class AttemptCompletionCommand:
    """A lifecycle-owned transition that closes one token-authorized attempt."""

    transition: WorkflowTransition
    execution_id: str
    token: str


@dataclass(frozen=True)
class CreateSessionCommand:
    session_id: str
    at: datetime | None = None


@dataclass(frozen=True)
class LoadOrCreateSessionCommand:
    session_id: str


@dataclass(frozen=True)
class BeginTurnCommand:
    session_id: str
    turn_id: str
    request_content: object
    state_version: int


@dataclass(frozen=True)
class HeartbeatAttemptCommand:
    execution_id: str
    token: str
    lease_seconds: int = DEFAULT_EXECUTION_LEASE_SECONDS


@dataclass(frozen=True)
class MarkBillingRecordedCommand:
    session_id: str
    turn_id: str


@dataclass(frozen=True)
class DeleteAnalysisSessionCommand:
    session_id: str


@dataclass(frozen=True)
class SessionDeletionResult:
    session_id: str


@runtime_checkable
class AnalysisStore(Protocol):
    """Atomic persistence operations used while coordinating one turn."""

    def create_session(self, command: CreateSessionCommand) -> AnalysisSessionState: ...

    def load_state(self, session_id: str) -> AnalysisSessionState: ...

    def load_or_create(
        self,
        command: LoadOrCreateSessionCommand,
    ) -> LoadedAnalysisState: ...

    def load(
        self,
        session_id: str,
        *,
        state: AnalysisSessionState | None = None,
    ) -> LoadedAnalysisState: ...

    def begin_turn(self, command: BeginTurnCommand) -> TurnStart: ...

    def commit_transition(
        self,
        transition: WorkflowTransition,
    ) -> AnalysisSessionState: ...

    def commit_plan_claim(self, command: ClaimPlanCommand) -> ClaimedExecution: ...

    def commit_attempt_completion(
        self,
        command: AttemptCompletionCommand,
    ) -> AnalysisSessionState: ...

    def verify_attempt(
        self,
        *,
        execution_id: str,
        token: str,
        plan: ExecutionPlan | None = None,
        require_active: bool = True,
    ) -> ExecutionAttempt: ...

    def heartbeat_attempt(
        self,
        command: HeartbeatAttemptCommand,
    ) -> ExecutionAttempt: ...

    def cancellation_requested(
        self,
        *,
        execution_id: str,
        token: str,
    ) -> bool: ...

    def expired_attempts(
        self,
        *,
        at: datetime | None = None,
        session_id: str | None = None,
    ) -> tuple[ExecutionAttempt, ...]: ...

    def load_revision(
        self,
        session_id: str,
        revision_id: str,
    ) -> SemanticRequestRevision: ...

    def load_bound_request(
        self,
        session_id: str,
        bound_request_id: str,
    ) -> BoundRequest: ...

    def load_plan(self, session_id: str, plan_id: str) -> ExecutionPlan: ...

    def load_attempt(self, execution_id: str) -> ExecutionAttempt: ...

    def load_receipt(self, session_id: str, turn_id: str) -> TurnReceipt: ...

    def mark_billing_recorded(self, command: MarkBillingRecordedCommand) -> bool: ...

    def pending_billing_intents(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> tuple[BillingIntent, ...]: ...

    def delete_session(
        self,
        command: DeleteAnalysisSessionCommand,
    ) -> SessionDeletionResult: ...
