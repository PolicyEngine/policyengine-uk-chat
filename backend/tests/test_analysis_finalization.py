from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, select

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.finalization import (
    _validate_agreement,
    finalize_turn,
    replay_outcome,
)
from analysis.lifecycle import ConversationAdvancedEvent, LifecycleReducer
from analysis.lifecycle import ConflictObservedEvent
from analysis.models import (
    AnalysisSessionState,
    BillingIntentStatus,
    CancelledTurnOutcome,
    ClarificationTurnOutcome,
    CompletedTurnOutcome,
    ConflictTurnOutcome,
    FailedTurnOutcome,
    ModelUsageEntry,
    ResponseArtifact,
    StillProcessingTurnOutcome,
    TurnReceipt,
    TurnReceiptStatus,
    UnsupportedTurnOutcome,
    WorkflowPhase,
)
from analysis.persistence import (
    AnalysisBillingIntentRow,
    AnalysisModelUsageRow,
    AnalysisStateStore,
    ensure_analysis_tables,
)
from analysis_helpers import NOW, owned_analysis_store
from billing.intents import build_billing_intent


def _state(phase: WorkflowPhase) -> AnalysisSessionState:
    return AnalysisSessionState(
        session_id="session_finalization",
        phase=phase,
        updated_at=NOW,
    )


_OUTCOME_PHASES = (
    (
        CompletedTurnOutcome(content="ok", route="execution_question"),
        set(WorkflowPhase),
    ),
    (
        CompletedTurnOutcome(content="ok", route="standard"),
        {WorkflowPhase.COMPLETED},
    ),
    (
        ClarificationTurnOutcome(
            content="question",
            question_id="question_one",
            reason_code="missing_output",
        ),
        {WorkflowPhase.AWAITING_CLARIFICATION, WorkflowPhase.EXECUTING},
    ),
    (
        UnsupportedTurnOutcome(
            content="unsupported",
            reason_code="unsupported_scope",
        ),
        {WorkflowPhase.FAILED, WorkflowPhase.EXECUTING, WorkflowPhase.CANCELLED},
    ),
    (
        FailedTurnOutcome(content="failed", error_code="failure"),
        {WorkflowPhase.FAILED, WorkflowPhase.EXECUTING, WorkflowPhase.CANCELLED},
    ),
    (CancelledTurnOutcome(content="cancelled"), set(WorkflowPhase)),
)


@pytest.mark.parametrize(
    ("outcome", "phase"),
    [(outcome, phase) for outcome, _allowed in _OUTCOME_PHASES for phase in WorkflowPhase],
)
def test_every_outcome_and_lifecycle_combination_is_checked(outcome, phase):
    allowed = next(allowed for item, allowed in _OUTCOME_PHASES if item is outcome)
    if phase in allowed:
        _validate_agreement(outcome, _state(phase))
    else:
        with pytest.raises(AnalysisError) as raised:
            _validate_agreement(outcome, _state(phase))
        assert raised.value.code == AnalysisErrorCode.OUTCOME_INVALID


@pytest.mark.parametrize(
    "outcome",
    [
        ConflictTurnOutcome(content="conflict"),
        StillProcessingTurnOutcome(content="processing"),
    ],
)
def test_replay_only_outcomes_cannot_finalize_session_state(outcome):
    with pytest.raises(AnalysisError) as raised:
        _validate_agreement(outcome, _state(WorkflowPhase.COMPLETED))
    assert raised.value.code == AnalysisErrorCode.OUTCOME_INVALID


def _receipt(category: str, status: TurnReceiptStatus, metadata=None) -> TurnReceipt:
    return TurnReceipt(
        session_id="session_finalization",
        turn_id=f"turn_{category}",
        request_hash="hash",
        state_version=1,
        status=status,
        outcome_category=category,
        response_content=f"content:{category}",
        response_metadata=metadata or {},
        created_at=NOW,
    )


