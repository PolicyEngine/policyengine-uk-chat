"""Framework-independent events emitted by a UK Chat turn."""

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, TypeAlias

from gateway.trace import GatewayTrace


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
    gateway_trace: GatewayTrace | None = None
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
    billable: bool = False
    gateway_trace: GatewayTrace | None = None
    type: Literal["error"] = field(default="error", init=False)


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    session_id: str
    model: str | None
    route: str
    usage: ChatUsage
    gateway_trace: GatewayTrace | None = None
    type: Literal["cancelled"] = field(default="cancelled", init=False)


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
)
