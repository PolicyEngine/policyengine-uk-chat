from __future__ import annotations

import json
import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlmodel import Session, create_engine, select

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis import persistence_compat, persistence_rows
from analysis.compiler import ExecutionPlanCompiler
from analysis.lifecycle import (
    ConversationAdvancedEvent,
    LifecycleReducer,
    PlanReadyEvent,
    RecoveryEvent,
)
from analysis.models import (
    AnalysisSessionState,
    ExecutionCompletion,
    ExecutionPlan,
    PersistedExecutionMetadata,
    SemanticRequestRevision,
    TurnReceipt,
)
from analysis.persistence import (
    AnalysisBillingIntentRow,
    AnalysisBoundRequestRow,
    AnalysisClarificationResolutionRow,
    AnalysisClarificationRow,
    AnalysisExecutionAttemptRow,
    AnalysisExecutionRow,
    AnalysisModelUsageRow,
    AnalysisPlanRow,
    AnalysisRequestRevisionRow,
    SqlAnalysisStore,
    AnalysisTurnReceiptRow,
    AnalysisWorkflowRow,
    ensure_analysis_tables,
)
from analysis.persistence_compat import parse_persisted
from analysis.store import HeartbeatAttemptCommand
from analysis_helpers import (
    NOW,
    bound_request,
    claim_plan,
    create_session,
    finish_attempt,
    owned_analysis_store,
    recover_expired_attempts,
    revision,
)
from analysis_store_contract import assert_analysis_store_contract


def _store(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'analysis.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    ensure_analysis_tables(engine)
    return owned_analysis_store(engine)


def _ready(store):
    state = create_session(store, "session_test")
    semantic = revision()
    bound = bound_request()
    plan = ExecutionPlanCompiler.compile(bound)
    transition = LifecycleReducer.reduce(
        state,
        PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
    )
    ready = store.commit_transition(transition)
    return ready, semantic, bound, plan


def test_sqlite_analysis_store_contract(tmp_path):
    assert_analysis_store_contract(_store(tmp_path), suffix="sqlite")


def test_atomic_transition_persists_linked_records(tmp_path):
    store = _store(tmp_path)
    ready, semantic, bound, plan = _ready(store)
    loaded = store.load("session_test")
    assert loaded.state == ready
    assert loaded.active_revision == semantic
    assert loaded.active_bound_request == bound
    assert loaded.active_plan == plan


def test_version_conflict_rolls_back_all_appended_records(tmp_path):
    store = _store(tmp_path)
    state = create_session(store, "session_test")
    semantic = revision()
    bound = bound_request()
    plan = ExecutionPlanCompiler.compile(bound)
    transition = LifecycleReducer.reduce(
        state,
        PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
    )
    store.commit_transition(
        LifecycleReducer.reduce(state, ConversationAdvancedEvent(occurred_at=NOW))
    )
    with pytest.raises(AnalysisError) as raised:
        store.commit_transition(transition)
    assert raised.value.code == AnalysisErrorCode.STATE_CONFLICT
    with Session(store.engine) as db:
        assert db.exec(select(AnalysisRequestRevisionRow)).all() == []
        assert db.exec(select(AnalysisBoundRequestRow)).all() == []
        assert db.exec(select(AnalysisPlanRow)).all() == []


def test_wrong_parent_reference_rolls_back_transition(tmp_path):
    store = _store(tmp_path)
    state = create_session(store, "session_test")
    semantic = revision()
    bound = bound_request().model_copy(update={"request_revision_id": "missing"})
    plan = ExecutionPlanCompiler.compile(bound)
    with pytest.raises(AnalysisError):
        transition = LifecycleReducer.reduce(
            state,
            PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
        )
        store.commit_transition(transition)
    assert store.load_state("session_test").state_version == 0


def test_wrong_session_record_rolls_back_complete_transition(tmp_path):
    store = _store(tmp_path)
    state = create_session(store, "session_test")
    semantic = revision()
    bound = bound_request()
    plan = ExecutionPlanCompiler.compile(bound)
    transition = LifecycleReducer.reduce(
        state,
        PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
    ).model_copy(
        update={
            "bound_requests": (
                bound.model_copy(update={"session_id": "another_session"}),
            )
        }
    )

    with pytest.raises(AnalysisError) as raised:
        store.commit_transition(transition)

    assert raised.value.code == AnalysisErrorCode.LIFECYCLE_PRECONDITION_FAILED
    with Session(store.engine) as db:
        assert db.exec(select(AnalysisRequestRevisionRow)).all() == []
        assert db.exec(select(AnalysisBoundRequestRow)).all() == []
        assert db.exec(select(AnalysisPlanRow)).all() == []


def test_failed_conditional_status_update_rolls_back_new_records(tmp_path):
    store = _store(tmp_path)
    ready, _semantic, _bound, old_plan = _ready(store)
    replacement_revision = revision(
        revision_id="rev_replacement",
        revision_number=2,
        turn_id="turn_replacement",
        outputs=("poverty_impact",),
    )
    replacement_bound = bound_request(
        revision_id="rev_replacement",
        turn_id="turn_replacement",
        outputs=("poverty_impact",),
    )
    replacement_plan = ExecutionPlanCompiler.compile(replacement_bound)
    transition = LifecycleReducer.reduce(
        ready,
        PlanReadyEvent(
            revision=replacement_revision,
            bound_request=replacement_bound,
            plan=replacement_plan,
        ),
    )
    with Session(store.engine) as db:
        row = db.get(AnalysisPlanRow, old_plan.plan_id)
        row.status = "executing"
        db.add(row)
        db.commit()

    with pytest.raises(AnalysisError) as raised:
        store.commit_transition(transition)

    assert raised.value.code == AnalysisErrorCode.STATE_CONFLICT
    assert store.load_state("session_test") == ready
    with Session(store.engine) as db:
        assert db.get(AnalysisRequestRevisionRow, "rev_replacement") is None
        assert db.get(
            AnalysisBoundRequestRow, replacement_bound.bound_request_id
        ) is None
        assert db.get(AnalysisPlanRow, replacement_plan.plan_id) is None


def test_two_workers_claim_exactly_one_attempt(tmp_path):
    store = _store(tmp_path)
    ready, _semantic, _bound, plan = _ready(store)

    def claim(worker):
        try:
            return claim_plan(
                store,
                state=ready,
                plan=plan,
                worker_id=worker,
            )
        except AnalysisError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("worker_one", "worker_two")))
    winners = [item for item in results if not isinstance(item, Exception)]
    assert len(winners) == 1
    with Session(store.engine) as db:
        assert len(db.exec(select(AnalysisExecutionAttemptRow)).all()) == 1


