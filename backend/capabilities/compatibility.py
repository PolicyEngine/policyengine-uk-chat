"""Central compatibility checks for transferable capability artifacts."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from capabilities.artifacts import ArtifactBase


class ArtifactRequirements(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_type: str
    schema_version: str
    year: int | None = None
    scenario_revision: str | None = None
    catalogue_version: str | None = None
    dataset_version: str | None = None
    calculation_engine_version: str | None = None


class ArtifactCompatibility(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    compatible: bool
    issues: tuple[str, ...]


def check_artifact_compatibility(
    artifact: ArtifactBase,
    requirements: ArtifactRequirements,
) -> ArtifactCompatibility:
    issues: list[str] = []
    fields = (
        "artifact_type",
        "schema_version",
        "year",
        "scenario_revision",
        "catalogue_version",
        "dataset_version",
        "calculation_engine_version",
    )
    for field in fields:
        required = getattr(requirements, field)
        if required is None:
            continue
        actual = getattr(artifact, field, None)
        if actual != required:
            issues.append(f"{field}: expected {required!r}, got {actual!r}")
    return ArtifactCompatibility(compatible=not issues, issues=tuple(issues))
