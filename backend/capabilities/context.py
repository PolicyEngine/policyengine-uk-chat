"""Request-scoped context supplied to capability implementations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from threading import Lock
from typing import TYPE_CHECKING, Protocol, TypeVar

from pydantic import BaseModel

from capabilities.artifacts import ArtifactBase
from tools.contracts import CallerType
from tools.context import TurnResultStore


if TYPE_CHECKING:
    from capabilities.contracts import CapabilityOutcome
    from capabilities.executor import InvocationExecutor
    from capabilities.repository import WaitingCapabilityInvocation
    from conversation_context.models import ConversationContext


ArtifactT = TypeVar("ArtifactT", bound=ArtifactBase)
CancellationProbe = Callable[[], Awaitable[bool]]


@dataclass(frozen=True, slots=True)
class ModelUsageSnapshot:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    def since(self, earlier: "ModelUsageSnapshot") -> "ModelUsageSnapshot":
        return ModelUsageSnapshot(
            input_tokens=self.input_tokens - earlier.input_tokens,
            output_tokens=self.output_tokens - earlier.output_tokens,
            cache_creation_input_tokens=(
                self.cache_creation_input_tokens
                - earlier.cache_creation_input_tokens
            ),
            cache_read_input_tokens=(
                self.cache_read_input_tokens - earlier.cache_read_input_tokens
            ),
        )


@dataclass(slots=True)
class ModelUsageLedger:
    _input_tokens: int = 0
    _output_tokens: int = 0
    _cache_creation_input_tokens: int = 0
    _cache_read_input_tokens: int = 0
    _lock: Lock = field(default_factory=Lock)

    def record(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        with self._lock:
            self._input_tokens += input_tokens
            self._output_tokens += output_tokens
            self._cache_creation_input_tokens += cache_creation_input_tokens
            self._cache_read_input_tokens += cache_read_input_tokens

    def snapshot(self) -> ModelUsageSnapshot:
        with self._lock:
            return ModelUsageSnapshot(
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                cache_creation_input_tokens=self._cache_creation_input_tokens,
                cache_read_input_tokens=self._cache_read_input_tokens,
            )


class ArtifactAccess(Protocol):
    async def find_artifacts(
        self,
        *,
        conversation_id: str,
        artifact_model: type[ArtifactT],
    ) -> tuple[ArtifactT, ...]: ...

    async def save_artifact(
        self,
        *,
        conversation_id: str,
        artifact: ArtifactT,
    ) -> ArtifactT: ...

    async def save_waiting(self, invocation: object) -> object: ...

    async def list_waiting(
        self,
        *,
        conversation_id: str,
        capability_id: str,
    ) -> tuple["WaitingCapabilityInvocation", ...]: ...

    async def update_waiting(
        self,
        *,
        invocation_id: str,
        partial_input: BaseModel,
    ) -> "WaitingCapabilityInvocation": ...

    async def remove_waiting(self, *, invocation_id: str) -> None: ...


class EmptyArtifactAccess:
    async def find_artifacts(
        self,
        *,
        conversation_id: str,
        artifact_model: type[ArtifactT],
    ) -> tuple[ArtifactT, ...]:
        del conversation_id, artifact_model
        return ()

    async def save_artifact(
        self,
        *,
        conversation_id: str,
        artifact: ArtifactT,
    ) -> ArtifactT:
        del conversation_id
        return artifact

    async def save_waiting(self, invocation: object) -> object:
        return invocation

    async def list_waiting(
        self,
        *,
        conversation_id: str,
        capability_id: str,
    ) -> tuple["WaitingCapabilityInvocation", ...]:
        del conversation_id, capability_id
        return ()

    async def update_waiting(
        self,
        *,
        invocation_id: str,
        partial_input: BaseModel,
    ) -> "WaitingCapabilityInvocation":
        del invocation_id, partial_input
        raise KeyError("No waiting capability invocation is available.")

    async def remove_waiting(self, *, invocation_id: str) -> None:
        del invocation_id


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    request_id: str
    conversation_id: str
    turn_id: str
    is_cancelled: CancellationProbe
    artifacts: ArtifactAccess
    result_store: TurnResultStore
    model_usage: ModelUsageLedger
    _executor: "InvocationExecutor"
    current_user_message: str = ""
    conversation_context: "ConversationContext | None" = None
    _capability_id: str | None = None
    _capability_invocation_id: str | None = None
    _capability_version: str | None = None
    _tool_id: str | None = None

    def for_capability(
        self,
        capability_id: str,
        invocation_id: str | None = None,
        capability_version: str | None = None,
    ) -> "CapabilityContext":
        return replace(
            self,
            _capability_id=capability_id,
            _capability_invocation_id=invocation_id,
            _capability_version=capability_version,
            _tool_id=None,
        )

    def for_tool(self, tool_id: str) -> "CapabilityContext":
        return replace(self, _tool_id=tool_id)

    def with_current_user_message(self, message: str) -> "CapabilityContext":
        """Bind the exact current user text as request-scoped evidence."""

        return replace(self, current_user_message=message)

    def with_conversation_context(
        self,
        conversation_context: "ConversationContext",
    ) -> "CapabilityContext":
        """Bind the validated typed context revision for this turn."""

        return replace(self, conversation_context=conversation_context)

    async def cancelled(self) -> bool:
        return await self.is_cancelled()

    async def find_artifacts(
        self,
        artifact_model: type[ArtifactT],
    ) -> tuple[ArtifactT, ...]:
        return await self.artifacts.find_artifacts(
            conversation_id=self.conversation_id,
            artifact_model=artifact_model,
        )

    async def save_artifact(self, artifact: ArtifactT) -> ArtifactT:
        saved = await self.artifacts.save_artifact(
            conversation_id=self.conversation_id,
            artifact=artifact,
        )
        if not isinstance(saved, type(artifact)):
            raise TypeError("Artifact repository returned an incompatible model.")
        return saved

    @property
    def capability_invocation_id(self) -> str:
        if self._capability_invocation_id is None:
            raise RuntimeError("Capability invocation identity is unavailable.")
        return self._capability_invocation_id

    async def persist_waiting(
        self,
        partial_input: BaseModel,
        *,
        input_schema_version: str = "1",
    ) -> object:
        if self._capability_id is None or self._capability_version is None:
            raise RuntimeError("Capability identity is unavailable for waiting input.")
        from capabilities.repository import WaitingCapabilityInvocation

        invocation = WaitingCapabilityInvocation(
            invocation_id=self.capability_invocation_id,
            conversation_id=self.conversation_id,
            capability_id=self._capability_id,
            capability_version=self._capability_version,
            input_schema_version=input_schema_version,
            partial_input=partial_input,
            source_turn_id=self.turn_id,
            context_scope_id=getattr(partial_input, "context_scope_id", None),
            context_revision=getattr(partial_input, "context_revision", None),
            requirements=tuple(
                getattr(partial_input, "fact_requirements", ())
            ),
        )
        return await self.artifacts.save_waiting(invocation)

    async def waiting_invocations(
        self,
        capability_id: str,
    ) -> tuple["WaitingCapabilityInvocation", ...]:
        return await self.artifacts.list_waiting(
            conversation_id=self.conversation_id,
            capability_id=capability_id,
        )

    async def update_waiting(
        self,
        invocation_id: str,
        partial_input: BaseModel,
    ) -> "WaitingCapabilityInvocation":
        return await self.artifacts.update_waiting(
            invocation_id=invocation_id,
            partial_input=partial_input,
        )

    async def remove_waiting(self, invocation_id: str) -> None:
        await self.artifacts.remove_waiting(invocation_id=invocation_id)

    async def invoke_tool(self, identifier: str, tool_input: object) -> BaseModel:
        if self._tool_id is not None:
            caller = CallerType.TOOL
        elif self._capability_id is not None:
            caller = CallerType.CAPABILITY
        else:
            raise RuntimeError("Nested tool invocation requires a caller identity.")
        return await self._executor.invoke_tool(
            identifier,
            tool_input,
            caller=caller,
            context=self,
        )

    async def invoke_capability(
        self,
        identifier: str,
        capability_input: object,
    ) -> "CapabilityOutcome[BaseModel]":
        if self._capability_id is None:
            raise RuntimeError("Nested capability invocation requires a capability identity.")
        return await self._executor.invoke_capability(
            identifier,
            capability_input,
            caller=CallerType.CAPABILITY,
            context=self,
        )

    def record_model_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
    ) -> None:
        self.model_usage.record(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
        )
