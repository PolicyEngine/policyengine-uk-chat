from __future__ import annotations

import inspect
from datetime import timedelta

import pytest
from hypothesis import given, settings, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from pydantic import ValidationError

import analysis.lifecycle as lifecycle
from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.compiler import ExecutionPlanCompiler
from analysis.lifecycle import (
    AttemptOutcomeEvent,
    CancellationRequestedEvent,
    ClarificationRequiredEvent,
    ConversationAdvancedEvent,
    LifecycleReducer,
    PlanClaimedEvent,
    PlanReadyEvent,
    RecoveryEvent,
    RequestRejectedEvent,
)
from analysis.models import (
    AnalysisSessionState,
    ClarificationResolutionOutcome,
    ExecutionCompletion,
    PendingClarification,
    WorkflowPhase,
)
from analysis_helpers import NOW, bound_request, revision


def _ready_transition(state=None):
    state = state or AnalysisSessionState(session_id="session_test", updated_at=NOW)
    semantic = revision()
    bound = bound_request()
    plan = ExecutionPlanCompiler.compile(bound)
    transition = LifecycleReducer.reduce(
        state,
        PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
    )
    return transition, semantic, bound, plan


def _claimed():
    ready_transition, semantic, bound, plan = _ready_transition()
    ready = ready_transition.next_state
    claim_transition = LifecycleReducer.reduce(
        ready,
        PlanClaimedEvent(
            plan=plan,
            execution_id="execution_test",
            token_hash="hash",
            worker_id="worker",
            claimed_at=NOW,
            lease_expires_at=NOW + timedelta(minutes=3),
        ),
    )
    return claim_transition, semantic, bound, plan


@pytest.mark.parametrize(
    ("phase", "event_factory"),
    [
        (WorkflowPhase.IDLE, lambda: CancellationRequestedEvent()),
        (WorkflowPhase.COMPLETED, lambda: CancellationRequestedEvent()),
        (WorkflowPhase.FAILED, lambda: CancellationRequestedEvent()),
        (WorkflowPhase.CANCELLED, lambda: CancellationRequestedEvent()),
    ],
)
def test_undefined_phase_event_pairs_are_rejected(phase, event_factory):
    state = AnalysisSessionState(
        session_id="session_test", phase=phase, updated_at=NOW
    )
    with pytest.raises(AnalysisError) as raised:
        LifecycleReducer.reduce(state, event_factory())
    assert raised.value.code == AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED


def test_only_lifecycle_reducer_constructs_next_session_state():
    helper = lifecycle._next_state
    signature = inspect.signature(helper)
    source = inspect.getsource(helper)

    assert all(
        parameter.kind is not inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )
    assert "AnalysisSessionState(" in source
    assert "model_copy" not in source


def test_next_state_revalidates_an_invalid_constructed_current_state():
    invalid = AnalysisSessionState.model_construct(
        session_id="session_test",
        state_version=0,
        phase="not_a_phase",
        updated_at=NOW,
    )

    with pytest.raises(ValidationError):
        LifecycleReducer.reduce(
            invalid,
            ConversationAdvancedEvent(occurred_at=NOW),
        )


def test_plan_claim_creates_durable_attempt_identity_links():
    claim, semantic, bound, plan = _claimed()
    attempt = claim.execution_attempts[0]
    assert attempt.request_revision_id == semantic.revision_id
    assert attempt.bound_request_id == bound.bound_request_id
    assert attempt.plan_id == plan.plan_id
    assert claim.next_state.active_execution_id == attempt.execution_id
    assert claim.next_state.phase == WorkflowPhase.EXECUTING


def test_clarification_during_execution_requests_cancellation_and_resumes_after_outcome():
    claim, semantic, _bound, _plan = _claimed()
    executing = claim.next_state
    revised = revision(
        revision_id="rev_clarify",
        revision_number=2,
        turn_id="turn_clarify",
    )
    clarification = PendingClarification(
        question_id="question_new",
        session_id="session_test",
        request_revision_id=revised.revision_id,
        target_field="outputs",
        reason_code="missing_output",
        prompt="Which output?",
        created_at=NOW,
    )
    transition = LifecycleReducer.reduce(
        executing,
        ClarificationRequiredEvent(
            revision=revised,
            clarification=clarification,
        ),
    )
    assert transition.next_state.phase == WorkflowPhase.EXECUTING
    assert transition.next_state.active_clarification_id == clarification.question_id
    assert transition.status_changes[0].next_status == "cancellation_requested"
    attempt = claim.execution_attempts[0]
    completed = LifecycleReducer.reduce(
        transition.next_state,
        AttemptOutcomeEvent(
            attempt=attempt,
            completion=ExecutionCompletion(
                execution_id=attempt.execution_id,
                status="cancelled",
            ),
            completed_at=NOW + timedelta(seconds=1),
        ),
    )
    assert completed.next_state.phase == WorkflowPhase.AWAITING_CLARIFICATION
    assert completed.next_state.active_plan_id is None


