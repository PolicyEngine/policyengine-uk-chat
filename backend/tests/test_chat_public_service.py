import asyncio
import json

import pytest

from chat.events import (
    ChatUsage,
    SuggestionsGenerated,
    TextChunk,
    ToolCompleted,
    ToolUsed,
    TurnCancelled,
    TurnCompleted,
)
from chat.schemas import ChatRequest
from gateway.trace import GatewayTrace, GatewayTraceSlot


async def connected():
    return False


def parse_sse(chunks):
    return [json.loads(chunk.removeprefix("data: ").strip()) for chunk in chunks]


def test_public_service_projects_events_and_records_usage_once(monkeypatch):
    import billing
    from chat import public_service

    raw_output = {"status": "success", "secret_internal_field": "do-not-expose"}

    async def fake_turn(*_args, **_kwargs):
        yield TextChunk("Answer")
        yield ToolUsed("run_society_simulation", "tool-1", {"year": 2026})
        yield ToolCompleted(
            "run_society_simulation", "tool-1", "success", raw_output
        )
        yield TurnCompleted(
            content="Answer",
            session_id="session-1",
            model="claude",
            route="compute",
            outcome="ready",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=12, output_tokens=3),
            gateway_trace=GatewayTrace(
                selected_tool="run_society_simulation",
                slots=(
                    GatewayTraceSlot(
                        name="reform",
                        kind="tool_input",
                        source="prompt",
                        value="private evidence",
                    ),
                ),
                defaults_applied={"year": 2026},
                catalogue_recovery_used=True,
            ),
        )
        yield SuggestionsGenerated(["What next?"])

    usage_calls = []
    monkeypatch.setattr(public_service, "run_chat_turn", fake_turn)
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

    chunks = asyncio.run(collect())
    events = parse_sse(chunks)

    assert [event["type"] for event in events] == [
        "chunk",
        "tool_use",
        "tool_result",
        "done",
        "suggestions",
    ]
    tool_result = events[2]
    assert "result_summary" in tool_result
    assert "output" not in tool_result
    done = events[3]
    assert done["usage"]["input_tokens"] == 12
    assert done["cost_gbp"] == 0.01
    assert done["balance"] == 9.99
    assert "gateway_trace" not in done
    assert "slots" not in done
    assert "defaults_applied" not in done
    assert "catalogue_recovery_used" not in done
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
            route="compute",
            usage=ChatUsage(input_tokens=4),
        )

    usage_calls = []
    monkeypatch.setattr(public_service, "run_chat_turn", fake_turn)
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