def test_token_remains_valid_after_unrelated_state_advance(tmp_path):
    store = _store(tmp_path)
    ready, _semantic, _bound, plan = _ready(store)
    claim = claim_plan(
        store,
        state=ready,
        plan=plan,
        worker_id="worker",
    )
    advanced = LifecycleReducer.reduce(
        claim.state,
        ConversationAdvancedEvent(occurred_at=NOW + timedelta(seconds=1)),
    )
    store.commit_transition(advanced)
    verified = store.verify_attempt(
        execution_id=claim.attempt.execution_id,
        token=claim.token,
        plan=plan,
    )
    assert verified.execution_id == claim.attempt.execution_id


def test_replacement_is_queued_until_active_attempt_finishes(tmp_path):
    store = _store(tmp_path)
    ready, _semantic, _bound, plan = _ready(store)
    claim = claim_plan(
        store,
        state=ready,
        plan=plan,
        worker_id="worker",
    )
    replacement_revision = revision(
        revision_id="rev_replacement",
        revision_number=2,
        turn_id="turn_replacement",
        outputs=("poverty_impact",),
    )
    replacement_bound = bound_request(
        revision_id="rev_replacement",
        turn_id="turn_replacement",
        outputs=("poverty_impact",),
    )
    replacement_plan = ExecutionPlanCompiler.compile(replacement_bound)
    queued_transition = LifecycleReducer.reduce(
        claim.state,
        PlanReadyEvent(
            revision=replacement_revision,
            bound_request=replacement_bound,
            plan=replacement_plan,
        ),
    )
    queued = store.commit_transition(queued_transition)
    assert queued.pending_plan_id == replacement_plan.plan_id
    assert store.load_attempt(claim.attempt.execution_id).status.value == "cancellation_requested"
    with pytest.raises(AnalysisError):
        claim_plan(
            store,
            state=queued,
            plan=replacement_plan,
            worker_id="other",
        )
    promoted = finish_attempt(
        store,
        state=queued,
        attempt=claim.attempt,
        token=claim.token,
        completion=ExecutionCompletion(
            execution_id=claim.attempt.execution_id,
            status="cancelled",
        ),
    )
    assert promoted.phase.value == "ready"
    assert promoted.active_plan_id == replacement_plan.plan_id
    assert promoted.active_execution_id is None