def test_late_old_completion_cannot_change_newer_state():
    claim, _semantic, _bound, _plan = _claimed()
    attempt = claim.execution_attempts[0]
    newer = claim.next_state.model_copy(update={"active_execution_id": "execution_new"})
    with pytest.raises(AnalysisError):
        LifecycleReducer.reduce(
            newer,
            AttemptOutcomeEvent(
                attempt=attempt,
                completion=ExecutionCompletion(
                    execution_id=attempt.execution_id,
                    status="completed",
                ),
                completed_at=NOW,
            ),
        )


@given(st.sampled_from(["completed", "failed", "cancelled"]))
def test_valid_attempt_outcomes_preserve_identity_invariants(status):
    claim, _semantic, _bound, _plan = _claimed()
    attempt = claim.execution_attempts[0]
    transition = LifecycleReducer.reduce(
        claim.next_state,
        AttemptOutcomeEvent(
            attempt=attempt,
            completion=ExecutionCompletion(
                execution_id=attempt.execution_id,
                status=status,
            ),
            completed_at=NOW + timedelta(seconds=1),
        ),
    )
    assert transition.next_state.state_version == claim.next_state.state_version + 1
    assert transition.next_state.active_execution_id is None
    assert transition.next_state.latest_execution_id == attempt.execution_id
    assert transition.next_state.session_id == claim.next_state.session_id


def test_conversation_can_advance_while_attempt_remains_active():
    claim, _semantic, _bound, _plan = _claimed()
    transition = LifecycleReducer.reduce(
        claim.next_state,
        ConversationAdvancedEvent(occurred_at=NOW + timedelta(seconds=1)),
    )
    assert transition.next_state.active_execution_id == "execution_test"
    assert transition.next_state.state_version == claim.next_state.state_version + 1


@pytest.mark.parametrize("phase", ["ready", "awaiting_clarification", "executing"])
def test_cancellation_is_defined_for_each_eligible_phase(phase):
    if phase == "ready":
        transition, _semantic, _bound, _plan = _ready_transition()
        current = transition.next_state
    elif phase == "executing":
        transition, _semantic, _bound, _plan = _claimed()
        current = transition.next_state
    else:
        current_revision = revision()
        current = AnalysisSessionState(
            session_id=current_revision.session_id,
            state_version=1,
            phase=WorkflowPhase.AWAITING_CLARIFICATION,
            active_revision_id=current_revision.revision_id,
            active_clarification_id="question_cancel",
            updated_at=NOW,
        )

    cancelled = LifecycleReducer.reduce(
        current,
        CancellationRequestedEvent(
            request_revision_id=current.active_revision_id
        ),
    )

    assert cancelled.next_state.phase == WorkflowPhase.CANCELLED
    assert cancelled.next_state.active_clarification_id is None
    if phase == "executing":
        assert cancelled.next_state.active_execution_id is not None
        assert cancelled.status_changes[0].next_status == "cancellation_requested"
    else:
        assert cancelled.next_state.active_execution_id is None


def test_rejected_clarification_answer_records_resolution_and_closes_question():
    current_revision = revision()
    clarification = PendingClarification(
        question_id="question_rejected",
        session_id=current_revision.session_id,
        request_revision_id=current_revision.revision_id,
        target_field="outputs",
        reason_code="missing_output",
        prompt="Which output?",
        created_at=NOW,
    )
    current = AnalysisSessionState(
        session_id=current_revision.session_id,
        state_version=2,
        phase=WorkflowPhase.AWAITING_CLARIFICATION,
        active_revision_id=current_revision.revision_id,
        active_clarification_id=clarification.question_id,
        updated_at=NOW,
    )
    rejected_revision = revision(
        revision_id="rev_rejected",
        revision_number=2,
        turn_id="turn_rejected",
    )
    transition = LifecycleReducer.reduce(
        current,
        RequestRejectedEvent(
            revision=rejected_revision,
            outcome="unsupported",
            prior_clarification=clarification,
            resolving_turn_id="turn_rejected",
            answer_submitted=True,
        ),
    )

    assert len(transition.clarification_resolutions) == 1
    resolution = transition.clarification_resolutions[0]
    assert resolution.question_id == clarification.question_id
    assert resolution.outcome == ClarificationResolutionOutcome.UNSUPPORTED
    assert transition.next_state.active_clarification_id is None
    assert transition.next_state.phase == WorkflowPhase.FAILED


