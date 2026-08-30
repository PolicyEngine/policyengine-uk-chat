from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from capabilities.artifacts import (
    ArtifactProvenance,
    PolicyScenarioRef,
)
from capabilities.repository import WaitingCapabilityInvocation
from capabilities.tracing import (
    InvocationKind,
    InvocationRecord,
    InvocationStatus,
)
from conversations.models import ChatConversation
from conversation_context.models import ConversationContext
from persistence.capability_repository import (
    InvalidPersistedRecord,
    PartialInputRegistry,
    RepositoryArtifactAccess,
    SQLConversationCapabilityRepository,
)
from persistence.deletion import delete_capability_records
from persistence.context_repository import SQLConversationContextRepository
from persistence.database_namespace import (
    drop_preview_schema,
    ensure_database_schema,
    namespaced_engine,
)
from persistence.idempotency import (
    IdempotencyDecision,
    ReceiptStatus,
    SQLIdempotencyRepository,
    request_fingerprint,
)
from persistence.rows import (
    CapabilityArtifactRow,
    CapabilityCallReceiptRow,
    ConversationContextRow,
    InvocationTraceRow,
    TurnReceiptRow,
    WaitingCapabilityInvocationRow,
)
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.contracts import Visibility


class PartialSocietyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str | None = None
    year: int | None = None


def _engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'capability.sqlite'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            ChatConversation(
                session_id="conversation-1",
                title="Test",
                messages=[],
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )
        session.commit()
    return engine


def _repository(engine):
    partials = PartialInputRegistry()
    partials.register(
        "society_analysis",
        schema_version="1",
        model=PartialSocietyInput,
    )
    return SQLConversationCapabilityRepository(
        engine=engine,
        partial_inputs=partials,
    )


def _scenario() -> PolicyScenarioRef:
    return PolicyScenarioRef(
        artifact_id="scenario-1",
        provenance=ArtifactProvenance(
            conversation_id="conversation-1",
            turn_id="turn-1",
            capability_id="policy_reform",
            capability_version="1",
            invocation_id="invocation-1",
        ),
        year=2026,
        scenario_revision="revision-1",
        catalogue_version="catalogue-1",
        calculation_engine_version="engine-1",
        baseline=True,
    )


def test_sqlite_artifact_round_trip_and_invalid_payload_rejection(tmp_path):
    engine = _engine(tmp_path)
    repository = _repository(engine)
    scenario = _scenario()

    repository.save_artifact("conversation-1", scenario)

    assert repository.get_artifact("conversation-1", "scenario-1") == scenario
    assert repository.find_artifacts(
        "conversation-1",
        PolicyScenarioRef,
    ) == (scenario,)
    with Session(engine) as session:
        row = session.get(CapabilityArtifactRow, "scenario-1")
        row.payload_json = json.dumps(
            {
                "artifact_type": "policy_scenario",
                "schema_version": "1",
                "artifact_id": "scenario-1",
            }
        )
        session.add(row)
        session.commit()

    with pytest.raises(InvalidPersistedRecord, match="Invalid persisted artifact"):
        repository.get_artifact("conversation-1", "scenario-1")


def test_waiting_invocations_resume_branch_and_remain_independent(tmp_path):
    repository = _repository(_engine(tmp_path))
    first = WaitingCapabilityInvocation(
        invocation_id="waiting-1",
        conversation_id="conversation-1",
        capability_id="society_analysis",
        capability_version="1",
        input_schema_version="1",
        partial_input=PartialSocietyInput(instruction="raise UC"),
        source_turn_id="turn-1",
    )
    repository.create_waiting(first)

    resumed = repository.resume_waiting("waiting-1", {"year": 2026})
    branch = repository.branch_waiting("waiting-1", "waiting-2", "turn-2")
    repository.resume_waiting("waiting-2", {"year": 2025})

    assert resumed.partial_input == PartialSocietyInput(
        instruction="raise UC",
        year=2026,
    )
    assert branch.invocation_id == "waiting-2"
    assert repository.get_waiting("waiting-1").partial_input.year == 2026
    assert repository.get_waiting("waiting-2").partial_input.year == 2025
    assert len(repository.list_waiting("conversation-1")) == 2

    repository.remove_waiting("waiting-1")
    assert [
        item.invocation_id for item in repository.list_waiting("conversation-1")
    ] == ["waiting-2"]


def test_async_artifact_access_lists_updates_and_removes_waiting_input(tmp_path):
    repository = _repository(_engine(tmp_path))
    access = RepositoryArtifactAccess(repository)
    invocation = WaitingCapabilityInvocation(
        invocation_id="waiting-async",
        conversation_id="conversation-1",
        capability_id="society_analysis",
        capability_version="1",
        input_schema_version="1",
        partial_input=PartialSocietyInput(instruction="raise UC"),
        source_turn_id="turn-1",
    )

    async def exercise_access():
        await access.save_waiting(invocation)
        listed = await access.list_waiting(
            conversation_id="conversation-1",
            capability_id="society_analysis",
        )
        updated = await access.update_waiting(
            invocation_id="waiting-async",
            partial_input=PartialSocietyInput(instruction="raise UC", year=2026),
        )
        await access.remove_waiting(invocation_id="waiting-async")
        remaining = await access.list_waiting(
            conversation_id="conversation-1",
            capability_id="society_analysis",
        )
        return listed, updated, remaining

    listed, updated, remaining = asyncio.run(exercise_access())

    assert [item.invocation_id for item in listed] == ["waiting-async"]
    assert updated.partial_input.year == 2026
    assert remaining == ()


