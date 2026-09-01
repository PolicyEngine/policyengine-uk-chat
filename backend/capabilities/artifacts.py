"""Immutable typed values that capabilities may transfer across chat turns."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, TypeAlias
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


Scalar: TypeAlias = str | int | float | bool | None


def _artifact_id() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ImmutableModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ArtifactProvenance(ImmutableModel):
    conversation_id: str
    turn_id: str
    capability_id: str
    capability_version: str
    invocation_id: str
    sources: tuple[str, ...] = ()


class PolicyChange(ImmutableModel):
    parameter_path: str
    value: Scalar
    effective_date: str | None = None


class HouseholdValue(ImmutableModel):
    field_id: str
    label: str
    value: Scalar
    source: Literal["user", "artifact", "default"]
    subject_entity_id: str | None = None
    engine_variable: str | None = None
    period: Literal["annual", "monthly", "weekly", "four_weekly"] | None = None


class HouseholdEntityPosition(ImmutableModel):
    entity_id: str
    engine_position: str


class AggregateDimension(ImmutableModel):
    name: str
    value: str


class AggregateValue(ImmutableModel):
    output_id: str
    metric_id: str
    label: str
    value: float | int | None
    unit: str
    dimensions: tuple[AggregateDimension, ...] = ()


class ArtifactBase(ImmutableModel):
    artifact_id: str = Field(default_factory=_artifact_id)
    schema_version: str = "1"
    created_at: datetime = Field(default_factory=_now)
    provenance: ArtifactProvenance


class PolicyScenarioRef(ArtifactBase):
    artifact_type: Literal["policy_scenario"] = "policy_scenario"
    year: int
    scenario_revision: str
    catalogue_version: str
    calculation_engine_version: str
    baseline: bool
    verified_changes: tuple[PolicyChange, ...] = ()


class HouseholdRef(ArtifactBase):
    artifact_type: Literal["household"] = "household"
    year: int
    household_revision: str
    catalogue_version: str
    calculation_engine_version: str
    values: tuple[HouseholdValue, ...]
    context_scope_id: str | None = None
    context_revision: int | None = None
    entity_positions: tuple[HouseholdEntityPosition, ...] = ()


class HouseholdResultRef(ArtifactBase):
    artifact_type: Literal["household_result"] = "household_result"
    year: int
    household_artifact_id: str
    policy_scenario_artifact_id: str
    scenario_revision: str
    calculation_engine_version: str
    outputs: tuple[AggregateValue, ...]
    context_scope_id: str | None = None
    context_revision: int | None = None


class RequestedOutputIssue(ImmutableModel):
    request: str
    kind: Literal["ambiguous", "unsupported"]
    guidance: str


class SocietyDatasetProvenance(ImmutableModel):
    logical_name: str
    title: str
    data_package_name: str
    data_package_version: str
    revision: str
    sha256: str | None = None
    certification_basis: str | None = None
    certified_for_model_version: str | None = None


class SocietyAnalysisResultRef(ArtifactBase):
    artifact_type: Literal["society_analysis_result"] = "society_analysis_result"
    year: int
    policy_scenario_artifact_id: str
    scenario_revision: str
    catalogue_version: str
    dataset_version: str
    dataset: SocietyDatasetProvenance | None = None
    calculation_engine_version: str
    default_profile_version: str
    calculated_output_ids: tuple[str, ...]
    outputs: tuple[AggregateValue, ...]
    requested_output_issues: tuple[RequestedOutputIssue, ...] = ()

    @model_validator(mode="after")
    def dataset_revision_matches_compatibility_version(
        self,
    ) -> SocietyAnalysisResultRef:
        if self.dataset is not None and self.dataset.revision != self.dataset_version:
            raise ValueError("dataset revision must match dataset_version")
        return self


class ChartPresentation(ImmutableModel):
    chart_type: str
    title: str
    serialized_spec: str


class ChartArtifactRef(ArtifactBase):
    artifact_type: Literal["chart"] = "chart"
    source_result_artifact_id: str
    source_result_schema_version: str
    year: int
    scenario_revision: str
    calculation_engine_version: str
    presentation: ChartPresentation


TransferableArtifact: TypeAlias = (
    PolicyScenarioRef
    | HouseholdRef
    | HouseholdResultRef
    | SocietyAnalysisResultRef
    | ChartArtifactRef
)


ARTIFACT_MODELS: dict[str, type[ArtifactBase]] = {
    "policy_scenario": PolicyScenarioRef,
    "household": HouseholdRef,
    "household_result": HouseholdResultRef,
    "society_analysis_result": SocietyAnalysisResultRef,
    "chart": ChartArtifactRef,
}
