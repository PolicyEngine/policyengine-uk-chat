"""Structured evaluation adapter over the capability chat runtime."""

from dataclasses import replace
from typing import Any

from pydantic import TypeAdapter

from chat.events import (
    CancellationProbe,
    InvocationActivity,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.capability_runtime import run_capability_chat_turn
from chat.schemas import ChatRequest
from chat.turn_input import prepare_turn_input
from eval.schemas import EvalChatResponse, EvalInvocationTrace, EvalUsage


_ANY_ADAPTER = TypeAdapter(Any)


def _usage(value) -> EvalUsage:
    return EvalUsage(**value.as_dict())


def _json_safe(value: Any) -> Any:
    return _ANY_ADAPTER.dump_python(value, mode="json")


async def run_eval_chat(
    chat_request: ChatRequest,
    *,
    is_cancelled: CancellationProbe,
) -> EvalChatResponse:
    """Run one deployed chat turn and retain its complete structured trace."""

    turn = replace(prepare_turn_input(chat_request), debug=True)
    trace: list[EvalInvocationTrace] = []
    trace_indexes: dict[str, int] = {}
    stream = run_capability_chat_turn(turn, is_cancelled=is_cancelled)

    try:
        async for event in stream:
            if isinstance(event, InvocationActivity):
                record = event.record
                index = trace_indexes.get(record.invocation_id)
                input_value = _json_safe(record.debug_input)
                completed = EvalInvocationTrace(
                    invocation_id=record.invocation_id,
                    kind=record.kind.value,
                    name=record.identifier,
                    input=input_value if isinstance(input_value, dict) else {},
                    status=record.status.value,
                    output=_json_safe(record.debug_output),
                )
                if index is None:
                    trace_indexes[record.invocation_id] = len(trace)
                    trace.append(completed)
                else:
                    trace[index] = completed
            elif isinstance(event, TurnCompleted):
                return EvalChatResponse(
                    status="completed",
                    content=event.content,
                    session_id=event.session_id,
                    model=event.model,
                    route=event.route,
                    outcome=event.outcome,
                    stop_reason=event.stop_reason,
                    usage=_usage(event.usage),
                    invocation_trace=trace,
                )
            elif isinstance(event, TurnFailed):
                return EvalChatResponse(
                    status="failed",
                    content=event.content,
                    session_id=event.session_id,
                    stop_reason=event.stop_reason,
                    usage=_usage(event.usage),
                    invocation_trace=trace,
                )
            elif isinstance(event, TurnCancelled):
                return EvalChatResponse(
                    status="failed",
                    session_id=event.session_id,
                    model=event.model,
                    route=event.route,
                    stop_reason="client_disconnected",
                    usage=_usage(event.usage),
                    invocation_trace=trace,
                )
    finally:
        await stream.aclose()

    return EvalChatResponse(
        status="failed",
        content="Chat turn ended without a terminal event.",
        session_id=turn.session_id,
        stop_reason="missing_terminal_event",
        invocation_trace=trace,
    )