def test_trace_repository_round_trips_typed_records_and_filters_private(tmp_path):
    engine = _engine(tmp_path)
    repository = SQLInvocationTraceRepository(engine=engine)
    now = datetime.now(timezone.utc)
    public = InvocationRecord(
        conversation_id="conversation-1",
        turn_id="turn-1",
        invocation_id="trace-1",
        sequence=1,
        kind=InvocationKind.CAPABILITY,
        identifier="society_analysis",
        version="1",
        visibility=Visibility.PUBLIC,
        started_at=now,
        completed_at=now,
        duration_ms=0,
        status=InvocationStatus.COMPLETED,
        summary="society analysis completed",
    )
    private = public.model_copy(
        update={
            "invocation_id": "trace-2",
            "sequence": 2,
            "kind": InvocationKind.TOOL,
            "identifier": "validate_reform",
            "visibility": Visibility.PRIVATE,
        }
    )
    repository.save(public)
    repository.save(private)

    assert repository.list_for_conversation(
        "conversation-1", include_private=False
    ) == (public,)
    assert repository.list_for_conversation(
        "conversation-1", include_private=True
    ) == (public, private)


def test_turn_and_call_idempotency_conflicts_replay_and_billing_claim(tmp_path):
    repository = SQLIdempotencyRepository(engine=_engine(tmp_path))
    fingerprint = request_fingerprint({"messages": ["hello"]})

    started = repository.begin_turn(
        conversation_id="conversation-1",
        turn_id="turn-1",
        fingerprint=fingerprint,
    )
    in_progress = repository.begin_turn(
        conversation_id="conversation-1",
        turn_id="turn-1",
        fingerprint=fingerprint,
    )
    conflict = repository.begin_turn(
        conversation_id="conversation-1",
        turn_id="turn-1",
        fingerprint=request_fingerprint({"messages": ["different"]}),
    )
    repository.complete_turn(
        turn_id="turn-1",
        fingerprint=fingerprint,
        outcome={"content": "answer"},
    )
    replay = repository.begin_turn(
        conversation_id="conversation-1",
        turn_id="turn-1",
        fingerprint=fingerprint,
    )

    assert started.decision is IdempotencyDecision.STARTED
    assert in_progress.decision is IdempotencyDecision.IN_PROGRESS
    assert conflict.decision is IdempotencyDecision.CONFLICT
    assert replay.decision is IdempotencyDecision.REPLAY
    assert replay.outcome == {"content": "answer"}
    assert repository.claim_billing("turn-1") is True
    assert repository.claim_billing("turn-1") is False

    call = repository.begin_call(
        conversation_id="conversation-1",
        turn_id="turn-1",
        call_id="call-1",
        operation_id="run_society_simulation",
        fingerprint=fingerprint,
    )
    repository.complete_call(
        call_id="call-1",
        fingerprint=fingerprint,
        outcome={"artifact_id": "result-1"},
    )
    call_replay = repository.begin_call(
        conversation_id="conversation-1",
        turn_id="turn-1",
        call_id="call-1",
        operation_id="run_society_simulation",
        fingerprint=fingerprint,
    )
    assert call.decision is IdempotencyDecision.STARTED
    assert call_replay.status is ReceiptStatus.COMPLETED
    assert call_replay.outcome == {"artifact_id": "result-1"}


@pytest.mark.parametrize(
    "conflicting_update",
    [
        {"conversation_id": "conversation-2"},
        {"turn_id": "turn-2"},
        {"operation_id": "different_operation"},
        {"fingerprint": "different-fingerprint"},
    ],
)
def test_each_capability_call_identity_dimension_rejects_reuse(
    tmp_path,
    conflicting_update,
):
    repository = SQLIdempotencyRepository(engine=_engine(tmp_path))
    original = {
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "call_id": "call-property",
        "operation_id": "society_analysis",
        "fingerprint": "fingerprint-1",
    }
    assert repository.begin_call(**original).decision is IdempotencyDecision.STARTED

    conflicting = {**original, **conflicting_update}
    assert repository.begin_call(**conflicting).decision is IdempotencyDecision.CONFLICT


