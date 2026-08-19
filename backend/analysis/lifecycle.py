"""Pure analysis-session lifecycle transitions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, TypeVar, assert_never

from pydantic import Field

from analysis.common import AnalysisError, AnalysisErrorCode, stable_identifier
from analysis.models import (
    AnalysisSessionState,
    BillingIntent,
    BoundRequest,
    ClarificationResolution,
    ClarificationResolutionOutcome,
    BoundRequestId,
    ClarificationId,
    ExecutionId,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCompletion,
    ExecutionPlan,
    ExecutionStatusChange,
    FinalizationIntent,
    FrozenModel,
    ModelUsageEntry,
    PendingClarification,
    PlanId,
    PlanStatus,
    PlanStatusChange,
    RevisionId,
    SemanticRequestRevision,
    TransitionStatusChange,
    TurnReceipt,
    WorkflowPhase,
    WorkflowTransition,
)


class ClarificationRequiredEvent(FrozenModel):
    kind: Literal["clarification_required"] = "clarification_required"
    revision: SemanticRequestRevision
    clarification: PendingClarification
    prior_clarification: PendingClarification | None = None
    resolving_turn_id: str | None = None
    answer_submitted: bool = False


class RequestRejectedEvent(FrozenModel):
    kind: Literal["request_rejected"] = "request_rejected"
    revision: SemanticRequestRevision
    outcome: Literal["unsupported", "failed"]
    prior_clarification: PendingClarification | None = None
    resolving_turn_id: str | None = None
    answer_submitted: bool = False


class TurnFailedEvent(FrozenModel):
    kind: Literal["turn_failed"] = "turn_failed"
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class PlanReadyEvent(FrozenModel):
    kind: Literal["plan_ready"] = "plan_ready"
    revision: SemanticRequestRevision
    bound_request: BoundRequest
    plan: ExecutionPlan
    prior_clarification: PendingClarification | None = None
    resolving_turn_id: str | None = None
    answer_submitted: bool = False


class ConversationAdvancedEvent(FrozenModel):
    kind: Literal["conversation_advanced"] = "conversation_advanced"
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class ConflictObservedEvent(FrozenModel):
    kind: Literal["conflict_observed"] = "conflict_observed"


class CancellationRequestedEvent(FrozenModel):
    kind: Literal["cancellation_requested"] = "cancellation_requested"
    request_revision_id: RevisionId | None = None
    prior_clarification: PendingClarification | None = None
    resolving_turn_id: str | None = None
    occurred_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class PlanClaimedEvent(FrozenModel):
    kind: Literal["plan_claimed"] = "plan_claimed"
    plan: ExecutionPlan
    execution_id: ExecutionId
    token_hash: str
    worker_id: str
    claimed_at: datetime
    lease_expires_at: datetime


class AttemptOutcomeEvent(FrozenModel):
    kind: Literal["attempt_outcome"] = "attempt_outcome"
    attempt: ExecutionAttempt
    completion: ExecutionCompletion
    completed_at: datetime


class ExplanationOutcomeEvent(FrozenModel):
    kind: Literal["explanation_outcome"] = "explanation_outcome"
    plan_id: PlanId
    status: Literal["completed", "failed"]
    completed_at: datetime


class RecoveryEvent(FrozenModel):
    kind: Literal["recovery"] = "recovery"
    attempt: ExecutionAttempt
    recovered_at: datetime
    outcome: Literal["failed", "cancelled", "superseded", "expired"] = "expired"


LifecycleEvent = Annotated[
    ClarificationRequiredEvent
    | RequestRejectedEvent
    | TurnFailedEvent
    | PlanReadyEvent
    | ConversationAdvancedEvent
    | ConflictObservedEvent
    | CancellationRequestedEvent
    | PlanClaimedEvent
    | AttemptOutcomeEvent
    | ExplanationOutcomeEvent
    | RecoveryEvent,
    Field(discriminator="kind"),
]


class _Unchanged:
    __slots__ = ()


_UNCHANGED = _Unchanged()
_StateValue = TypeVar("_StateValue")


def _resolve_state_value(
    current: _StateValue,
    replacement: _StateValue | _Unchanged,
) -> _StateValue:
    if isinstance(replacement, _Unchanged):
        return current
    return replacement


def _next_state(
    current: AnalysisSessionState,
    *,
    now: datetime,
    phase: WorkflowPhase | _Unchanged = _UNCHANGED,
    active_revision_id: RevisionId | None | _Unchanged = _UNCHANGED,
    active_bound_request_id: BoundRequestId | None | _Unchanged = _UNCHANGED,
    active_clarification_id: ClarificationId | None | _Unchanged = _UNCHANGED,
    active_plan_id: PlanId | None | _Unchanged = _UNCHANGED,
    active_execution_id: ExecutionId | None | _Unchanged = _UNCHANGED,
    pending_plan_id: PlanId | None | _Unchanged = _UNCHANGED,
    latest_execution_id: ExecutionId | None | _Unchanged = _UNCHANGED,
) -> AnalysisSessionState:
    return AnalysisSessionState(
        schema_version=current.schema_version,
        session_id=current.session_id,
        state_version=current.state_version + 1,
        phase=_resolve_state_value(current.phase, phase),
        active_revision_id=_resolve_state_value(
            current.active_revision_id,
            active_revision_id,
        ),
        active_bound_request_id=_resolve_state_value(
            current.active_bound_request_id,
            active_bound_request_id,
        ),
        active_clarification_id=_resolve_state_value(
            current.active_clarification_id,
            active_clarification_id,
        ),
        active_plan_id=_resolve_state_value(
            current.active_plan_id,
            active_plan_id,
        ),
        active_execution_id=_resolve_state_value(
            current.active_execution_id,
            active_execution_id,
        ),
        pending_plan_id=_resolve_state_value(
            current.pending_plan_id,
            pending_plan_id,
        ),
        latest_execution_id=_resolve_state_value(
            current.latest_execution_id,
            latest_execution_id,
        ),
        updated_at=now,
    )


def _transition(
    current: AnalysisSessionState,
    next_state: AnalysisSessionState,
    *,
    finalization_intent: FinalizationIntent = FinalizationIntent.COMMIT_TRANSITION,
    revisions: tuple[SemanticRequestRevision, ...] = (),
    bound_requests: tuple[BoundRequest, ...] = (),
    clarifications: tuple[PendingClarification, ...] = (),
    clarification_resolutions: tuple[ClarificationResolution, ...] = (),
    plans: tuple[ExecutionPlan, ...] = (),
    execution_attempts: tuple[ExecutionAttempt, ...] = (),
    execution_completions: tuple[ExecutionCompletion, ...] = (),
    turn_receipts: tuple[TurnReceipt, ...] = (),
    usage_entries: tuple[ModelUsageEntry, ...] = (),
    billing_intents: tuple[BillingIntent, ...] = (),
    status_changes: tuple[TransitionStatusChange, ...] = (),
) -> WorkflowTransition:
    return WorkflowTransition(
        expected_state_version=current.state_version,
        current_phase=current.phase,
        next_state=next_state,
        finalization_intent=finalization_intent,
        revisions=revisions,
        bound_requests=bound_requests,
        clarifications=clarifications,
        clarification_resolutions=clarification_resolutions,
        plans=plans,
        execution_attempts=execution_attempts,
        execution_completions=execution_completions,
        turn_receipts=turn_receipts,
        usage_entries=usage_entries,
        billing_intents=billing_intents,
        status_changes=status_changes,
    )


def _validate_revision_records(
    revision: SemanticRequestRevision,
    bound_request: BoundRequest | None = None,
    plan: ExecutionPlan | None = None,
) -> None:
    if bound_request is not None and (
        bound_request.session_id != revision.session_id
        or bound_request.request_revision_id != revision.revision_id
    ):
        raise AnalysisError(
            AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
            "bound request does not belong to the semantic revision",
        )
    if plan is not None and (
        bound_request is None
        or plan.session_id != revision.session_id
        or plan.request_revision_id != revision.revision_id
        or plan.bound_request_id != bound_request.bound_request_id
    ):
        raise AnalysisError(
            AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
            "execution plan does not belong to the bound request",
        )


def _resolve_clarification(
    current: AnalysisSessionState,
    clarification: PendingClarification | None,
    *,
    resolving_turn_id: str | None,
    outcome: ClarificationResolutionOutcome,
    created_at: datetime,
) -> tuple[ClarificationResolution, ...]:
    if clarification is None:
        return ()
    if (
        resolving_turn_id is None
        or current.active_clarification_id != clarification.question_id
        or clarification.session_id != current.session_id
    ):
        raise AnalysisError(
            AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
            "clarification resolution does not reference the active clarification",
        )
    return (
        ClarificationResolution(
            resolution_id=stable_identifier(
                "clarification_resolution",
                clarification.session_id,
                clarification.question_id,
                resolving_turn_id,
                outcome.value,
            ),
            session_id=clarification.session_id,
            question_id=clarification.question_id,
            request_revision_id=clarification.request_revision_id,
            resolving_turn_id=resolving_turn_id,
            outcome=outcome,
            created_at=created_at,
        ),
    )
class LifecycleReducer:
    """The only component that creates the next analysis-session state."""

    @staticmethod
    def reduce(
        current: AnalysisSessionState,
        event: LifecycleEvent,
    ) -> WorkflowTransition:
        if isinstance(event, ConversationAdvancedEvent):
            return _transition(
                current,
                _next_state(current, now=event.occurred_at),
            )

        if isinstance(event, ConflictObservedEvent):
            return _transition(
                current,
                current,
                finalization_intent=FinalizationIntent.RECEIPT_ONLY,
            )

        if isinstance(event, TurnFailedEvent):
            return _transition(
                current,
                _next_state(
                    current,
                    now=event.occurred_at,
                    phase=(
                        current.phase
                        if current.active_execution_id is not None
                        else WorkflowPhase.FAILED
                    ),
                ),
            )

        if isinstance(event, ClarificationRequiredEvent):
            _validate_revision_records(event.revision)
            if event.revision.session_id != current.session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "semantic revision belongs to another analysis session",
                )
            if event.clarification.request_revision_id != event.revision.revision_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "clarification does not belong to the new revision",
                )
            executing = current.active_execution_id is not None
            clarification = event.clarification
            if event.prior_clarification is not None and event.answer_submitted:
                same_target = (
                    clarification.target_field
                    == event.prior_clarification.target_field
                )
                resolution_outcome = (
                    ClarificationResolutionOutcome.REJECTED
                    if same_target
                    else ClarificationResolutionOutcome.ANSWERED
                )
                if same_target:
                    clarification = clarification.model_copy(
                        update={
                            "attempt_count": (
                                event.prior_clarification.attempt_count + 1
                            )
                        }
                    )
            else:
                resolution_outcome = ClarificationResolutionOutcome.SUPERSEDED
            resolutions = _resolve_clarification(
                current,
                event.prior_clarification,
                resolving_turn_id=event.resolving_turn_id,
                outcome=resolution_outcome,
                created_at=event.revision.created_at,
            )
            status_changes: tuple[TransitionStatusChange, ...] = ()
            if executing:
                assert current.active_execution_id is not None
                status_changes = (
                    ExecutionStatusChange(
                        execution_id=current.active_execution_id,
                        next_status=ExecutionAttemptStatus.CANCELLATION_REQUESTED,
                    ),
                )
            next_state = _next_state(
                current,
                now=event.revision.created_at,
                phase=(
                    WorkflowPhase.EXECUTING
                    if executing
                    else WorkflowPhase.AWAITING_CLARIFICATION
                ),
                active_revision_id=event.revision.revision_id,
                active_bound_request_id=None,
                active_clarification_id=clarification.question_id,
                pending_plan_id=None,
                active_plan_id=(
                    _UNCHANGED
                    if executing
                    else None
                ),
            )
            return _transition(
                current,
                next_state,
                revisions=(event.revision,),
                clarifications=(clarification,),
                clarification_resolutions=resolutions,
                status_changes=status_changes,
            )

        if isinstance(event, RequestRejectedEvent):
            if event.revision.session_id != current.session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "semantic revision belongs to another analysis session",
                )
            resolution_outcome = (
                ClarificationResolutionOutcome.UNSUPPORTED
                if event.answer_submitted and event.outcome == "unsupported"
                else ClarificationResolutionOutcome.REJECTED
                if event.answer_submitted
                else ClarificationResolutionOutcome.SUPERSEDED
            )
            resolutions = _resolve_clarification(
                current,
                event.prior_clarification,
                resolving_turn_id=event.resolving_turn_id,
                outcome=resolution_outcome,
                created_at=event.revision.created_at,
            )
            if current.active_execution_id:
                next_state = _next_state(
                    current,
                    now=event.revision.created_at,
                    active_clarification_id=None,
                )
            else:
                next_state = _next_state(
                    current,
                    now=event.revision.created_at,
                    phase=WorkflowPhase.FAILED,
                    active_revision_id=event.revision.revision_id,
                    active_bound_request_id=None,
                    active_clarification_id=None,
                    active_plan_id=None,
                    pending_plan_id=None,
                )
            return _transition(
                current,
                next_state,
                revisions=(event.revision,),
                clarification_resolutions=resolutions,
            )

        if isinstance(event, PlanReadyEvent):
            _validate_revision_records(event.revision, event.bound_request, event.plan)
            if event.revision.session_id != current.session_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "plan-ready records belong to another analysis session",
                )
            resolutions = _resolve_clarification(
                current,
                event.prior_clarification,
                resolving_turn_id=event.resolving_turn_id,
                outcome=(
                    ClarificationResolutionOutcome.ANSWERED
                    if event.answer_submitted
                    else ClarificationResolutionOutcome.SUPERSEDED
                ),
                created_at=event.revision.created_at,
            )
            if current.active_execution_id:
                next_state = _next_state(
                    current,
                    now=event.revision.created_at,
                    phase=WorkflowPhase.EXECUTING,
                    active_revision_id=event.revision.revision_id,
                    active_bound_request_id=event.bound_request.bound_request_id,
                    active_clarification_id=None,
                    pending_plan_id=event.plan.plan_id,
                )
                status_changes = (
                    ExecutionStatusChange(
                        execution_id=current.active_execution_id,
                        next_status=ExecutionAttemptStatus.CANCELLATION_REQUESTED,
                    ),
                )
            else:
                status_changes_list: list[TransitionStatusChange] = []
                if current.active_plan_id and current.phase == WorkflowPhase.READY:
                    status_changes_list.append(
                        PlanStatusChange(
                            plan_id=current.active_plan_id,
                            expected_status=PlanStatus.READY,
                            next_status=PlanStatus.SUPERSEDED,
                        )
                    )
                status_changes = tuple(status_changes_list)
                next_state = _next_state(
                    current,
                    now=event.revision.created_at,
                    phase=WorkflowPhase.READY,
                    active_revision_id=event.revision.revision_id,
                    active_bound_request_id=event.bound_request.bound_request_id,
                    active_clarification_id=None,
                    active_plan_id=event.plan.plan_id,
                    active_execution_id=None,
                    pending_plan_id=None,
                )
            return _transition(
                current,
                next_state,
                revisions=(event.revision,),
                bound_requests=(event.bound_request,),
                clarification_resolutions=resolutions,
                plans=(event.plan,),
                status_changes=status_changes,
            )

        if isinstance(event, CancellationRequestedEvent):
            if (
                event.request_revision_id is not None
                and event.request_revision_id != current.active_revision_id
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "cancellation does not reference the active semantic revision",
                )
            resolutions = _resolve_clarification(
                current,
                event.prior_clarification,
                resolving_turn_id=event.resolving_turn_id,
                outcome=ClarificationResolutionOutcome.CANCELLED,
                created_at=event.occurred_at,
            )
            if current.phase == WorkflowPhase.EXECUTING and current.active_execution_id:
                next_state = _next_state(
                    current,
                    now=event.occurred_at,
                    phase=WorkflowPhase.CANCELLED,
                    active_clarification_id=None,
                    pending_plan_id=None,
                )
                changes: tuple[TransitionStatusChange, ...] = (
                    ExecutionStatusChange(
                        execution_id=current.active_execution_id,
                        next_status=ExecutionAttemptStatus.CANCELLATION_REQUESTED,
                    ),
                )
            elif current.phase in {
                WorkflowPhase.READY,
                WorkflowPhase.AWAITING_CLARIFICATION,
            }:
                next_state = _next_state(
                    current,
                    now=event.occurred_at,
                    phase=WorkflowPhase.CANCELLED,
                    active_clarification_id=None,
                    active_bound_request_id=None,
                    active_plan_id=None,
                    pending_plan_id=None,
                )
                changes = (
                    (
                        PlanStatusChange(
                            plan_id=current.active_plan_id,
                            expected_status=PlanStatus.READY,
                            next_status=PlanStatus.CANCELLED,
                        ),
                    )
                    if current.active_plan_id
                    else ()
                )
            else:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    f"cancellation is not permitted from {current.phase.value}",
                )
            return _transition(
                current,
                next_state,
                clarification_resolutions=resolutions,
                status_changes=changes,
            )

        if isinstance(event, PlanClaimedEvent):
            if (
                current.phase != WorkflowPhase.READY
                or current.active_plan_id is None
                or current.active_revision_id is None
                or current.active_bound_request_id is None
                or current.active_execution_id is not None
                or current.pending_plan_id is not None
                or event.plan.plan_id != current.active_plan_id
                or event.plan.request_revision_id != current.active_revision_id
                or event.plan.bound_request_id != current.active_bound_request_id
                or event.plan.session_id != current.session_id
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "only one current ready plan may be claimed",
                )
            attempt = ExecutionAttempt(
                execution_id=event.execution_id,
                session_id=current.session_id,
                request_revision_id=current.active_revision_id,
                bound_request_id=current.active_bound_request_id,
                plan_id=current.active_plan_id,
                plan_hash=event.plan.plan_hash,
                token_hash=event.token_hash,
                status=ExecutionAttemptStatus.CLAIMED,
                worker_id=event.worker_id,
                catalogue_version=event.plan.catalogue_version,
                engine_version=event.plan.engine_version,
                country_package_version=event.plan.country_package_version,
                dataset_identifier=event.plan.dataset_identifier,
                claimed_at=event.claimed_at,
                heartbeat_at=event.claimed_at,
                lease_expires_at=event.lease_expires_at,
            )
            next_state = _next_state(
                current,
                now=event.claimed_at,
                phase=WorkflowPhase.EXECUTING,
                active_execution_id=attempt.execution_id,
            )
            return _transition(
                current,
                next_state,
                execution_attempts=(attempt,),
                status_changes=(
                    PlanStatusChange(
                        plan_id=current.active_plan_id,
                        expected_status=PlanStatus.READY,
                        next_status=PlanStatus.EXECUTING,
                    ),
                ),
            )

        if isinstance(event, (AttemptOutcomeEvent, RecoveryEvent)):
            attempt = event.attempt
            if (
                attempt.session_id != current.session_id
                or current.active_execution_id != attempt.execution_id
            ):
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "attempt outcome does not reference the active execution",
                )
            if isinstance(event, RecoveryEvent):
                completion = ExecutionCompletion(
                    execution_id=attempt.execution_id,
                    status=event.outcome,
                    error_code=AnalysisErrorCode.EXECUTION_EXPIRED.value,
                )
                completed_at = event.recovered_at
            else:
                completion = event.completion
                completed_at = event.completed_at
                if completion.execution_id != attempt.execution_id:
                    raise AnalysisError(
                        AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                        "completion identity differs from the active attempt",
                    )
            plan_status = PlanStatus(completion.status)
            if current.pending_plan_id is not None:
                next_state = _next_state(
                    current,
                    now=completed_at,
                    phase=WorkflowPhase.READY,
                    active_plan_id=current.pending_plan_id,
                    active_execution_id=None,
                    pending_plan_id=None,
                    latest_execution_id=attempt.execution_id,
                )
            elif current.active_clarification_id is not None:
                next_state = _next_state(
                    current,
                    now=completed_at,
                    phase=WorkflowPhase.AWAITING_CLARIFICATION,
                    active_bound_request_id=None,
                    active_plan_id=None,
                    active_execution_id=None,
                    latest_execution_id=attempt.execution_id,
                )
            else:
                phase = {
                    "completed": WorkflowPhase.COMPLETED,
                    "failed": WorkflowPhase.FAILED,
                    "cancelled": WorkflowPhase.CANCELLED,
                    "superseded": WorkflowPhase.CANCELLED,
                    "expired": WorkflowPhase.FAILED,
                }[completion.status]
                next_state = _next_state(
                    current,
                    now=completed_at,
                    phase=phase,
                    active_execution_id=None,
                    latest_execution_id=attempt.execution_id,
                )
            return _transition(
                current,
                next_state,
                execution_completions=(completion,),
                status_changes=(
                    ExecutionStatusChange(
                        execution_id=attempt.execution_id,
                        expected_status=attempt.status,
                        expected_lease_expires_at=(
                            attempt.lease_expires_at
                            if isinstance(event, RecoveryEvent)
                            else None
                        ),
                        next_status=ExecutionAttemptStatus(completion.status),
                    ),
                    PlanStatusChange(
                        plan_id=attempt.plan_id,
                        next_status=plan_status,
                    ),
                ),
            )

        if isinstance(event, ExplanationOutcomeEvent):
            if current.phase != WorkflowPhase.READY or current.active_plan_id != event.plan_id:
                raise AnalysisError(
                    AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED,
                    "explanation outcome does not reference the current ready plan",
                )
            phase = (
                WorkflowPhase.COMPLETED
                if event.status == "completed"
                else WorkflowPhase.FAILED
            )
            return _transition(
                current,
                _next_state(current, now=event.completed_at, phase=phase),
                status_changes=(
                    PlanStatusChange(
                        plan_id=event.plan_id,
                        expected_status=PlanStatus.READY,
                        next_status=PlanStatus(event.status),
                    ),
                ),
            )

        assert_never(event)
