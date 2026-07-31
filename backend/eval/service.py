"""Structured evaluation adapter over the shared UK Chat turn engine."""

from typing import Any

from pydantic import TypeAdapter

from chat.events import (
    CancellationProbe,
    ToolCompleted,
    ToolUsed,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.orchestrator import run_chat_turn
from chat.schemas import ChatRequest
from chat.turn_input import prepare_turn_input
from eval.schemas import EvalChatResponse, EvalToolTrace, EvalUsage


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

    turn = prepare_turn_input(chat_request)
    trace: list[EvalToolTrace] = []
    trace_indexes: dict[str, int] = {}
    stream = run_chat_turn(turn, is_cancelled=is_cancelled)

    try:
        async for event in stream:
            if isinstance(event, ToolUsed):
                trace_indexes[event.tool_id] = len(trace)
                trace.append(
                    EvalToolTrace(
                        tool_id=event.tool_id,
                        name=event.tool_name,
                        input=_json_safe(event.tool_input),
                    )
                )
            elif isinstance(event, ToolCompleted):
                index = trace_indexes.get(event.tool_id)
                completed = EvalToolTrace(
                    tool_id=event.tool_id,
                    name=event.tool_name,
                    input=trace[index].input if index is not None else {},
                    status=event.status,
                    output=_json_safe(event.output),
                )
                if index is None:
                    trace_indexes[event.tool_id] = len(trace)
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
                    tool_trace=trace,
                )
            elif isinstance(event, TurnFailed):
                return EvalChatResponse(
                    status="failed",
                    content=event.content,
                    session_id=event.session_id,
                    stop_reason=event.stop_reason,
                    usage=_usage(event.usage),
                    tool_trace=trace,
                )
            elif isinstance(event, TurnCancelled):
                return EvalChatResponse(
                    status="failed",
                    session_id=event.session_id,
                    model=event.model,
                    route=event.route,
                    stop_reason="client_disconnected",
                    usage=_usage(event.usage),
                    tool_trace=trace,
                )
    finally:
        await stream.aclose()

    return EvalChatResponse(
        status="failed",
        content="Chat turn ended without a terminal event.",
        session_id=turn.session_id,
        stop_reason="missing_terminal_event",
        tool_trace=trace,
    )