def test_competing_turn_retries_start_only_once(tmp_path):
    repository = SQLIdempotencyRepository(engine=_engine(tmp_path))
    fingerprint = request_fingerprint({"turn": 1})

    def begin():
        return repository.begin_turn(
            conversation_id="conversation-1",
            turn_id="turn-concurrent",
            fingerprint=fingerprint,
        ).decision

    with ThreadPoolExecutor(max_workers=4) as pool:
        decisions = list(pool.map(lambda _: begin(), range(4)))

    assert decisions.count(IdempotencyDecision.STARTED) == 1
    assert decisions.count(IdempotencyDecision.IN_PROGRESS) == 3


def test_deletion_removes_only_conversation_owned_capability_rows(tmp_path):
    engine = _engine(tmp_path)
    repository = _repository(engine)
    repository.save_artifact("conversation-1", _scenario())
    repository.create_waiting(
        WaitingCapabilityInvocation(
            invocation_id="waiting-1",
            conversation_id="conversation-1",
            capability_id="society_analysis",
            capability_version="1",
            input_schema_version="1",
            partial_input=PartialSocietyInput(),
            source_turn_id="turn-1",
        )
    )
    idempotency = SQLIdempotencyRepository(engine=engine)
    idempotency.begin_turn(
        conversation_id="conversation-1",
        turn_id="turn-1",
        fingerprint="fingerprint",
    )
    SQLConversationContextRepository(engine=engine).save(
        ConversationContext.initial("conversation-1"),
        expected_revision=0,
    )

    with Session(engine) as session:
        delete_capability_records(session, "conversation-1")
        session.commit()

    with Session(engine) as session:
        assert session.exec(select(CapabilityArtifactRow)).all() == []
        assert session.exec(select(WaitingCapabilityInvocationRow)).all() == []
        assert session.exec(select(InvocationTraceRow)).all() == []
        assert session.exec(select(TurnReceiptRow)).all() == []
        assert session.exec(select(CapabilityCallReceiptRow)).all() == []
        assert session.exec(select(ConversationContextRow)).all() == []
        assert session.exec(select(ChatConversation)).one().session_id == "conversation-1"


def test_additive_alembic_revision_uses_conversation_identity_without_foreign_key():
    migration = (
        Path(__file__).parents[2]
        / "backend"
        / "migrations"
        / "versions"
        / "0002_add_capability_persistence_tables.py"
    ).read_text()

    assert migration.count("sa.Column('conversation_id'") == 5
    assert "ForeignKeyConstraint" not in migration
    assert "chat_conversations" not in migration
    assert "capability_artifacts" in migration
    assert "waiting_capability_invocations" in migration
    assert "capability_invocation_traces" in migration
    assert "capability_turn_receipts" in migration
    assert "capability_call_receipts" in migration
    assert "analysis_workflows" not in migration
    assert "analysis_plans" not in migration
    assert "analysis_request_revisions" not in migration


def test_context_alembic_revision_was_cli_autogenerated_and_is_additive():
    migration = (
        Path(__file__).parents[2]
        / "backend"
        / "migrations"
        / "versions"
        / "9526d8c80914_add_conversation_context_persistence.py"
    ).read_text()

    assert "commands auto generated by Alembic" in migration
    assert "op.create_table('conversation_contexts'" in migration
    assert "op.create_index('idx_conversation_context_updated'" in migration
    assert "ForeignKeyConstraint" not in migration
    assert "analysis_" not in migration


POSTGRES_URL = os.environ.get("CAPABILITY_TEST_POSTGRES_URL")


@pytest.mark.skipif(
    not POSTGRES_URL,
    reason="CAPABILITY_TEST_POSTGRES_URL is not configured for disposable PostgreSQL tests",
)
def test_disposable_postgres_uses_the_same_typed_repository_contract(tmp_path):
    del tmp_path
    assert POSTGRES_URL is not None
    schema = f"uk_chat_pr_{os.getpid()}"
    drop_preview_schema(POSTGRES_URL, schema)
    ensure_database_schema(POSTGRES_URL, schema)
    engine = namespaced_engine(POSTGRES_URL, schema)
    SQLModel.metadata.create_all(engine)
    conversation_id = f"capability-postgres-{os.getpid()}"
    try:
        with Session(engine) as session:
            session.add(
                ChatConversation(
                    session_id=conversation_id,
                    title="Disposable test",
                    messages=[],
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
            session.commit()
        scenario = _scenario().model_copy(
            update={
                "artifact_id": f"scenario-{os.getpid()}",
                "provenance": _scenario().provenance.model_copy(
                    update={"conversation_id": conversation_id}
                ),
            }
        )
        repository = SQLConversationCapabilityRepository(engine=engine)
        repository.save_artifact(conversation_id, scenario)
        assert repository.get_artifact(conversation_id, scenario.artifact_id) == scenario
    finally:
        with Session(engine) as session:
            delete_capability_records(session, conversation_id)
            row = session.exec(
                select(ChatConversation).where(
                    ChatConversation.session_id == conversation_id
                )
            ).first()
            if row is not None:
                session.delete(row)
            session.commit()
        engine.dispose()
        drop_preview_schema(POSTGRES_URL, schema)
