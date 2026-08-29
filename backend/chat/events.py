"""Framework-independent events emitted by a UK Chat turn."""

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Literal, TypeAlias

from capabilities.tracing import InvocationRecord


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
class TextChunk:
    content: str
    type: Literal["chunk"] = field(default="chunk", init=False)


@dataclass(frozen=True, slots=True)
class InvocationActivity:
    """One sanitized invocation state change with optional structured debug values."""

    phase: Literal["started", "finished"]
    record: InvocationRecord
    type: Literal["invocation_activity"] = field(
        default="invocation_activity",
        init=False,
    )


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
    turn_id: str | None = None
    type: Literal["error"] = field(default="error", init=False)


@dataclass(frozen=True, slots=True)
class TurnCancelled:
    session_id: str
    model: str | None
    route: str
    usage: ChatUsage
    turn_id: str | None = None
    type: Literal["cancelled"] = field(default="cancelled", init=False)


ChatEvent: TypeAlias = (
    TextChunk
    | InvocationActivity
    | TurnCompleted
    | SuggestionsGenerated
    | TurnFailed
    | TurnCancelled
)
