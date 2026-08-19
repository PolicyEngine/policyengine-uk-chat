import asyncio
import json
from types import SimpleNamespace

import pytest

from chat.projector import public_events
from analysis.models import (
    BillingChargeInput,
    BillingIntent,
    CancelledTurnOutcome,
    CompletedTurnOutcome,
    ConflictTurnOutcome,
    FailedTurnOutcome,
    ResponseArtifact,
    StillProcessingTurnOutcome,
)
from analysis.trace import AnalysisTrace
from chat.events import (
    ChatUsage,
    SuggestionsGenerated,
    TextChunk,
    ToolCompleted,
    ToolUsed,
    TurnCancelled,
    TurnCompleted,
    TurnConflict,
    TurnFailed,
)
from chat.schemas import ChatRequest


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
            route="standard",
            outcome="completed",
            stop_reason="end_turn",
            usage=ChatUsage(input_tokens=12, output_tokens=3),
            analysis_trace=AnalysisTrace(
                workflow_version=3,
                update_kind="start_analysis",
                binding_outcome="ready",
                plan_id="private-plan-id",
                permitted_operations=("run_society_simulation",),
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
    assert "analysis_trace" not in done
    assert "plan_id" not in done
    assert "permitted_operations" not in done
    assert len(usage_calls) == 1


def test_public_service_delivers_request_local_chart_artifact(monkeypatch):
    from chat import public_service

    chart_markdown = '```chart\n{"type":"bar","private_dataset":9173}\n```'

    async def fake_turn(*_args, **_kwargs):
        yield TurnCompleted(
            content="Here is the chart.",
            session_id="session-1",
            turn_id="turn-1",
            model="claude",
            route="standard",
            outcome="completed",
            stop_reason="end_turn",
            usage=ChatUsage(),
            response_artifacts=(
                ResponseArtifact(
                    artifact_id="chart_budget",
                    content=chart_markdown,
                ),
            ),
        )

    monkeypatch.setattr(public_service, "run_chat_turn", fake_turn)

    async def collect():
        stream = await public_service.start_public_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Chart it"}],
                session_id="session-1",
                turn_id="turn-1",
            ),
            is_cancelled=connected,
        )
        return [chunk async for chunk in stream]

    [event] = parse_sse(asyncio.run(collect()))
    assert event["artifacts"] == [
        {
            "kind": "chart",
            "artifact_id": "chart_budget",
            "content": chart_markdown,
        }
    ]


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