def test_expired_attempt_recovery_is_bounded_and_idempotent(tmp_path):
    store = _store(tmp_path)
    ready, _semantic, _bound, plan = _ready(store)
    claim = claim_plan(
        store,
        state=ready,
        plan=plan,
        worker_id="worker",
        lease_seconds=-1,
    )
    recovered = recover_expired_attempts(
        store,
        at=claim.attempt.lease_expires_at + timedelta(seconds=1)
    )
    assert recovered == (claim.attempt.execution_id,)
    assert recover_expired_attempts(
        store,
        at=claim.attempt.lease_expires_at + timedelta(seconds=2)
    ) == ()
    assert store.load_attempt(claim.attempt.execution_id).status.value == "expired"


def test_recovery_transition_cannot_expire_an_attempt_after_heartbeat(tmp_path):
    store = _store(tmp_path)
    ready, _semantic, _bound, plan = _ready(store)
    claim = claim_plan(
        store,
        state=ready,
        plan=plan,
        worker_id="worker",
    )
    stale_recovery = LifecycleReducer.reduce(
        claim.state,
        RecoveryEvent(
            attempt=claim.attempt,
            recovered_at=claim.attempt.lease_expires_at + timedelta(seconds=1),
        ),
    )

    refreshed = store.heartbeat_attempt(
        HeartbeatAttemptCommand(
            execution_id=claim.attempt.execution_id,
            token=claim.token,
        )
    )
    with pytest.raises(AnalysisError) as raised:
        store.commit_transition(stale_recovery)

    assert raised.value.code == AnalysisErrorCode.STATE_CONFLICT
    current = store.load_attempt(claim.attempt.execution_id)
    assert current.status.is_active
    assert current.lease_expires_at == refreshed.lease_expires_at


@pytest.mark.parametrize(
    ("fixture_name", "model"),
    [
        ("workflow-v1.json", AnalysisSessionState),
        ("plan-v1.json", ExecutionPlan),
        ("receipt-v1.json", TurnReceipt),
        ("execution-v1.json", PersistedExecutionMetadata),
    ],
)
def test_previous_schema_fixtures_load_through_compatibility_reader(fixture_name, model):
    fixture = Path(__file__).parent / "fixtures" / "analysis_v1" / fixture_name
    restored = parse_persisted(model, fixture.read_text())
    assert restored.schema_version == 2


def test_store_uses_separate_sql_rows_and_compatibility_reader():
    durable_rows = (
        AnalysisWorkflowRow,
        AnalysisRequestRevisionRow,
        AnalysisBoundRequestRow,
        AnalysisClarificationRow,
        AnalysisClarificationResolutionRow,
        AnalysisPlanRow,
        AnalysisExecutionAttemptRow,
        AnalysisExecutionRow,
        AnalysisTurnReceiptRow,
        AnalysisModelUsageRow,
        AnalysisBillingIntentRow,
    )

    assert {inspect.getmodule(row) for row in durable_rows} == {persistence_rows}
    assert parse_persisted.__module__ == persistence_compat.__name__


def test_durable_analysis_schema_defines_no_result_payload_columns(tmp_path):
    store = _store(tmp_path)
    ready, _semantic, _bound, plan = _ready(store)
    claim = claim_plan(
        store,
        state=ready,
        plan=plan,
        worker_id="worker",
    )
    finish_attempt(
        store,
        state=claim.state,
        attempt=claim.attempt,
        token=claim.token,
        completion=ExecutionCompletion(
            execution_id=claim.attempt.execution_id,
            status="completed",
        ),
    )
    durable_tables = (
        AnalysisWorkflowRow,
        AnalysisRequestRevisionRow,
        AnalysisBoundRequestRow,
        AnalysisClarificationRow,
        AnalysisClarificationResolutionRow,
        AnalysisPlanRow,
        AnalysisExecutionAttemptRow,
        AnalysisExecutionRow,
        AnalysisTurnReceiptRow,
        AnalysisModelUsageRow,
        AnalysisBillingIntentRow,
    )
    prohibited_columns = {
        "result_id",
        "result_identifier",
        "result_envelope",
        "result_payload",
        "calculation_payload",
    }
    for table in durable_tables:
        assert prohibited_columns.isdisjoint(table.__table__.columns.keys())


def test_migration_is_repeatable_and_defines_active_attempt_uniqueness():
    migration = Path("supabase/migrations/006_analysis_compiler_hardening.sql").read_text()
    assert "if not exists" in migration.casefold()
    assert "uq_analysis_active_attempt_session" in migration
    assert "cancellation_requested" in migration
