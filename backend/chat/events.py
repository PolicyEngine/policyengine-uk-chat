"""Framework-independent events emitted by a UK Chat turn."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypeAlias

from analysis.models import BillingIntent

CancellationProbe: TypeAlias = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ChatUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
        }

    def plus(self, values: dict[str, int] | "ChatUsage") -> "ChatUsage":
        added = values.as_dict() if isinstance(values, ChatUsage) else values
        return ChatUsage(
            input_tokens=self.input_tokens + added.get("input_tokens", 0),
            output_tokens=self.output_tokens + added.get("output_tokens", 0),
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens
                + added.get("cache_creation_input_tokens", 0)
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens
                + added.get("cache_read_input_tokens", 0)
            ),
        )


@dataclass(frozen=True, slots=True)
class ToolStarted:
    tool_name: str
    tool_id: str
    type: Literal["tool_start"] = field(default="tool_start", init=False)


@dataclass(frozen=True, slots=True)
class TextChunk:
    content: str
    type: Literal["chunk"] = field(default="chunk", init=False)


@dataclass(frozen=True, slots=True)
class ToolUsed:
    tool_name: str
    tool_id: str
    tool_input: dict[str, Any]
    type: Literal["tool_use"] = field(default="tool_use", init=False)


@dataclass(frozen=True, slots=True)
class ThinkingCompleted:
    type: Literal["thinking_done"] = field(default="thinking_done", init=False)


@dataclass(frozen=True, slots=True)
class ToolCompleted:
    tool_name: str
    tool_id: str
    status: Literal["success", "error"]
    output: Any
    type: Literal["tool_result"] = field(default="tool_result", init=False)


@dataclass(frozen=True, slots=True)
class TurnCompleted:
    content: str
    session_id: str
    model: str | None
    route: str
    outcome: str | None
    stop_reason: str | None
    usage: ChatUsage
    turn_id: str | None = None
    processed_duplicate: bool = False
    analysis_trace: Any | None = None
    usage_entries: tuple[Any, ...] = ()
    response_artifacts: tuple[Any, ...] = ()
    billing_intent: BillingIntent | None = None
    type: Literal["done"] = field(default="done", init=False)


@dataclass(frozen=True, slots=True)
class SuggestionsGenerated:
    suggestions: list[str]
    type: Literal["suggestions"] = field(default="suggestions", init=False)


@dataclass(frozen=True, slots=True)
class TurnFailed:
    content: str
    session_id: str
    stop_reason: str
    usage: ChatUsage
    turn_id: str | None = None
    model: str | None = None
    billable: bool = False
    analysis_trace: Any | None = None
    usage_entries: tuple[Any, ...] = ()
    billing_intent: BillingIntent | None = None
    type: Literal["error"] = field(default="error", init=False)


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    session_id: str
    model: str | None
    route: str
    usage: ChatUsage
    turn_id: str | None = None
    analysis_trace: Any | None = None
    type: Literal["cancelled"] = field(default="cancelled", init=False)


@dataclass(frozen=True, slots=True)
class ClarificationRequired:
    question_id: str
    reason_code: str
    type: Literal["clarification"] = field(default="clarification", init=False)


@dataclass(frozen=True, slots=True)
class TurnConflict:
    content: str
    session_id: str
    turn_id: str
    retryable: bool
    type: Literal["conflict"] = field(default="conflict", init=False)


@dataclass(frozen=True, slots=True)
class DuplicateProcessed:
    session_id: str
    turn_id: str
    status: str
    type: Literal["processed_duplicate"] = field(
        default="processed_duplicate", init=False
    )


@dataclass(frozen=True, slots=True)
class CancellationAccepted:
    session_id: str
    turn_id: str
    request_revision_id: str | None
    type: Literal["cancellation"] = field(default="cancellation", init=False)


ChatEvent: TypeAlias = (
    ToolStarted
    | TextChunk
    | ToolUsed
    | ThinkingCompleted
    | ToolCompleted
    | TurnCompleted
    | SuggestionsGenerated
    | TurnFailed
    | TurnCancelled
    | ClarificationRequired
    | TurnConflict
    | DuplicateProcessed
    | CancellationAccepted
)
