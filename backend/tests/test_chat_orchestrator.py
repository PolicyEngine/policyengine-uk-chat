import asyncio
from types import SimpleNamespace

from chat.events import TextChunk, ToolCompleted, ToolUsed, TurnCancelled, TurnCompleted
from chat.turn_input import ChatTurnInput


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