@pytest.mark.parametrize(
    ("receipt", "outcome_type"),
    [
        (
            _receipt(
                "completed",
                TurnReceiptStatus.COMPLETED,
                {"route": "standard", "model": "model-one"},
            ),
            CompletedTurnOutcome,
        ),
        (
            _receipt(
                "clarification",
                TurnReceiptStatus.COMPLETED,
                {"question_id": "question", "reason_code": "missing_output"},
            ),
            ClarificationTurnOutcome,
        ),
        (
            _receipt(
                "unsupported",
                TurnReceiptStatus.COMPLETED,
                {"reason_code": "unsupported_scope"},
            ),
            UnsupportedTurnOutcome,
        ),
        (
            _receipt(
                "failed",
                TurnReceiptStatus.FAILED,
                {"error_code": "failed", "retryable": True},
            ),
            FailedTurnOutcome,
        ),
        (
            _receipt("cancelled", TurnReceiptStatus.CANCELLED),
            CancelledTurnOutcome,
        ),
        (
            _receipt("conflict", TurnReceiptStatus.CONFLICT),
            ConflictTurnOutcome,
        ),
        (
            _receipt("processing", TurnReceiptStatus.PROCESSING),
            StillProcessingTurnOutcome,
        ),
    ],
)
def test_replay_preserves_each_recorded_outcome_category(receipt, outcome_type):
    outcome = replay_outcome(receipt)
    assert isinstance(outcome, outcome_type)
    if not isinstance(outcome, StillProcessingTurnOutcome):
        assert outcome.duplicate is True


def test_finalization_commits_receipt_usage_and_idempotent_billing_intent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'finalization.sqlite'}")
    ensure_analysis_tables(engine)
    store = owned_analysis_store(engine)
    state = store.create_session("session_finalization", at=NOW)
    started = store.begin_turn(
        session_id=state.session_id,
        turn_id="turn_finalization",
        request_content={"message": "question"},
        state_version=state.state_version,
    )
    transition = LifecycleReducer.reduce(
        state,
        ConversationAdvancedEvent(occurred_at=NOW),
    )
    usage = ModelUsageEntry(
        usage_entry_id="usage_finalization",
        session_id=state.session_id,
        turn_id="turn_finalization",
        operation="interpretation",
        model="model-one",
        input_tokens=10,
        output_tokens=5,
    )
    outcome = CompletedTurnOutcome(
        content="method answer",
        route="execution_question",
        response_artifacts=(
            ResponseArtifact(
                artifact_id="chart_request_local",
                content='```chart\n{"private_dataset":9173}\n```',
            ),
        ),
    )
    finalized = finalize_turn(
        store=store,
        receipt=started.receipt,
        transition=transition,
        outcome=outcome,
        usage_entries=(usage,),
        billing_intent=build_billing_intent(
            session_id=str(state.session_id),
            turn_id="turn_finalization",
            user_id="user-finalization",
            usage_entries=(usage,),
        ),
    )
    assert finalized.outcome is outcome
    assert finalized.live_response_artifacts == outcome.response_artifacts
    receipt = store.load_receipt(state.session_id, "turn_finalization")
    assert "private_dataset" not in (receipt.response_content or "")
    assert "private_dataset" not in str(receipt.response_metadata)
    with Session(engine) as database:
        assert len(database.exec(select(AnalysisModelUsageRow)).all()) == 1
        intent = database.exec(select(AnalysisBillingIntentRow)).one()
        assert intent.status == BillingIntentStatus.PENDING.value
        assert intent.user_id == "user-finalization"
        persisted_intent = json.loads(intent.payload_json)
        assert persisted_intent["charge_inputs"] == [
            {
                "usage_entry_id": "usage_finalization",
                "operation": "interpretation",
                "model": "model-one",
                "input_tokens": 10,
                "output_tokens": 5,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "cost_gbp": persisted_intent["charge_inputs"][0]["cost_gbp"],
            }
        ]
        assert persisted_intent["charge_inputs"][0]["cost_gbp"] > 0
    assert store.mark_billing_recorded(state.session_id, "turn_finalization") is True
    assert store.mark_billing_recorded(state.session_id, "turn_finalization") is True
    with pytest.raises(AnalysisError):
        finalize_turn(
            store=store,
            receipt=started.receipt,
            transition=transition,
            outcome=outcome,
            usage_entries=(usage,),
        )


def test_conflict_uses_the_common_finalizer_without_changing_session_state(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'conflict-finalization.sqlite'}")
    ensure_analysis_tables(engine)
    store = owned_analysis_store(engine)
    state = store.create_session("session_finalization", at=NOW)
    started = store.begin_turn(
        session_id=state.session_id,
        turn_id="turn_conflict",
        request_content={"message": "stale update"},
        state_version=state.state_version,
    )
    transition = LifecycleReducer.reduce(state, ConflictObservedEvent())

    finalized = finalize_turn(
        store=store,
        receipt=started.receipt,
        transition=transition,
        outcome=ConflictTurnOutcome(content="retry"),
    )

    assert finalized.state == state
    assert finalized.outcome.kind == "conflict"
    receipt = store.load_receipt(state.session_id, "turn_conflict")
    assert receipt.status == TurnReceiptStatus.CONFLICT
    assert receipt.outcome_category == "conflict"