def test_incomplete_clarification_answer_is_rejected_and_increments_attempt_count():
    current_revision = revision()
    prior = PendingClarification(
        question_id="question_prior",
        session_id=current_revision.session_id,
        request_revision_id=current_revision.revision_id,
        target_field="outputs",
        reason_code="missing_output",
        prompt="Which output?",
        attempt_count=1,
        created_at=NOW,
    )
    current = AnalysisSessionState(
        session_id=current_revision.session_id,
        state_version=2,
        phase=WorkflowPhase.AWAITING_CLARIFICATION,
        active_revision_id=current_revision.revision_id,
        active_clarification_id=prior.question_id,
        updated_at=NOW,
    )
    revised = revision(
        revision_id="rev_still_incomplete",
        revision_number=2,
        turn_id="turn_still_incomplete",
    )
    next_clarification = PendingClarification(
        question_id="question_next",
        session_id=revised.session_id,
        request_revision_id=revised.revision_id,
        target_field="outputs",
        reason_code="missing_output",
        prompt="Which output?",
        created_at=NOW,
    )

    transition = LifecycleReducer.reduce(
        current,
        ClarificationRequiredEvent(
            revision=revised,
            clarification=next_clarification,
            prior_clarification=prior,
            resolving_turn_id=revised.turn_id,
            answer_submitted=True,
        ),
    )

    assert transition.clarifications[0].attempt_count == 2
    assert (
        transition.clarification_resolutions[0].outcome
        == ClarificationResolutionOutcome.REJECTED
    )


def test_complete_clarification_answer_records_answered_resolution_with_ready_plan():
    current_revision = revision()
    clarification = PendingClarification(
        question_id="question_answered",
        session_id=current_revision.session_id,
        request_revision_id=current_revision.revision_id,
        target_field="outputs",
        reason_code="missing_output",
        prompt="Which output?",
        created_at=NOW,
    )
    current = AnalysisSessionState(
        session_id=current_revision.session_id,
        state_version=2,
        phase=WorkflowPhase.AWAITING_CLARIFICATION,
        active_revision_id=current_revision.revision_id,
        active_clarification_id=clarification.question_id,
        updated_at=NOW,
    )
    answered_revision = revision(
        revision_id="rev_answered",
        revision_number=2,
        turn_id="turn_answered",
    )
    bound = bound_request(
        revision_id=answered_revision.revision_id,
        turn_id=answered_revision.turn_id,
    )
    plan = ExecutionPlanCompiler.compile(bound)

    transition = LifecycleReducer.reduce(
        current,
        PlanReadyEvent(
            revision=answered_revision,
            bound_request=bound,
            plan=plan,
            prior_clarification=clarification,
            resolving_turn_id=answered_revision.turn_id,
            answer_submitted=True,
        ),
    )

    assert (
        transition.clarification_resolutions[0].outcome
        == ClarificationResolutionOutcome.ANSWERED
    )
    assert transition.next_state.phase == WorkflowPhase.READY
    assert transition.next_state.active_clarification_id is None


def test_cancelling_clarification_records_cancelled_resolution():
    current_revision = revision()
    clarification = PendingClarification(
        question_id="question_cancelled",
        session_id=current_revision.session_id,
        request_revision_id=current_revision.revision_id,
        target_field="outputs",
        reason_code="missing_output",
        prompt="Which output?",
        created_at=NOW,
    )
    current = AnalysisSessionState(
        session_id=current_revision.session_id,
        state_version=2,
        phase=WorkflowPhase.AWAITING_CLARIFICATION,
        active_revision_id=current_revision.revision_id,
        active_clarification_id=clarification.question_id,
        updated_at=NOW,
    )

    transition = LifecycleReducer.reduce(
        current,
        CancellationRequestedEvent(
            request_revision_id=current_revision.revision_id,
            prior_clarification=clarification,
            resolving_turn_id="turn_cancelled",
            occurred_at=NOW,
        ),
    )

    assert (
        transition.clarification_resolutions[0].outcome
        == ClarificationResolutionOutcome.CANCELLED
    )
    assert transition.next_state.phase == WorkflowPhase.CANCELLED


class LifecycleEventSequenceMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.state = AnalysisSessionState(session_id="session_test", updated_at=NOW)
        self.plan = None
        self.pending_plan = None
        self.attempt = None
        self.clarification = None
        self.sequence = 0
        self.expected_version = 0

    def _records(self):
        self.sequence += 1
        semantic = revision(
            revision_id=f"rev_sequence_{self.sequence}",
            revision_number=self.sequence,
            turn_id=f"turn_sequence_{self.sequence}",
        )
        bound = bound_request(
            revision_id=semantic.revision_id,
            turn_id=semantic.turn_id,
        )
        return semantic, bound, ExecutionPlanCompiler.compile(bound)

    def _apply(self, transition):
        assert transition.expected_state_version == self.expected_version
        self.state = transition.next_state
        self.expected_version += 1
        assert self.state.state_version == self.expected_version

    @rule()
    def advance_conversation(self):
        self._apply(
            LifecycleReducer.reduce(
                self.state,
                ConversationAdvancedEvent(
                    occurred_at=NOW + timedelta(seconds=self.expected_version + 1)
                ),
            )
        )

    @precondition(
        lambda self: self.state.active_execution_id is None
        and self.state.phase != WorkflowPhase.AWAITING_CLARIFICATION
    )
    @rule()
    def register_ready_plan(self):
        semantic, bound, plan = self._records()
        transition = LifecycleReducer.reduce(
            self.state,
            PlanReadyEvent(
                revision=semantic,
                bound_request=bound,
                plan=plan,
            ),
        )
        self._apply(transition)
        self.plan = plan
        self.pending_plan = None
        self.attempt = None
        self.clarification = None

    @precondition(
        lambda self: self.state.active_execution_id is None
        and self.state.phase != WorkflowPhase.READY
    )
    @rule()
    def request_clarification(self):
        semantic, _bound, _plan = self._records()
        clarification = PendingClarification(
            question_id=f"question_sequence_{self.sequence}",
            session_id=self.state.session_id,
            request_revision_id=semantic.revision_id,
            target_field="outputs",
            reason_code="missing_output",
            prompt="Which output?",
            created_at=NOW + timedelta(seconds=self.expected_version + 1),
        )
        self._apply(
            LifecycleReducer.reduce(
                self.state,
                ClarificationRequiredEvent(
                    revision=semantic,
                    clarification=clarification,
                ),
            )
        )
        self.clarification = clarification

    @precondition(
        lambda self: self.state.phase == WorkflowPhase.AWAITING_CLARIFICATION
        and self.clarification is not None
    )
    @rule()
    def answer_clarification(self):
        semantic, bound, plan = self._records()
        self._apply(
            LifecycleReducer.reduce(
                self.state,
                PlanReadyEvent(
                    revision=semantic,
                    bound_request=bound,
                    plan=plan,
                    prior_clarification=self.clarification,
                    resolving_turn_id=semantic.turn_id,
                    answer_submitted=True,
                ),
            )
        )
        self.plan = plan
        self.clarification = None

    @precondition(
        lambda self: self.state.phase == WorkflowPhase.READY
        and self.plan is not None
    )
    @rule()
    def claim_ready_plan(self):
        transition = LifecycleReducer.reduce(
            self.state,
            PlanClaimedEvent(
                plan=self.plan,
                execution_id=f"execution_sequence_{self.sequence}",
                token_hash=f"hash_sequence_{self.sequence}",
                worker_id="worker_sequence",
                claimed_at=NOW + timedelta(seconds=self.expected_version + 1),
                lease_expires_at=NOW + timedelta(minutes=3),
            ),
        )
        self._apply(transition)
        self.attempt = transition.execution_attempts[0]

    @precondition(
        lambda self: self.state.active_execution_id is not None
        and self.state.pending_plan_id is None
        and self.state.phase == WorkflowPhase.EXECUTING
    )
    @rule()
    def queue_replacement(self):
        semantic, bound, plan = self._records()
        self._apply(
            LifecycleReducer.reduce(
                self.state,
                PlanReadyEvent(
                    revision=semantic,
                    bound_request=bound,
                    plan=plan,
                ),
            )
        )
        self.pending_plan = plan

    @precondition(
        lambda self: self.state.phase
        in {
            WorkflowPhase.READY,
            WorkflowPhase.AWAITING_CLARIFICATION,
            WorkflowPhase.EXECUTING,
        }
    )
    @rule()
    def request_cancellation(self):
        self._apply(
            LifecycleReducer.reduce(
                self.state,
                CancellationRequestedEvent(
                    request_revision_id=self.state.active_revision_id
                ),
            )
        )
        if self.state.active_execution_id is None:
            self.plan = None
        self.clarification = None

    @precondition(
        lambda self: self.state.phase
        in {
            WorkflowPhase.READY,
            WorkflowPhase.AWAITING_CLARIFICATION,
            WorkflowPhase.EXECUTING,
        }
        and self.state.active_revision_id is not None
    )
    @rule()
    def reject_stale_cancellation_identifier(self):
        before = self.state
        with pytest.raises(AnalysisError) as raised:
            LifecycleReducer.reduce(
                self.state,
                CancellationRequestedEvent(
                    request_revision_id="stale_revision",
                ),
            )
        assert raised.value.code == AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED
        assert self.state == before

    @precondition(lambda self: self.state.active_execution_id is not None)
    @rule()
    def reject_foreign_recovery_identifier(self):
        before = self.state
        foreign_attempt = self.attempt.model_copy(
            update={"session_id": "foreign_session"}
        )
        with pytest.raises(AnalysisError) as raised:
            LifecycleReducer.reduce(
                self.state,
                RecoveryEvent(
                    attempt=foreign_attempt,
                    recovered_at=NOW
                    + timedelta(seconds=self.expected_version + 1),
                ),
            )
        assert raised.value.code == AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED
        assert self.state == before

    @precondition(lambda self: self.state.active_execution_id is not None)
    @rule()
    def recover_attempt(self):
        self._apply(
            LifecycleReducer.reduce(
                self.state,
                RecoveryEvent(
                    attempt=self.attempt,
                    recovered_at=NOW
                    + timedelta(seconds=self.expected_version + 1),
                ),
            )
        )
        self.attempt = None
        if self.pending_plan is not None:
            self.plan = self.pending_plan
            self.pending_plan = None

    @precondition(lambda self: self.state.active_execution_id is not None)
    @rule(status=st.sampled_from(("completed", "failed", "cancelled")))
    def finish_attempt(self, status):
        effective_status = (
            "cancelled"
            if self.state.pending_plan_id is not None
            or self.state.phase == WorkflowPhase.CANCELLED
            else status
        )
        self._apply(
            LifecycleReducer.reduce(
                self.state,
                AttemptOutcomeEvent(
                    attempt=self.attempt,
                    completion=ExecutionCompletion(
                        execution_id=self.attempt.execution_id,
                        status=effective_status,
                    ),
                    completed_at=NOW + timedelta(seconds=self.expected_version + 1),
                ),
            )
        )
        self.attempt = None
        if self.pending_plan is not None:
            self.plan = self.pending_plan
            self.pending_plan = None

    @invariant()
    def state_identifiers_remain_coherent(self):
        assert self.state.session_id == "session_test"
        assert self.state.state_version == self.expected_version
        if self.state.active_execution_id is not None:
            assert self.state.active_revision_id is not None
            assert self.state.active_bound_request_id is not None
            assert self.state.active_plan_id is not None
            assert self.state.phase in {
                WorkflowPhase.EXECUTING,
                WorkflowPhase.CANCELLED,
            }
        if self.state.pending_plan_id is not None:
            assert self.state.active_execution_id is not None
            assert self.state.phase == WorkflowPhase.EXECUTING
        if self.state.phase == WorkflowPhase.READY:
            assert self.state.active_plan_id is not None
            assert self.state.active_execution_id is None
        if self.state.phase == WorkflowPhase.AWAITING_CLARIFICATION:
            assert self.state.active_clarification_id is not None
            assert self.state.active_execution_id is None


TestLifecycleEventSequences = LifecycleEventSequenceMachine.TestCase
TestLifecycleEventSequences.settings = settings(
    max_examples=30,
    stateful_step_count=30,
    deadline=None,
)
