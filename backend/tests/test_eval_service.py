import asyncio
from datetime import datetime, timezone
import json

from fastapi.testclient import TestClient

from api.main import app
from capabilities.tracing import InvocationKind, InvocationRecord, InvocationStatus
from chat.events import (
    ChatUsage,
    InvocationActivity,
    SuggestionsGenerated,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.schemas import ChatRequest
from eval.schemas import EvalChatResponse
from tools.contracts import Visibility


async def connected():
    return False


def invocation_record(*, status=InvocationStatus.COMPLETED):
    return InvocationRecord(
        conversation_id="eval-session",
        turn_id="turn-1",
        invocation_id="invocation-1",
        sequence=1,
        kind=InvocationKind.TOOL,
        identifier="run_society_simulation",
        version="1",
        visibility=Visibility.PRIVATE,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_ms=4,
        status=status,
        summary="Society simulation completed.",
        debug_input={"year": 2026, "reform": {"rate": 0.19}},
        debug_output={"budgetary_impact": -1_000_000_000},
    )


def test_eval_service_collects_complete_invocation_trace_and_stops_at_completion(monkeypatch):
    from eval import service

    advanced_after_completion = False

    async def fake_turn(*_args, **_kwargs):
        nonlocal advanced_after_completion
        yield InvocationActivity("finished", invocation_record())
        yield TurnCompleted(
            content="The reform costs £1bn annually.",
            session_id="eval-session",
            model="claude-sonnet",
            route="capability",
            outcome="ready",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=20, output_tokens=5),
            turn_id="turn-1",
        )
        advanced_after_completion = True
        yield SuggestionsGenerated(["Another question?"])

    monkeypatch.setattr(service, "run_capability_chat_turn", fake_turn)

    response = asyncio.run(
        service.run_eval_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate it"}],
                session_id="eval-session",
            ),
            is_cancelled=connected,
        )
    )

    assert response.status == "completed"
    assert response.content == "The reform costs £1bn annually."
    assert response.usage.input_tokens == 20
    assert response.invocation_trace[0].name == "run_society_simulation"
    assert response.invocation_trace[0].input["reform"]["rate"] == 0.19
    assert response.invocation_trace[0].output == {
        "budgetary_impact": -1_000_000_000
    }
    assert advanced_after_completion is False


def test_eval_service_returns_structured_failure_with_invocation_trace(monkeypatch):
    from eval import service

    async def fake_turn(*_args, **_kwargs):
        yield InvocationActivity(
            "finished",
            invocation_record(status=InvocationStatus.FAILED),
        )
        yield TurnFailed(
            content="The capability could not complete.",
            session_id="eval-session",
            stop_reason="capability_failed",
            usage=ChatUsage(input_tokens=30),
            billable=True,
            turn_id="turn-1",
        )

    monkeypatch.setattr(service, "run_capability_chat_turn", fake_turn)

    response = asyncio.run(
        service.run_eval_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate it"}],
                session_id="eval-session",
            ),
            is_cancelled=connected,
        )
    )

    assert response.status == "failed"
    assert response.stop_reason == "capability_failed"
    assert response.usage.input_tokens == 30
    assert response.invocation_trace[0].status == "failed"


def test_eval_service_retains_trace_on_cancellation(monkeypatch):
    from eval import service

    async def fake_turn(*_args, **_kwargs):
        yield InvocationActivity("finished", invocation_record())
        yield TurnCancelled(
            session_id="eval-session",
            model=None,
            route="capability",
            usage=ChatUsage(input_tokens=4),
            turn_id="turn-1",
        )

    monkeypatch.setattr(service, "run_capability_chat_turn", fake_turn)

    response = asyncio.run(
        service.run_eval_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate it"}],
                session_id="eval-session",
            ),
            is_cancelled=connected,
        )
    )

    assert response.status == "failed"
    assert response.stop_reason == "client_disconnected"
    assert response.invocation_trace[0].name == "run_society_simulation"


def test_public_and_eval_adapters_preserve_turn_parity(monkeypatch):
    import billing
    from chat import public_service
    from eval import service

    async def event_stream(*_args, **_kwargs):
        yield InvocationActivity("finished", invocation_record())
        yield TurnCompleted(
            content="Annual impact: £1bn.",
            session_id="eval-session",
            model="claude",
            route="capability",
            outcome="ready",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=10, output_tokens=2),
            turn_id="turn-1",
        )

    monkeypatch.setattr(public_service, "run_capability_chat_turn", event_stream)
    monkeypatch.setattr(service, "run_capability_chat_turn", event_stream)
    monkeypatch.setattr(
        billing,
        "record_usage",
        lambda **_kwargs: {"cost_gbp": 0.0, "balance": 1.0},
    )
    request = ChatRequest(
        messages=[{"role": "user", "content": "Calculate"}],
        session_id="eval-session",
    )

    async def collect_public():
        stream = await public_service.start_public_chat(
            request,
            is_cancelled=connected,
        )
        return [
            json.loads(chunk.removeprefix("data: ").strip())
            async for chunk in stream
        ]

    public_events = asyncio.run(collect_public())
    eval_response = asyncio.run(
        service.run_eval_chat(request, is_cancelled=connected)
    )
    public_done = next(event for event in public_events if event["type"] == "done")
    public_invocations = [
        event["invocation"]["identifier"]
        for event in public_events
        if event["type"] == "invocation_activity"
    ]

    assert public_done["content"] == eval_response.content
    assert public_done["model"] == eval_response.model
    assert public_done["route"] == eval_response.route
    assert public_done["outcome"] == eval_response.outcome
    assert public_done["stop_reason"] == eval_response.stop_reason
    assert public_done["usage"] == eval_response.usage.model_dump()
    assert public_invocations == [
        trace.name for trace in eval_response.invocation_trace
    ]


def test_eval_route_fails_closed_and_accepts_only_the_configured_token(monkeypatch):
    import eval.routes as routes

    client = TestClient(app)
    body = {"messages": [{"role": "user", "content": "Calculate it"}]}
    monkeypatch.delenv("UK_CHAT_EVAL_TOKEN", raising=False)

    assert client.post("/eval/chat/message", json=body).status_code == 503

    monkeypatch.setenv("UK_CHAT_EVAL_TOKEN", "server-secret")
    assert client.post("/eval/chat/message", json=body).status_code == 401
    assert (
        client.post(
            "/eval/chat/message",
            json=body,
            headers={"X-Eval-Token": "wrong-secret"},
        ).status_code
        == 401
    )

    async def fake_eval(*_args, **_kwargs):
        return EvalChatResponse(
            status="completed",
            content="Answer",
            session_id="eval-session",
            model="claude",
            route="capability",
            outcome="ready",
            stop_reason="end_turn",
        )

    monkeypatch.setattr(routes, "run_eval_chat", fake_eval)
    response = client.post(
        "/eval/chat/message",
        json=body,
        headers={"X-Eval-Token": "server-secret"},
    )

    assert response.status_code == 200
    assert response.json()["content"] == "Answer"
