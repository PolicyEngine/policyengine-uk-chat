import asyncio
from types import SimpleNamespace

from chat.events import (
    TextChunk,
    ToolCompleted,
    ToolUsed,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.turn_input import ChatTurnInput
from gateway.assessment import (
    GatewayCatalogueUnavailable,
    ReformAlternative,
    ReformAssessment,
    ValidatedParameterBinding,
)
from gateway.intent import ReformIntent
from gateway.policy import GatingReason, SlotFact
from gateway.runtime import GatewayVerdict


def _event(name: str, **attrs):
    value = type(name, (), {})()
    for key, item in attrs.items():
        setattr(value, key, item)
    return value


class FakeStream:
    def __init__(self, *, chunks=None, final_content=None, stop_reason="end_turn"):
        self._events = [
            _event(
                "RawContentBlockDeltaEvent",
                delta=SimpleNamespace(type="text_delta", text=chunk),
            )
            for chunk in (chunks or [])
        ]
        self._final = SimpleNamespace(
            content=final_content or [],
            stop_reason=stop_reason,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for event in self._events:
            yield event

    async def get_final_message(self):
        return self._final


class FakeMessages:
    def __init__(self, streams):
        self.streams = list(streams)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.streams.pop(0)


class FakeClient:
    def __init__(self, streams):
        self.messages = FakeMessages(streams)


def _tool_use(name, tool_input, tool_id="tool-1"):
    return SimpleNamespace(type="tool_use", id=tool_id, name=name, input=tool_input)


async def _connected():
    return False


def test_run_chat_turn_emits_complete_tool_trace_and_completion(monkeypatch):
    import chat.orchestrator as orchestrator

    tool_input = {"year": 2026, "reform": {"income_tax": {"basic_rate": 0.19}}}
    tool_output = {
        "status": "success",
        "result_id": "result-1",
        "rows": [{"budgetary_impact": -1_234_567_890}],
    }
    client = FakeClient(
        [
            FakeStream(
                chunks=["I will calculate."],
                final_content=[_tool_use("run_society_simulation", tool_input)],
                stop_reason="tool_use",
            ),
            FakeStream(chunks=["The result is £1.23bn."], final_content=[]),
        ]
    )

    monkeypatch.setattr(orchestrator, "get_async_client", lambda: client)
    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: True)
    monkeypatch.setattr(orchestrator, "execute_tool", lambda *_args, **_kwargs: tool_output)

    async def no_suggestions(*_args, **_kwargs):
        return []

    monkeypatch.setattr(orchestrator, "generate_followup_suggestions", no_suggestions)

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[{"role": "user", "content": "Calculate it."}],
                    session_id="eval-session",
                ),
                is_cancelled=_connected,
            )
        ]

    events = asyncio.run(collect())

    assert [
        type(event)
        for event in events
        if isinstance(event, (ToolUsed, ToolCompleted, TurnCompleted))
    ] == [ToolUsed, ToolCompleted, TurnCompleted]
    completed_tool = next(event for event in events if isinstance(event, ToolCompleted))
    assert completed_tool.output is tool_output
    assert completed_tool.output["rows"][0]["budgetary_impact"] == -1_234_567_890
    done = next(event for event in events if isinstance(event, TurnCompleted))
    assert done.content == "The result is £1.23bn."
    assert done.session_id == "eval-session"
    assert any(
        isinstance(event, TextChunk) and event.content == "I will calculate."
        for event in events
    )
    assert client.messages.calls[1]["messages"][-1]["content"][0]["type"] == "tool_result"


def test_run_chat_turn_cancels_without_calling_the_model(monkeypatch):
    import chat.orchestrator as orchestrator

    client = FakeClient([])
    monkeypatch.setattr(orchestrator, "get_async_client", lambda: client)
    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: True)

    async def cancelled():
        return True

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[{"role": "user", "content": "Calculate it."}],
                    session_id="cancel-session",
                ),
                is_cancelled=cancelled,
            )
        ]

    events = asyncio.run(collect())

    assert len(events) == 1
    assert isinstance(events[0], TurnCancelled)
    assert events[0].session_id == "cancel-session"
    assert client.messages.calls == []


def test_needs_plan_is_rendered_without_async_writer_model(monkeypatch):
    import chat.orchestrator as orchestrator

    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        gating_reasons=[GatingReason("missing_output", "output")],
    )
    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: False)
    monkeypatch.setattr(orchestrator, "run_gateway", lambda _prompt: verdict)

    def no_client():
        raise AssertionError("deterministic clarification must not create a client")

    monkeypatch.setattr(orchestrator, "get_async_client", no_client)

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[{"role": "user", "content": "Model a reform."}],
                    session_id="clarify-session",
                ),
                is_cancelled=_connected,
            )
        ]

    events = asyncio.run(collect())

    assert [type(event) for event in events] == [TextChunk, TurnCompleted]
    assert events[0].content.startswith("What result")
    done = events[1]
    assert done.model is None
    assert done.route == "lightweight"
    assert done.outcome == "needs_plan"
    assert done.stop_reason == "gateway_clarification"
    assert done.gateway_trace.gating_reasons[0].code == "missing_output"


def test_cancellation_before_deterministic_clarification_emits_cancelled(monkeypatch):
    import chat.orchestrator as orchestrator

    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        gating_reasons=[GatingReason("missing_output", "output")],
    )
    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: False)
    monkeypatch.setattr(orchestrator, "run_gateway", lambda _prompt: verdict)
    probes = iter([False, True])

    async def cancelled_after_gateway():
        return next(probes)

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[{"role": "user", "content": "Model a reform."}],
                    session_id="cancel-clarification",
                ),
                is_cancelled=cancelled_after_gateway,
            )
        ]

    events = asyncio.run(collect())

    assert len(events) == 1
    assert isinstance(events[0], TurnCancelled)
    assert events[0].gateway_trace.gating_reasons[0].code == "missing_output"


