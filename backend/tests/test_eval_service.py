import asyncio
from datetime import date
import json

from fastapi.testclient import TestClient

from api.main import app
from chat.events import (
    ChatUsage,
    SuggestionsGenerated,
    ToolCompleted,
    ToolUsed,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.schemas import ChatRequest
from analysis.trace import AnalysisTrace
from eval.schemas import EvalChatResponse


async def connected():
    return False


TRACE = AnalysisTrace(
    workflow_version=4,
    update_kind="start_analysis",
    binding_outcome="ready",
    execution_mode="standard",
    permitted_operations=(
        "run_society_simulation",
        "compute_budgetary_impact",
    ),
)


def test_eval_service_collects_complete_trace_and_stops_at_completion(monkeypatch):
    from eval import service

    advanced_after_completion = False

    async def fake_turn(*_args, **_kwargs):
        nonlocal advanced_after_completion
        yield ToolUsed(
            "run_society_simulation",
            "tool-1",
            {"year": 2026, "reform": {"rate": 0.19}},
        )
        yield ToolCompleted(
            "run_society_simulation",
            "tool-1",
            "success",
            {date(2026, 1, 1): {"budgetary_impact": -1_000_000_000}},
        )
        yield TurnCompleted(
            content="The reform costs £1bn annually.",
            session_id="eval-session",
            model="claude-sonnet",
            route="compute",
            outcome="ready",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=20, output_tokens=5),
            analysis_trace=TRACE,
        )
        advanced_after_completion = True
        yield SuggestionsGenerated(["Another question?"])

    monkeypatch.setattr(service, "run_chat_turn", fake_turn)

    response = asyncio.run(
        service.run_eval_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate it"}],
                session_id="eval-session",
                user_id="must-not-be-billed",
            ),
            is_cancelled=connected,
        )
    )

    assert response.status == "completed"
    assert response.content == "The reform costs £1bn annually."
    assert response.usage.input_tokens == 20
    assert response.tool_trace[0].input["reform"]["rate"] == 0.19
    assert response.tool_trace[0].output == {
        "2026-01-01": {"budgetary_impact": -1_000_000_000}
    }
    assert advanced_after_completion is False
    assert response.analysis_trace.update_kind == "start_analysis"
    assert response.analysis_trace.binding_outcome == "ready"
    assert response.analysis_trace.execution_mode == "standard"
    assert response.analysis_trace.permitted_operations == [
        "run_society_simulation",
        "compute_budgetary_impact",
    ]


def test_eval_service_returns_structured_terminal_failure(monkeypatch):
    from eval import service

    async def fake_turn(*_args, **_kwargs):
        yield TurnFailed(
            content="Agent appears to be stuck in a loop.",
            session_id="eval-session",
            stop_reason="loop_detected",
            usage=ChatUsage(input_tokens=30),
            billable=True,
            analysis_trace=TRACE,
        )

    monkeypatch.setattr(service, "run_chat_turn", fake_turn)

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
    assert response.stop_reason == "loop_detected"
    assert response.usage.input_tokens == 30
    assert response.analysis_trace.workflow_version == 4


def test_eval_service_retains_trace_on_cancellation(monkeypatch):
    from eval import service

    async def fake_turn(*_args, **_kwargs):
        yield TurnCancelled(
            session_id="eval-session",
            model=None,
            route="lightweight",
            usage=ChatUsage(input_tokens=4),
            analysis_trace=TRACE,
        )

    monkeypatch.setattr(service, "run_chat_turn", fake_turn)

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
    assert "compute_budgetary_impact" in response.analysis_trace.permitted_operations


def test_public_and_eval_adapters_preserve_turn_parity(monkeypatch):
    import billing
    from chat import public_service
    from eval import service

    def event_stream():
        async def generate():
            yield ToolUsed("run_society_simulation", "tool-1", {"year": 2026})
            yield ToolCompleted(
                "run_society_simulation",
                "tool-1",
                "success",
                {"budgetary_impact": -100},
            )
            yield TurnCompleted(
                content="Annual impact: £100.",
                session_id="parity-session",
                model="claude",
                route="compute",
                outcome="ready",
                stop_reason="end_turn",
                usage=ChatUsage(input_tokens=10, output_tokens=2),
            )

        return generate()

    monkeypatch.setattr(public_service, "run_chat_turn", lambda *_a, **_k: event_stream())
    monkeypatch.setattr(service, "run_chat_turn", lambda *_a, **_k: event_stream())
    monkeypatch.setattr(
        billing,
        "record_usage",
        lambda **_kwargs: {"cost_gbp": 0.0, "balance": 1.0},
    )
    request = ChatRequest(
        messages=[{"role": "user", "content": "Calculate"}],
        session_id="parity-session",
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
    public_tools = [
        event["tool_name"]
        for event in public_events
        if event["type"] == "tool_use"
    ]

    assert public_done["content"] == eval_response.content
    assert public_done["model"] == eval_response.model
    assert public_done["route"] == eval_response.route
    assert public_done["outcome"] == eval_response.outcome
    assert public_done["stop_reason"] == eval_response.stop_reason
    assert public_done["usage"] == eval_response.usage.model_dump()
    assert public_tools == [trace.name for trace in eval_response.tool_trace]


def test_eval_route_fails_closed_and_accepts_only_the_configured_token(
    monkeypatch,
):
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
            route="compute",
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