def test_public_service_bills_failed_model_usage_with_responsible_model(monkeypatch):
    import billing
    from chat import public_service

    async def fake_turn(*_args, **_kwargs):
        yield TurnFailed(
            content="The response could not be validated.",
            session_id="session-1",
            turn_id="turn-1",
            model="narration-model",
            stop_reason="execution_failed",
            usage=ChatUsage(input_tokens=8, output_tokens=2),
            billable=True,
            analysis_trace=AnalysisTrace(
                workflow_version=3,
                plan_id="private-plan",
            ),
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
                turn_id="turn-1",
            ),
            is_cancelled=connected,
        )
        return [chunk async for chunk in stream]

    events = parse_sse(asyncio.run(collect()))
    assert events == [
        {
            "type": "error",
            "content": "The response could not be validated.",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "model": "narration-model",
            "route": "failed",
            "outcome": "failed",
            "stop_reason": "execution_failed",
            "usage": {
                "input_tokens": 8,
                "output_tokens": 2,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            "cost_gbp": None,
            "balance": None,
            "billable": True,
        }
    ]
    assert usage_calls[0]["model"] == "narration-model"
    assert usage_calls[0]["turn_id"] == "turn-1"


def test_public_service_retries_pending_billing_from_durable_inputs(monkeypatch):
    import billing
    from chat import public_service

    intent = BillingIntent(
        billing_intent_id="billing-1",
        session_id="session-1",
        turn_id="turn-1",
        user_id="user-1",
        usage_entry_ids=("usage-1",),
        charge_inputs=(
            BillingChargeInput(
                usage_entry_id="usage-1",
                operation="narration",
                model="narration-model",
                input_tokens=8,
                output_tokens=2,
                cost_gbp=0.02,
            ),
        ),
    )

    class Store:
        recorded = []

        def pending_billing_intents(self, *, user_id):
            assert user_id == "user-1"
            return (intent,)

        def mark_billing_recorded(self, session_id, turn_id):
            self.recorded.append((session_id, turn_id))
            return True

    usage_calls = []
    monkeypatch.setattr(public_service, "billing_enabled", lambda: True)
    monkeypatch.setattr(
        billing,
        "record_usage",
        lambda **kwargs: usage_calls.append(kwargs) or {"recorded": True},
    )
    store = Store()

    public_service._retry_pending_billing_intents(
        user_id="user-1",
        store=store,
    )

    assert usage_calls[0]["turn_id"] == "turn-1"
    assert usage_calls[0]["input_tokens"] == 8
    assert usage_calls[0]["usage_entries"] == [
        {
            "usage_entry_id": "usage-1",
            "operation": "narration",
            "model": "narration-model",
            "input_tokens": 8,
            "output_tokens": 2,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cost_gbp": 0.02,
        }
    ]
    assert store.recorded == [("session-1", "turn-1")]


def test_public_service_projects_complete_conflict_event_without_billing(monkeypatch):
    import billing
    from chat import public_service

    async def fake_turn(*_args, **_kwargs):
        yield TurnConflict(
            content="Retry against the latest conversation state.",
            session_id="session-1",
            turn_id="turn-1",
            retryable=True,
        )

    usage_calls = []
    monkeypatch.setattr(public_service, "run_chat_turn", fake_turn)
    monkeypatch.setattr(
        billing,
        "record_usage",
        lambda **kwargs: usage_calls.append(kwargs),
    )

    async def collect():
        stream = await public_service.start_public_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="session-1",
                turn_id="turn-1",
            ),
            is_cancelled=connected,
        )
        return [chunk async for chunk in stream]

    assert parse_sse(asyncio.run(collect())) == [
        {
            "type": "conflict",
            "content": "Retry against the latest conversation state.",
            "session_id": "session-1",
            "turn_id": "turn-1",
            "retryable": True,
        }
    ]
    assert usage_calls == []


@pytest.mark.parametrize(
    ("outcome", "expected_types", "completed_outcome"),
    [
        (
            StillProcessingTurnOutcome(content="Still processing."),
            ["processed_duplicate", "chunk", "done"],
            "still_processing",
        ),
        (
            CompletedTurnOutcome(
                content="Completed response.", route="standard", duplicate=True
            ),
            ["processed_duplicate", "chunk", "done"],
            "completed",
        ),
        (
            ConflictTurnOutcome(
                content="Retry the request.", retryable=True, duplicate=True
            ),
            ["processed_duplicate", "conflict"],
            None,
        ),
        (
            FailedTurnOutcome(
                content="The calculation failed.",
                error_code="execution_failed",
                duplicate=True,
            ),
            ["processed_duplicate", "error"],
            None,
        ),
        (
            CancelledTurnOutcome(
                content="The calculation was cancelled.", duplicate=True
            ),
            ["processed_duplicate", "cancellation", "chunk", "done"],
            "cancelled",
        ),
    ],
)
def test_public_service_preserves_duplicate_outcome_categories_without_billing(
    monkeypatch,
    outcome,
    expected_types,
    completed_outcome,
):
    from chat import public_service

    emitted = public_events(
        outcome=outcome,
        session_id="session-duplicate",
        turn_id="turn-duplicate",
    )

    async def fake_turn(*_args, **_kwargs):
        for event in emitted:
            yield event

    usage_calls = []
    monkeypatch.setattr(public_service, "run_chat_turn", fake_turn)
    monkeypatch.setattr(public_service, "billing_enabled", lambda: True)
    monkeypatch.setattr(
        public_service.billing,
        "record_usage",
        lambda **kwargs: usage_calls.append(kwargs) or {"recorded": True},
    )

    async def collect():
        stream = await public_service.start_public_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="session-duplicate",
                turn_id="turn-duplicate",
            ),
            is_cancelled=connected,
        )
        return [chunk async for chunk in stream]

    events = parse_sse(asyncio.run(collect()))

    assert [event["type"] for event in events] == expected_types
    if completed_outcome is not None:
        completed = next(event for event in events if event["type"] == "done")
        assert completed["outcome"] == completed_outcome
        assert completed["processed_duplicate"] is True
    assert usage_calls == []


