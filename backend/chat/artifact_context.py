"""Sanitized model-context summaries of compatible typed artifacts."""

from __future__ import annotations

import asyncio

from capabilities.artifacts import (
    ArtifactBase,
    ChartArtifactRef,
    HouseholdRef,
    HouseholdResultRef,
    PolicyScenarioRef,
    SocietyAnalysisResultRef,
)
from persistence.capability_repository import SQLConversationCapabilityRepository


def sanitized_artifact_summary(artifact: ArtifactBase) -> dict[str, object]:
    base: dict[str, object] = {
        "artifact_id": artifact.artifact_id,
        "artifact_type": getattr(artifact, "artifact_type"),
        "schema_version": artifact.schema_version,
    }
    if isinstance(artifact, PolicyScenarioRef):
        return {
            **base,
            "year": artifact.year,
            "scenario_revision": artifact.scenario_revision,
            "baseline": artifact.baseline,
            "change_count": len(artifact.verified_changes),
        }
    if isinstance(artifact, HouseholdRef):
        return {
            **base,
            "year": artifact.year,
            "household_revision": artifact.household_revision,
            "context_scope_id": artifact.context_scope_id,
            "context_revision": artifact.context_revision,
            "validated_inputs": tuple(
                {"label": value.label, "value": value.value, "source": value.source}
                for value in artifact.values
            ),
        }
    if isinstance(artifact, HouseholdResultRef):
        return {
            **base,
            "year": artifact.year,
            "scenario_revision": artifact.scenario_revision,
            "context_scope_id": artifact.context_scope_id,
            "context_revision": artifact.context_revision,
            "outputs": tuple(output.model_dump(mode="json") for output in artifact.outputs),
        }
    if isinstance(artifact, SocietyAnalysisResultRef):
        return {
            **base,
            "year": artifact.year,
            "scenario_revision": artifact.scenario_revision,
            "default_profile_version": artifact.default_profile_version,
            "outputs": tuple(output.model_dump(mode="json") for output in artifact.outputs),
            "requested_output_issues": tuple(
                issue.model_dump(mode="json")
                for issue in artifact.requested_output_issues
            ),
        }
    if isinstance(artifact, ChartArtifactRef):
        return {
            **base,
            "year": artifact.year,
            "scenario_revision": artifact.scenario_revision,
            "chart_type": artifact.presentation.chart_type,
            "title": artifact.presentation.title,
            "source_result_artifact_id": artifact.source_result_artifact_id,
        }
    raise TypeError(f"Unsupported artifact model: {type(artifact).__name__}")


class RepositoryArtifactSummarySource:
    def __init__(self, repository: SQLConversationCapabilityRepository) -> None:
        self._repository = repository

    async def __call__(
        self,
        conversation_id: str,
    ) -> tuple[dict[str, object], ...]:
        artifacts = await asyncio.to_thread(
            self._repository.list_artifacts,
            conversation_id,
        )
        return tuple(
            sanitized_artifact_summary(artifact)
            for artifact in artifacts
            if artifact.schema_version == "1"
        )
