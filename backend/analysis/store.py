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


@runtime_checkable
class AnalysisStore(Protocol):
    """Atomic persistence operations used while coordinating one turn."""

    def create_session(
        self,
        session_id: str,
        *,
        at: datetime | None = None,
    ) -> AnalysisSessionState: ...

    def load_state(self, session_id: str) -> AnalysisSessionState: ...

    def load_or_create(self, session_id: str) -> LoadedAnalysisState: ...

    def load(
        self,
        session_id: str,
        *,
        state: AnalysisSessionState | None = None,
    ) -> LoadedAnalysisState: ...

    def begin_turn(
        self,
        *,
        session_id: str,
        turn_id: str,
        request_content: object,
        state_version: int,
    ) -> TurnStart: ...

    def commit_transition(
        self,
        transition: WorkflowTransition,
    ) -> AnalysisSessionState: ...

    def claim_plan(
        self,
        *,
        session_id: str,
        plan: ExecutionPlan,
        worker_id: str,
        expected_state_version: int,
        lease_seconds: int = 180,
    ) -> ClaimedExecution: ...

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
        *,
        execution_id: str,
        token: str,
        lease_seconds: int = 180,
    ) -> ExecutionAttempt: ...

    def cancellation_requested(
        self,
        *,
        execution_id: str,
        token: str,
    ) -> bool: ...

    def recover_expired_attempts(
        self,
        *,
        at: datetime | None = None,
        session_id: str | None = None,
    ) -> tuple[str, ...]: ...

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

    def mark_billing_recorded(self, session_id: str, turn_id: str) -> bool: ...

    def pending_billing_intents(
        self,
        *,
        user_id: str,
        limit: int = 100,
    ) -> tuple[BillingIntent, ...]: ...
