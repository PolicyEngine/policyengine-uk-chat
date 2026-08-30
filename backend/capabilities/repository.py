"""Repository contracts for cross-turn capability data."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from capabilities.artifacts import ArtifactBase, TransferableArtifact
from capabilities.tracing import InvocationRecord
from conversation_context.models import (
    CapabilityInvocationReference,
    FactRequirement,
)


class WaitingCapabilityInvocation(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    invocation_id: str
    conversation_id: str
    capability_id: str
    capability_version: str
    input_schema_version: str
    partial_input: BaseModel
    source_turn_id: str
    context_scope_id: str | None = None
    context_revision: int | None = None
    requirements: tuple[FactRequirement, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def reference(self) -> CapabilityInvocationReference | None:
        if self.context_scope_id is None or self.context_revision is None:
            return None
        return CapabilityInvocationReference(
            invocation_id=self.invocation_id,
            capability_id=self.capability_id,
            capability_version=self.capability_version,
            context_scope_id=self.context_scope_id,
            context_revision=self.context_revision,
        )


class ConversationCapabilityRepository(Protocol):
    def save_artifact(
        self,
        conversation_id: str,
        artifact: TransferableArtifact,
    ) -> ArtifactBase: ...

    def get_artifact(self, conversation_id: str, artifact_id: str) -> ArtifactBase: ...

    def find_artifacts(
        self,
        conversation_id: str,
        artifact_model: type[ArtifactBase],
    ) -> tuple[ArtifactBase, ...]: ...

    def create_waiting(
        self, invocation: WaitingCapabilityInvocation
    ) -> WaitingCapabilityInvocation: ...

    def get_waiting(self, invocation_id: str) -> WaitingCapabilityInvocation: ...

    def list_waiting(
        self,
        conversation_id: str,
        *,
        capability_id: str | None = None,
    ) -> tuple[WaitingCapabilityInvocation, ...]: ...

    def update_waiting(
        self,
        invocation_id: str,
        partial_input: BaseModel,
    ) -> WaitingCapabilityInvocation: ...

    def resume_waiting(
        self,
        invocation_id: str,
        updates: dict[str, object],
    ) -> WaitingCapabilityInvocation: ...

    def branch_waiting(
        self,
        invocation_id: str,
        new_invocation_id: str,
        source_turn_id: str,
    ) -> WaitingCapabilityInvocation: ...

    def remove_waiting(self, invocation_id: str) -> None: ...


class InvocationTraceRepository(Protocol):
    def save(self, record: InvocationRecord) -> None: ...

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        include_private: bool,
    ) -> tuple[InvocationRecord, ...]: ...
