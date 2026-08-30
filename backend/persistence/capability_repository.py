"""SQL repositories for typed artifacts and waiting capability input."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Mapping, TypeVar, cast

from pydantic import BaseModel, ValidationError
from sqlmodel import Session, select

from capabilities.artifacts import ARTIFACT_MODELS, ArtifactBase, TransferableArtifact
from capabilities.repository import WaitingCapabilityInvocation
from conversations.models import get_engine
from persistence.rows import CapabilityArtifactRow, WaitingCapabilityInvocationRow


ArtifactT = TypeVar("ArtifactT", bound=ArtifactBase)


class InvalidPersistedRecord(ValueError):
    pass


class PartialInputDefinition(BaseModel):
    model_config = {"frozen": True, "arbitrary_types_allowed": True, "extra": "forbid"}

    schema_version: str
    model: type[BaseModel]


class PartialInputRegistry:
    def __init__(
        self,
        definitions: Mapping[str, PartialInputDefinition] | None = None,
    ) -> None:
        self._definitions = dict(definitions or {})

    def register(
        self,
        capability_id: str,
        *,
        schema_version: str,
        model: type[BaseModel],
    ) -> None:
        if capability_id in self._definitions:
            raise ValueError(f"Duplicate partial-input registration: {capability_id}")
        self._definitions[capability_id] = PartialInputDefinition(
            schema_version=schema_version,
            model=model,
        )

    def get(self, capability_id: str) -> PartialInputDefinition:
        try:
            return self._definitions[capability_id]
        except KeyError as exc:
            raise InvalidPersistedRecord(
                f"No partial-input model is registered for {capability_id}."
            ) from exc


class SQLConversationCapabilityRepository:
    def __init__(
        self,
        *,
        engine=None,
        artifact_models: Mapping[str, type[ArtifactBase]] | None = None,
        partial_inputs: PartialInputRegistry | None = None,
    ) -> None:
        self._engine = engine or get_engine()
        self._artifact_models = dict(artifact_models or ARTIFACT_MODELS)
        self._partial_inputs = partial_inputs or PartialInputRegistry()

    def save_artifact(
        self,
        conversation_id: str,
        artifact: ArtifactT,
    ) -> ArtifactT:
        expected_model = self._artifact_models.get(artifact.artifact_type)
        if expected_model is None or not isinstance(artifact, expected_model):
            raise TypeError(
                f"Artifact {artifact.artifact_type!r} is not registered with its declared type."
            )
        row = CapabilityArtifactRow(
            artifact_id=artifact.artifact_id,
            conversation_id=conversation_id,
            artifact_type=artifact.artifact_type,
            schema_version=artifact.schema_version,
            payload_json=artifact.model_dump_json(),
            created_at=artifact.created_at,
        )
        with Session(self._engine) as session:
            if session.get(CapabilityArtifactRow, artifact.artifact_id) is not None:
                raise ValueError(f"Artifact already exists: {artifact.artifact_id}")
            session.add(row)
            session.commit()
        return artifact

    def get_artifact(self, conversation_id: str, artifact_id: str) -> ArtifactBase:
        with Session(self._engine) as session:
            row = session.get(CapabilityArtifactRow, artifact_id)
        if row is None or row.conversation_id != conversation_id:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        return self._decode_artifact(row)

    def find_artifacts(
        self,
        conversation_id: str,
        artifact_model: type[ArtifactT],
    ) -> tuple[ArtifactT, ...]:
        artifact_type = artifact_model.model_fields["artifact_type"].default
        with Session(self._engine) as session:
            rows = session.exec(
                select(CapabilityArtifactRow)
                .where(CapabilityArtifactRow.conversation_id == conversation_id)
                .where(CapabilityArtifactRow.artifact_type == artifact_type)
                .order_by(CapabilityArtifactRow.created_at)
            ).all()
        artifacts = tuple(self._decode_artifact(row) for row in rows)
        if not all(isinstance(artifact, artifact_model) for artifact in artifacts):
            raise InvalidPersistedRecord(
                f"Stored {artifact_type} artifact decoded to an incompatible model."
            )
        return cast(tuple[ArtifactT, ...], artifacts)

    def list_artifacts(self, conversation_id: str) -> tuple[ArtifactBase, ...]:
        with Session(self._engine) as session:
            rows = session.exec(
                select(CapabilityArtifactRow)
                .where(CapabilityArtifactRow.conversation_id == conversation_id)
                .order_by(CapabilityArtifactRow.created_at)
            ).all()
        return tuple(self._decode_artifact(row) for row in rows)

    def create_waiting(
        self,
        invocation: WaitingCapabilityInvocation,
    ) -> WaitingCapabilityInvocation:
        validated = self._validate_partial(
            invocation.capability_id,
            invocation.input_schema_version,
            invocation.partial_input,
        )
        stored = invocation.model_copy(
            update={
                "partial_input": validated,
                **self._waiting_metadata(validated),
            }
        )
        row = self._waiting_row(stored)
        with Session(self._engine) as session:
            if session.get(WaitingCapabilityInvocationRow, row.invocation_id) is not None:
                raise ValueError(f"Waiting invocation already exists: {row.invocation_id}")
            session.add(row)
            session.commit()
        return stored

    def get_waiting(self, invocation_id: str) -> WaitingCapabilityInvocation:
        with Session(self._engine) as session:
            row = session.get(WaitingCapabilityInvocationRow, invocation_id)
        if row is None:
            raise KeyError(f"Unknown waiting invocation: {invocation_id}")
        return self._decode_waiting(row)

    def list_waiting(
        self,
        conversation_id: str,
        *,
        capability_id: str | None = None,
    ) -> tuple[WaitingCapabilityInvocation, ...]:
        statement = select(WaitingCapabilityInvocationRow).where(
            WaitingCapabilityInvocationRow.conversation_id == conversation_id
        )
        if capability_id is not None:
            statement = statement.where(
                WaitingCapabilityInvocationRow.capability_id == capability_id
            )
        statement = statement.order_by(WaitingCapabilityInvocationRow.created_at)
        with Session(self._engine) as session:
            rows = session.exec(statement).all()
        return tuple(self._decode_waiting(row) for row in rows)

    def update_waiting(
        self,
        invocation_id: str,
        partial_input: BaseModel,
    ) -> WaitingCapabilityInvocation:
        current = self.get_waiting(invocation_id)
        validated = self._validate_partial(
            current.capability_id,
            current.input_schema_version,
            partial_input,
        )
        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            row = session.get(WaitingCapabilityInvocationRow, invocation_id)
            if row is None:
                raise KeyError(f"Unknown waiting invocation: {invocation_id}")
            row.partial_input_json = validated.model_dump_json()
            row.updated_at = now
            session.add(row)
            session.commit()
        return current.model_copy(
            update={
                "partial_input": validated,
                "updated_at": now,
                **self._waiting_metadata(validated),
            }
        )

    def resume_waiting(
        self,
        invocation_id: str,
        updates: dict[str, object],
    ) -> WaitingCapabilityInvocation:
        current = self.get_waiting(invocation_id)
        definition = self._partial_inputs.get(current.capability_id)
        merged = {**current.partial_input.model_dump(), **updates}
        try:
            resumed = definition.model.model_validate(merged)
        except ValidationError as exc:
            raise TypeError(
                f"Invalid resumed input for capability {current.capability_id}."
            ) from exc
        return self.update_waiting(invocation_id, resumed)

    def branch_waiting(
        self,
        invocation_id: str,
        new_invocation_id: str,
        source_turn_id: str,
    ) -> WaitingCapabilityInvocation:
        current = self.get_waiting(invocation_id)
        branched = current.model_copy(
            update={
                "invocation_id": new_invocation_id,
                "source_turn_id": source_turn_id,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return self.create_waiting(branched)

    def remove_waiting(self, invocation_id: str) -> None:
        with Session(self._engine) as session:
            row = session.get(WaitingCapabilityInvocationRow, invocation_id)
            if row is not None:
                session.delete(row)
                session.commit()

    def _decode_artifact(self, row: CapabilityArtifactRow) -> ArtifactBase:
        model = self._artifact_models.get(row.artifact_type)
        if model is None:
            raise InvalidPersistedRecord(
                f"Unknown persisted artifact type: {row.artifact_type}."
            )
        try:
            artifact = model.model_validate_json(row.payload_json)
        except ValidationError as exc:
            raise InvalidPersistedRecord(
                f"Invalid persisted artifact {row.artifact_id}."
            ) from exc
        if artifact.schema_version != row.schema_version:
            raise InvalidPersistedRecord(
                f"Artifact envelope version mismatch for {row.artifact_id}."
            )
        return artifact

    def _validate_partial(
        self,
        capability_id: str,
        schema_version: str,
        value: BaseModel,
    ) -> BaseModel:
        definition = self._partial_inputs.get(capability_id)
        if definition.schema_version != schema_version:
            raise InvalidPersistedRecord(
                f"Partial-input version mismatch for {capability_id}."
            )
        try:
            return definition.model.model_validate(value.model_dump())
        except ValidationError as exc:
            raise TypeError(
                f"Invalid partial input for capability {capability_id}."
            ) from exc

    def _decode_waiting(
        self,
        row: WaitingCapabilityInvocationRow,
    ) -> WaitingCapabilityInvocation:
        definition = self._partial_inputs.get(row.capability_id)
        if definition.schema_version != row.input_schema_version:
            raise InvalidPersistedRecord(
                f"Partial-input envelope version mismatch for {row.invocation_id}."
            )
        try:
            partial_input = definition.model.model_validate_json(row.partial_input_json)
        except ValidationError as exc:
            raise InvalidPersistedRecord(
                f"Invalid persisted waiting invocation {row.invocation_id}."
            ) from exc
        return WaitingCapabilityInvocation(
            invocation_id=row.invocation_id,
            conversation_id=row.conversation_id,
            capability_id=row.capability_id,
            capability_version=row.capability_version,
            input_schema_version=row.input_schema_version,
            partial_input=partial_input,
            source_turn_id=row.source_turn_id,
            **self._waiting_metadata(partial_input),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @staticmethod
    def _waiting_metadata(partial_input: BaseModel) -> dict[str, object]:
        return {
            "context_scope_id": getattr(partial_input, "context_scope_id", None),
            "context_revision": getattr(partial_input, "context_revision", None),
            "requirements": tuple(
                getattr(partial_input, "fact_requirements", ())
            ),
        }

    @staticmethod
    def _waiting_row(
        invocation: WaitingCapabilityInvocation,
    ) -> WaitingCapabilityInvocationRow:
        return WaitingCapabilityInvocationRow(
            invocation_id=invocation.invocation_id,
            conversation_id=invocation.conversation_id,
            capability_id=invocation.capability_id,
            capability_version=invocation.capability_version,
            input_schema_version=invocation.input_schema_version,
            partial_input_json=invocation.partial_input.model_dump_json(),
            source_turn_id=invocation.source_turn_id,
            created_at=invocation.created_at,
            updated_at=invocation.updated_at,
        )


class RepositoryArtifactAccess:
    """Async request-context view over the synchronous SQL repository."""

    def __init__(self, repository: SQLConversationCapabilityRepository) -> None:
        self._repository = repository

    async def find_artifacts(
        self,
        *,
        conversation_id: str,
        artifact_model: type[ArtifactT],
    ) -> tuple[ArtifactT, ...]:
        return await asyncio.to_thread(
            self._repository.find_artifacts,
            conversation_id,
            artifact_model,
        )

    async def save_artifact(
        self,
        *,
        conversation_id: str,
        artifact: ArtifactT,
    ) -> ArtifactT:
        return await asyncio.to_thread(
            self._repository.save_artifact,
            conversation_id,
            artifact,
        )

    async def save_waiting(self, invocation: object) -> object:
        if not isinstance(invocation, WaitingCapabilityInvocation):
            raise TypeError("Waiting state must use WaitingCapabilityInvocation.")
        return await asyncio.to_thread(
            self._repository.create_waiting,
            invocation,
        )

    async def list_waiting(
        self,
        *,
        conversation_id: str,
        capability_id: str,
    ) -> tuple[WaitingCapabilityInvocation, ...]:
        return await asyncio.to_thread(
            self._repository.list_waiting,
            conversation_id,
            capability_id=capability_id,
        )

    async def update_waiting(
        self,
        *,
        invocation_id: str,
        partial_input: BaseModel,
    ) -> WaitingCapabilityInvocation:
        return await asyncio.to_thread(
            self._repository.update_waiting,
            invocation_id,
            partial_input,
        )

    async def remove_waiting(self, *, invocation_id: str) -> None:
        await asyncio.to_thread(
            self._repository.remove_waiting,
            invocation_id,
        )
