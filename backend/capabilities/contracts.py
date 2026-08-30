"""Typed contracts and outcomes for conversational capabilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Generic, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tools.contracts import CallerType, Visibility
from conversation_context.models import (
    CapabilityInvocationReference,
    FactRequirement,
)


InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class ArtifactContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str
    schema_version: str

    @field_validator("artifact_type", "schema_version")
    @classmethod
    def require_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be empty")
        return value

    def is_compatible_with(self, produced: "ArtifactContract") -> bool:
        return (
            self.artifact_type == produced.artifact_type
            and self.schema_version == produced.schema_version
        )


class CapabilityDependency(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capability_id: str
    artifact: ArtifactContract | None = None


class CapabilitySpec(BaseModel):
    """Immutable registration and composition metadata for one capability."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    identifier: str
    version: str
    description: str
    required_use: str
    visibility: Visibility
    allowed_callers: frozenset[CallerType]
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    accepted_artifacts: tuple[ArtifactContract, ...] = ()
    produced_artifacts: tuple[ArtifactContract, ...] = ()
    tool_dependencies: tuple[str, ...] = ()
    dependencies: tuple[CapabilityDependency, ...] = ()

    @field_validator("identifier", "version", "description", "required_use")
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


class Completed(BaseModel, Generic[OutputT]):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["completed"] = "completed"
    value: OutputT


class NeedsInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["needs_input"] = "needs_input"
    prompt: str
    missing_fields: tuple[str, ...] = ()
    partial_input: dict[str, object] = Field(default_factory=dict)
    fact_requirements: tuple[FactRequirement, ...] = ()
    capability_invocation: CapabilityInvocationReference | None = None


class Unsupported(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["unsupported"] = "unsupported"
    reason: str


class Failed(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["failed"] = "failed"
    safe_message: str
    error_code: str


class Accepted(BaseModel):
    """Reserved for a future explicitly asynchronous capability."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["accepted"] = "accepted"
    operation_id: str


CapabilityOutcome: TypeAlias = (
    Completed[OutputT] | NeedsInput | Unsupported | Failed | Accepted
)


class Capability(ABC, Generic[InputT, OutputT]):
    """A cohesive component that coordinates typed tools and prerequisites."""

    spec: CapabilitySpec

    @abstractmethod
    async def run(
        self,
        capability_input: InputT,
        context: "CapabilityContext",
    ) -> CapabilityOutcome[OutputT]:
        """Execute the capability-owned sequence."""

    def trace_summary(self, status: str) -> str:
        return f"{self.spec.identifier} {status}"


from capabilities.context import CapabilityContext  # noqa: E402