def test_public_service_projects_cancellation_as_a_final_outcome(monkeypatch):
    from chat import public_service

    emitted = public_events(
        outcome=CancelledTurnOutcome(
            content="The calculation was cancelled."
        ),
        session_id="session-outcome",
        turn_id="turn-outcome",
    )

    async def fake_turn(*_args, **_kwargs):
        for event in emitted:
            yield event

    monkeypatch.setattr(public_service, "run_chat_turn", fake_turn)
    monkeypatch.setattr(public_service, "billing_enabled", lambda: False)

    async def collect():
        stream = await public_service.start_public_chat(
            ChatRequest(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="session-outcome",
                turn_id="turn-outcome",
            ),
            is_cancelled=connected,
        )
        return [chunk async for chunk in stream]

    events = parse_sse(asyncio.run(collect()))
    completed = next(event for event in events if event["type"] == "done")

    assert [event["type"] for event in events] == [
        "cancellation",
        "chunk",
        "done",
    ]
    assert completed["route"] == "cancelled"
    assert completed["processed_duplicate"] is False


def test_public_billing_retries_pending_intent_and_marks_only_recorded_result(
    monkeypatch,
):
    from chat import public_service

    results = iter(
        [
            {"cost_gbp": 0, "recorded": False},
            {"cost_gbp": 0.02, "recorded": True},
        ]
    )
    usage_calls = []
    marked = []

    monkeypatch.setattr(public_service, "billing_enabled", lambda: True)
    monkeypatch.setattr(
        public_service.billing,
        "record_usage",
        lambda **kwargs: usage_calls.append(kwargs) or next(results),
    )
    monkeypatch.setattr(
        public_service,
        "SqlAnalysisStore",
        lambda: SimpleNamespace(
            mark_billing_recorded=lambda session_id, turn_id: marked.append(
                (session_id, turn_id)
            )
        ),
    )

    result = public_service._record_turn_usage(
        user_id="user",
        session_id="session",
        turn_id="turn-stable",
        model="model-one",
        usage=ChatUsage(input_tokens=2, output_tokens=1),
    )
    public_service._mark_billing_intent_recorded(
        billing_result=result,
        session_id="session",
        turn_id="turn-stable",
    )

    assert len(usage_calls) == 2
    assert {call["turn_id"] for call in usage_calls} == {"turn-stable"}
    assert marked == [("session", "turn-stable")]


def test_public_billing_keeps_intent_pending_after_failed_retries(monkeypatch):
    from chat import public_service

    marked = []
    monkeypatch.setattr(public_service, "billing_enabled", lambda: True)
    monkeypatch.setattr(
        public_service.billing,
        "record_usage",
        lambda **_kwargs: {"cost_gbp": 0, "recorded": False},
    )
    monkeypatch.setattr(
        public_service,
        "SqlAnalysisStore",
        lambda: SimpleNamespace(
            mark_billing_recorded=lambda *args: marked.append(args)
        ),
    )

    result = public_service._record_turn_usage(
        user_id="user",
        session_id="session",
        turn_id="turn-pending",
        model="model-one",
        usage=ChatUsage(input_tokens=2, output_tokens=1),
    )
    public_service._mark_billing_intent_recorded(
        billing_result=result,
        session_id="session",
        turn_id="turn-pending",
    )

    assert result["recorded"] is False
    assert marked == []