def test_unrenderable_gateway_reason_fails_open_to_compute(monkeypatch):
    import chat.orchestrator as orchestrator

    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        gating_reasons=[GatingReason("internal_slot", "internal")],
    )
    client = FakeClient([FakeStream(chunks=["Computed response."], final_content=[])])
    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: False)
    monkeypatch.setattr(orchestrator, "run_gateway", lambda _prompt: verdict)
    monkeypatch.setattr(orchestrator, "get_async_client", lambda: client)

    async def no_suggestions(*_args, **_kwargs):
        return []

    monkeypatch.setattr(orchestrator, "generate_followup_suggestions", no_suggestions)

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[{"role": "user", "content": "Calculate it."}],
                    session_id="fail-open-session",
                ),
                is_cancelled=_connected,
            )
        ]

    events = asyncio.run(collect())

    done = next(event for event in events if isinstance(event, TurnCompleted))
    assert done.route == "compute"
    assert done.outcome == "ready"
    assert client.messages.calls[0]["tools"]


def test_low_confidence_clarification_contains_signed_resume_marker(
    monkeypatch,
):
    import chat.orchestrator as orchestrator

    assessment = ReformAssessment(
        reform={"path.best": 0.21},
        summary="Best proposal",
        confidence=72,
        parameter_bindings=(
            ValidatedParameterBinding("path.best", "Best label", "best"),
        ),
        alternatives=(
            ReformAlternative(
                "Other proposal",
                (
                    ValidatedParameterBinding(
                        "path.other",
                        "Other label",
                        "other",
                    ),
                ),
                {"path.other": 0.22},
            ),
        ),
        search_queries=("basic rate",),
        catalogue_version="test-version",
    )
    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        tool="run_society_simulation",
        slots=[
            SlotFact(
                "output",
                "prompt",
                kind="output",
                value="budgetary_impact",
            )
        ],
        gating_reasons=[GatingReason("confirm_reform", "reform")],
        reform_intent=ReformIntent(
            policy_phrase="basic rate",
            action="increase",
            amount="one percentage point",
            scope="unspecified",
            evidence="increasing the basic rate by one percentage point",
        ),
        reform_assessment=assessment,
    )

    monkeypatch.setenv(
        "GATEWAY_PROPOSAL_SIGNING_KEY",
        "orchestrator-test-signing-key-at-least-32-bytes",
    )
    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: False)
    monkeypatch.setattr(
        orchestrator,
        "run_gateway",
        lambda _prompt: verdict,
    )
    monkeypatch.setattr(
        orchestrator,
        "get_async_client",
        lambda: (_ for _ in ()).throw(AssertionError("writer client created")),
    )

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[
                        {
                            "role": "user",
                            "content": (
                                "What is the cost of increasing the basic rate "
                                "by one percentage point?"
                            ),
                        }
                    ],
                    session_id="proposal-session",
                ),
                is_cancelled=_connected,
            )
        ]

    events = asyncio.run(collect())

    assert [type(event) for event in events] == [TextChunk, TurnCompleted]
    assert "Best label" in events[0].content
    assert "path.best" not in events[0].content
    assert "<!--pe-proposal:v1:" in events[0].content


def test_resumed_proposal_enters_compute_without_opening_gateway(monkeypatch):
    import chat.orchestrator as orchestrator

    resumed = GatewayVerdict(outcome="ready", route="compute", proposal_resumed=True)
    client = FakeClient([FakeStream(chunks=["Computed."], final_content=[])])
    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: True)
    monkeypatch.setattr(
        orchestrator,
        "resume_gateway_proposal",
        lambda *_args, **_kwargs: resumed,
    )
    monkeypatch.setattr(
        orchestrator,
        "run_gateway",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("opening gateway ran")),
    )
    monkeypatch.setattr(orchestrator, "get_async_client", lambda: client)
    monkeypatch.setattr(
        orchestrator,
        "generate_followup_suggestions",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[
                        {"role": "user", "content": "Proposal"},
                        {"role": "assistant", "content": "Clarification"},
                        {"role": "user", "content": "Yes"},
                    ],
                    session_id="resume-session",
                ),
                is_cancelled=_connected,
            )
        ]

    events = asyncio.run(collect())

    done = next(event for event in events if isinstance(event, TurnCompleted))
    assert done.route == "compute"
    assert done.outcome == "ready"
    assert client.messages.calls[0]["tools"]


def test_catalogue_failure_terminates_without_response_model_client(monkeypatch):
    import chat.orchestrator as orchestrator

    monkeypatch.setattr(orchestrator, "is_followup", lambda _messages: False)
    monkeypatch.setattr(
        orchestrator,
        "run_gateway",
        lambda _prompt: (_ for _ in ()).throw(
            GatewayCatalogueUnavailable("catalogue offline")
        ),
    )
    monkeypatch.setattr(
        orchestrator,
        "get_async_client",
        lambda: (_ for _ in ()).throw(AssertionError("response client created")),
    )

    async def collect():
        return [
            event
            async for event in orchestrator.run_chat_turn(
                ChatTurnInput(
                    messages=[{"role": "user", "content": "Increase a tax."}],
                    session_id="catalogue-error-session",
                ),
                is_cancelled=_connected,
            )
        ]

    events = asyncio.run(collect())

    assert len(events) == 1
    assert isinstance(events[0], TurnFailed)
