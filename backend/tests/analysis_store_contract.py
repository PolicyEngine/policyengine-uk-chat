"""Reusable behavioral contract for analysis-store implementations."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.compiler import ExecutionPlanCompiler
from analysis.lifecycle import LifecycleReducer, PlanReadyEvent
from analysis.models import ExecutionCompletion, WorkflowPhase
from analysis.store import (
    AnalysisStore,
    BeginTurnCommand,
    DeleteAnalysisSessionCommand,
    HeartbeatAttemptCommand,
    LoadOrCreateSessionCommand,
    MarkBillingRecordedCommand,
    SessionDeletionResult,
)
from analysis_helpers import (
    bound_request,
    claim_plan,
    create_session,
    finish_attempt,
    revision,
)


def assert_analysis_store_contract(store: AnalysisStore, *, suffix: str) -> None:
    """Exercise the typed mutation and read contract against one store."""

    assert isinstance(store, AnalysisStore)
    session_id = f"store_contract_{suffix}"
    turn_id = f"turn_{suffix}"
    revision_id = f"revision_{suffix}"

    created = create_session(store, session_id)
    assert create_session(store, session_id) == created
    assert (
        store.load_or_create(LoadOrCreateSessionCommand(session_id=session_id)).state
        == created
    )

    begin_command = BeginTurnCommand(
        session_id=session_id,
        turn_id=turn_id,
        request_content={"message": "calculate"},
        state_version=created.state_version,
    )
    first_start = store.begin_turn(begin_command)
    duplicate_start = store.begin_turn(begin_command)
    assert first_start.duplicate is False
    assert duplicate_start.duplicate is True
    assert duplicate_start.receipt.turn_id == first_start.receipt.turn_id
    assert duplicate_start.receipt.request_hash == first_start.receipt.request_hash
    assert duplicate_start.receipt.status == first_start.receipt.status

    semantic = revision(
        session_id=session_id,
        revision_id=revision_id,
        turn_id=turn_id,
    )
    bound = bound_request(
        session_id=session_id,
        revision_id=revision_id,
        turn_id=turn_id,
    )
    plan = ExecutionPlanCompiler.compile(bound)
    ready = store.commit_transition(
        LifecycleReducer.reduce(
            created,
            PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
        )
    )
    claim = claim_plan(store, state=ready, plan=plan, worker_id="contract_worker")
    heartbeat = store.heartbeat_attempt(
        HeartbeatAttemptCommand(
            execution_id=claim.attempt.execution_id,
            token=claim.token,
        )
    )
    assert heartbeat.execution_id == claim.attempt.execution_id
    assert heartbeat.heartbeat_at >= claim.attempt.heartbeat_at

    completed = finish_attempt(
        store,
        state=claim.state,
        attempt=heartbeat,
        token=claim.token,
        completion=ExecutionCompletion(
            execution_id=heartbeat.execution_id,
            status="completed",
        ),
        completed_at=datetime.now(timezone.utc),
    )
    assert completed.phase == WorkflowPhase.COMPLETED
    assert store.load_state(session_id) == completed
    assert (
        store.mark_billing_recorded(
            MarkBillingRecordedCommand(session_id=session_id, turn_id=turn_id)
        )
        is False
    )

    deleted = store.delete_session(DeleteAnalysisSessionCommand(session_id=session_id))
    assert deleted == SessionDeletionResult(session_id=session_id)
    with pytest.raises(AnalysisError) as raised:
        store.load_state(session_id)
    assert raised.value.code == AnalysisErrorCode.STATE_PRECONDITION_FAILED
