import asyncio
from datetime import datetime, timezone
import json

import pytest

from capabilities.tracing import InvocationKind, InvocationRecord, InvocationStatus
from chat.events import (
    ChatUsage,
    InvocationActivity,
    SuggestionsGenerated,
    TextChunk,
    TurnCancelled,
    TurnCompleted,
)
from chat.schemas import ChatRequest
from tools.contracts import Visibility


async def connected():
    return False


def parse_sse(chunks):
    return [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]


def invocation_record(*, status=InvocationStatus.COMPLETED):
    return InvocationRecord(
        conversation_id="session-1",
        turn_id="turn-1",
        invocation_id="invocation-1",
        sequence=1,
        kind=InvocationKind.TOOL,
        identifier="run_society_simulation",
        version="1",
        visibility=Visibility.PUBLIC,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_ms=4,
        status=status,
        summary="Society simulation completed.",
        debug_input={"year": 2026},
        debug_output={"budgetary_impact": -1_000_000_000},
    )


def test_public_service_projects_invocation_activity_and_records_usage_once(monkeypatch):
    import billing
    from chat import public_service

    async def fake_turn(*_args, **_kwargs):
        yield TextChunk("Answer")
        yield InvocationActivity("finished", invocation_record())
        yield TurnCompleted(
            content="Answer",
            session_id="session-1",
            model="claude",
            route="capability",
            outcome="ready",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=12, output_tokens=3),
            turn_id="turn-1",
        )
        yield SuggestionsGenerated(["What next?"])

    usage_calls = []
    monkeypatch.setattr(public_service, "run_capability_chat_turn", fake_turn)
    monkeypatch.setattr(
        "persistence.idempotency.SQLIdempotencyRepository.claim_billing",
        lambda _self, _turn_id: True,
    )
    monkeypatch.setattr(
        billing,
        "record_usage",
        lambda **kwargs: usage_calls.append(kwargs)
        or {"cost_gbp": 0.01, "balance": 9.99},
    )

    async def collect():
        stream = await public_service.start_public_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="session-1",
            ),
            is_cancelled=connected,
        )
        return [chunk async for chunk in stream]

    events = parse_sse(asyncio.run(collect()))

    assert [event["type"] for event in events] == [
        "chunk",
        "invocation_activity",
        "done",
        "suggestions",
    ]
    invocation = events[1]["invocation"]
    assert invocation["identifier"] == "run_society_simulation"
    assert invocation["debug_input"] == {"year": 2026}
    assert "conversation_id" not in invocation
    done = events[2]
    assert done["usage"]["input_tokens"] == 12
    assert done["cost_gbp"] == 0.01
    assert done["balance"] == 9.99
    assert len(usage_calls) == 1


def test_public_service_rejects_empty_balance_before_starting_stream(monkeypatch):
    import billing
    from chat import public_service

    monkeypatch.setattr(billing, "check_balance", lambda _user_id: (False, {}))

    with pytest.raises(public_service.InsufficientCredit):
        asyncio.run(
            public_service.start_public_chat(
                ChatRequest(
                    messages=[{"role": "user", "content": "Calculate"}],
                    user_id="user-1",
                ),
                is_cancelled=connected,
            )
        )


def test_public_service_bills_cancelled_usage_without_emitting_sse(monkeypatch):
    import billing
    from chat import public_service

    async def fake_turn(*_args, **_kwargs):
        yield TurnCancelled(
            session_id="session-1",
            model="claude",
            route="capability",
            usage=ChatUsage(input_tokens=4),
            turn_id="turn-1",
        )

    usage_calls = []
    monkeypatch.setattr(public_service, "run_capability_chat_turn", fake_turn)
    monkeypatch.setattr(
        "persistence.idempotency.SQLIdempotencyRepository.claim_billing",
        lambda _self, _turn_id: True,
    )
    monkeypatch.setattr(
        billing,
        "record_usage",
        lambda **kwargs: usage_calls.append(kwargs) or None,
    )

    async def collect():
        stream = await public_service.start_public_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="session-1",
            ),
            is_cancelled=connected,
        )
        return [chunk async for chunk in stream]

    assert asyncio.run(collect()) == []
    assert len(usage_calls) == 1
    assert usage_calls[0]["input_tokens"] == 4
