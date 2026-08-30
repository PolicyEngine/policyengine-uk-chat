from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from api.main import app
from capabilities.tracing import (
    InvocationKind,
    InvocationRecord,
    InvocationStatus,
    InvocationTracer,
)
from chat.events import (
    ChatUsage,
    InvocationActivity,
    TextChunk,
    TurnCompleted,
)
from conversations.models import get_engine
from persistence.deletion import delete_capability_records
from persistence.rows import InvocationTraceRow
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.contracts import Visibility


client = TestClient(app)


def _events(response) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def _record(
    identifier: str,
    sequence: int,
    *,
    visibility: Visibility = Visibility.PUBLIC,
    status: InvocationStatus = InvocationStatus.COMPLETED,
    debug_details: bool = False,
) -> InvocationRecord:
    now = datetime.now(timezone.utc)
    return InvocationRecord(
        conversation_id="api-session",
        turn_id="api-turn",
        invocation_id=f"invocation-{sequence}",
        sequence=sequence,
        kind=(
            InvocationKind.CAPABILITY
            if identifier not in {"assess_relevance", "validate_reform"}
            else InvocationKind.TOOL
        ),
        identifier=identifier,
        version="1",
        visibility=visibility,
        started_at=now,
        completed_at=now,
        duration_ms=1,
        status=status,
        summary=f"operation {identifier} {status.value}",
        debug_input={"identifier": identifier} if debug_details else None,
        debug_output={"status": status.value} if debug_details else None,
    )


def test_public_api_projects_direct_mandatory_mixed_and_localized_outcomes(monkeypatch):
    from chat import public_service

    monkeypatch.setenv("BILLING_ENABLED", "false")

    async def fake_capability_turn(turn, *, is_cancelled):
        del is_cancelled
        prompt = str(turn.messages[-1]["content"])
        identifiers: list[str] = []
        if "mixed" in prompt:
            identifiers = [
                "policy_information",
                "household_analysis",
                "society_analysis",
            ]
        elif "clarify" in prompt:
            identifiers = ["household_analysis"]
        if turn.debug:
            yield InvocationActivity(
                phase="finished",
                record=_record(
                    "assess_relevance",
                    1,
                    visibility=Visibility.PRIVATE,
                    debug_details=True,
                ),
            )
            fact_record = _record(
                "reduce_context_patch",
                2,
                visibility=Visibility.PRIVATE,
                debug_details=True,
            ).model_copy(
                update={
                    "kind": InvocationKind.TOOL,
                    "debug_input": {
                        "patch": {
                            "expected_revision": 0,
                            "operations": [{"definition_key": "person.age"}],
                        }
                    },
                    "debug_output": {
                        "context": {"revision": 1},
                        "decisions": [
                            {
                                "status": "accepted",
                                "definition_key": "person.age",
                                "subject_entity_id": "person:self",
                            }
                        ],
                    },
                }
            )
            yield InvocationActivity(phase="finished", record=fact_record)
        for offset, identifier in enumerate(
            identifiers,
            start=3 if turn.debug else 2,
        ):
            status = (
                InvocationStatus.NEEDS_INPUT
                if "clarify" in prompt
                else InvocationStatus.COMPLETED
            )
            yield InvocationActivity(
                phase="finished",
                record=_record(
                    identifier,
                    offset,
                    status=status,
                    debug_details=turn.debug,
                ),
            )
        answer = (
            "Which household ages and relationships should I use?"
            if "clarify" in prompt
            else "Natural response."
        )
        yield TextChunk(answer)
        yield TurnCompleted(
            content=answer,
            session_id="api-session",
            model="fake-model",
            route="capability",
            outcome="completed",
            stop_reason="end_turn",
            usage=ChatUsage(),
            turn_id=turn.turn_id,
        )

    monkeypatch.setattr(
        public_service,
        "run_capability_chat_turn",
        fake_capability_turn,
    )

    direct = client.post(
        "/chat/message",
        json={"messages": [{"role": "user", "content": "direct"}]},
    )
    mixed = client.post(
        "/chat/message",
        json={"messages": [{"role": "user", "content": "mixed"}]},
    )
    clarification = client.post(
        "/chat/message",
        json={"messages": [{"role": "user", "content": "clarify"}]},
    )
    debug = client.post(
        "/chat/message",
        json={
            "messages": [{"role": "user", "content": "mixed"}],
            "debug": True,
        },
    )

    assert [event["type"] for event in _events(direct)] == ["chunk", "done"]
    mixed_activity = [
        event["invocation"]["identifier"]
        for event in _events(mixed)
        if event["type"] == "invocation_activity"
    ]
    assert mixed_activity == [
        "policy_information",
        "household_analysis",
        "society_analysis",
    ]
    assert all(
        "debug_input" not in event["invocation"]
        and "debug_output" not in event["invocation"]
        for event in _events(mixed)
        if event["type"] == "invocation_activity"
    )
    clarification_events = _events(clarification)
    assert next(
        event["invocation"]["status"]
        for event in clarification_events
        if event["type"] == "invocation_activity"
    ) == "needs_input"
    assert "Which household" in clarification_events[-1]["content"]
    debug_activity = [
        event["invocation"]
        for event in _events(debug)
        if event["type"] == "invocation_activity"
    ]
    assert debug_activity[0]["identifier"] == "assess_relevance"
    assert debug_activity[0]["visibility"] == "private"
    assert all("conversation_id" not in item for item in debug_activity)
    assert all("debug_input" in item and "debug_output" in item for item in debug_activity)
    fact_activity = next(
        item for item in debug_activity if item["identifier"] == "reduce_context_patch"
    )
    assert fact_activity["debug_output"]["decisions"][0] == {
        "status": "accepted",
        "definition_key": "person.age",
        "subject_entity_id": "person:self",
    }


def test_conversation_delete_removes_trace_endpoint_records(
    isolated_conversations_table,
):
    del isolated_conversations_table
    saved = client.post(
        "/conversations",
        json={
            "session_id": "delete-capability-session",
            "title": "Delete capability state",
            "messages": [{"role": "user", "content": "hello"}],
            "user_id": "owner-1",
        },
    )
    assert saved.status_code == 200
    tracer = InvocationTracer(sink=SQLInvocationTraceRepository())
    record = tracer.start(
        conversation_id="delete-capability-session",
        turn_id="turn-1",
        parent_invocation_id=None,
        kind=InvocationKind.CAPABILITY,
        identifier="policy_information",
        version="1",
        visibility=Visibility.PUBLIC,
        summary="started",
    )
    tracer.finish(
        record.invocation_id,
        status=InvocationStatus.COMPLETED,
        summary="completed",
    )
    activity = client.get(
        "/chat/delete-capability-session/activity",
        params={"user_id": "owner-1", "debug": "true"},
    )
    assert activity.status_code == 200
    assert len(activity.json()["invocations"]) == 1

    deleted = client.delete(f"/conversations/{saved.json()['id']}")

    assert deleted.status_code == 204
    assert client.get(
        "/chat/delete-capability-session/activity",
        params={"user_id": "owner-1", "debug": "true"},
    ).status_code == 404
    with Session(get_engine()) as session:
        assert session.exec(select(InvocationTraceRow)).all() == []
