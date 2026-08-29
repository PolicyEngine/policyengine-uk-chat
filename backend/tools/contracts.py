"""Typed contracts shared by capability-runtime tools."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

from tools.context import TurnResultStore


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class ToolCallContext(Protocol):
    @property
    def turn_id(self) -> str: ...

    @property
    def result_store(self) -> TurnResultStore: ...

    async def invoke_tool(self, identifier: str, tool_input: object) -> BaseModel: ...

    def record_model_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None: ...


class Visibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class CallerType(str, Enum):
    MODEL = "model"
    CAPABILITY = "capability"
    TOOL = "tool"
    RUNTIME = "runtime"


class ToolSpec(BaseModel):
    """Immutable registration metadata for one typed tool."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    identifier: str
    version: str
    description: str
    visibility: Visibility
    allowed_callers: frozenset[CallerType]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    tool_dependencies: tuple[str, ...] = ()

    @field_validator("identifier", "version", "description")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    @field_validator("allowed_callers")
    @classmethod
    def require_allowed_caller(
        cls, value: frozenset[CallerType]
    ) -> frozenset[CallerType]:
        if not value:
            raise ValueError("at least one allowed caller is required")
        return value


class Tool(ABC, Generic[InputT, OutputT]):
    """One scoped operation with typed input and output."""

    spec: ToolSpec

    @abstractmethod
    async def run(
        self,
        tool_input: InputT,
        context: ToolCallContext,
    ) -> OutputT:
        """Execute the operation after the executor validates its input."""

    def trace_summary(self, status: str) -> str:
        """Return metadata-only text that is safe to retain in traces."""

        return f"{self.spec.identifier} {status}"
