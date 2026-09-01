from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from capabilities.artifacts import (
    AggregateValue,
    ArtifactProvenance,
    PolicyChange,
    PolicyScenarioRef,
    SocietyAnalysisResultRef,
    SocietyDatasetProvenance,
)
from capabilities.compatibility import (
    ArtifactRequirements,
    check_artifact_compatibility,
)
from chat.artifact_context import sanitized_artifact_summary
from chat.capability_service import capability_result_for_model


def provenance() -> ArtifactProvenance:
    return ArtifactProvenance(
        conversation_id="conversation-1",
        turn_id="turn-1",
        capability_id="policy_reform",
        capability_version="1",
        invocation_id="invocation-1",
        sources=("current user instruction",),
    )


def test_policy_scenario_is_immutable_and_carries_compatibility_metadata():
    scenario = PolicyScenarioRef(
        artifact_id="scenario-1",
        created_at=datetime.now(timezone.utc),
        provenance=provenance(),
        year=2026,
        scenario_revision="revision-1",
        catalogue_version="catalogue-1",
        calculation_engine_version="engine-1",
        baseline=False,
        verified_changes=(
            PolicyChange(
                parameter_path="gov.example.amount",
                value=15_000,
                effective_date="2026-01-01",
            ),
        ),
    )

    assert scenario.artifact_type == "policy_scenario"
    assert scenario.verified_changes[0].value == 15_000
    with pytest.raises(ValidationError):
        scenario.year = 2027  # type: ignore[misc]
    with pytest.raises(TypeError):
        scenario.verified_changes[0] = PolicyChange(  # type: ignore[index]
            parameter_path="changed",
            value=1,
        )


def test_compatibility_checks_only_declared_consumer_requirements():
    result = SocietyAnalysisResultRef(
        artifact_id="result-1",
        provenance=provenance(),
        year=2026,
        policy_scenario_artifact_id="scenario-1",
        scenario_revision="revision-1",
        catalogue_version="catalogue-1",
        dataset_version="dataset-1",
        dataset=SocietyDatasetProvenance(
            logical_name="enhanced_frs_2024_25",
            title="Enhanced FRS 2024-25",
            data_package_name="policyengine-uk-data",
            data_package_version="1.56.16",
            revision="dataset-1",
            sha256="dataset-sha256",
            certification_basis="legacy_compatible_model_package",
            certified_for_model_version="2.90.2",
        ),
        calculation_engine_version="engine-1",
        default_profile_version="default-1",
        calculated_output_ids=("budgetary_impact",),
        outputs=(
            AggregateValue(
                output_id="budgetary_impact",
                metric_id="net_cost",
                label="Net budget cost",
                value=1_000_000,
                unit="GBP/year",
            ),
        ),
    )

    compatible = check_artifact_compatibility(
        result,
        ArtifactRequirements(
            artifact_type="society_analysis_result",
            schema_version="1",
            year=2026,
            scenario_revision="revision-1",
            dataset_version="dataset-1",
            calculation_engine_version="engine-1",
        ),
    )
    incompatible = check_artifact_compatibility(
        result,
        ArtifactRequirements(
            artifact_type="society_analysis_result",
            schema_version="1",
            year=2025,
            scenario_revision="revision-2",
            calculation_engine_version="engine-2",
        ),
    )

    assert compatible.compatible is True
    assert compatible.issues == ()
    assert incompatible.compatible is False
    assert {issue.split(":", 1)[0] for issue in incompatible.issues} == {
        "year",
        "scenario_revision",
        "calculation_engine_version",
    }
    summary = sanitized_artifact_summary(result)
    assert summary["artifact_type"] == "society_analysis_result"
    assert summary["dataset_version"] == "dataset-1"
    assert summary["dataset"] == {
        "logical_name": "enhanced_frs_2024_25",
        "title": "Enhanced FRS 2024-25",
        "data_package_name": "policyengine-uk-data",
        "data_package_version": "1.56.16",
        "revision": "dataset-1",
        "sha256": "dataset-sha256",
        "certification_basis": "legacy_compatible_model_package",
        "certified_for_model_version": "2.90.2",
    }
    assert summary["outputs"] == (
        {
            "output_id": "budgetary_impact",
            "metric_id": "net_cost",
            "label": "Net budget cost",
            "value": 1_000_000,
            "unit": "GBP/year",
            "dimensions": [],
        },
    )
    assert "provenance" not in summary
    model_result = capability_result_for_model(
        {
            "status": "completed",
            "value": {"result": result.model_dump(mode="json")},
        }
    )
    model_dataset = model_result["value"]["result"]["dataset"]
    assert model_dataset == summary["dataset"]
    assert "uri" not in model_dataset


def test_society_dataset_revision_must_match_compatibility_version():
    with pytest.raises(ValidationError, match="dataset revision must match"):
        SocietyAnalysisResultRef(
            artifact_id="result-mismatched-dataset",
            provenance=provenance(),
            year=2026,
            policy_scenario_artifact_id="scenario-1",
            scenario_revision="revision-1",
            catalogue_version="catalogue-1",
            dataset_version="dataset-1",
            dataset=SocietyDatasetProvenance(
                logical_name="enhanced_frs_2024_25",
                title="Enhanced FRS 2024-25",
                data_package_name="policyengine-uk-data",
                data_package_version="1.56.16",
                revision="dataset-2",
            ),
            calculation_engine_version="engine-1",
            default_profile_version="default-1",
            calculated_output_ids=(),
            outputs=(),
        )


@pytest.mark.parametrize(
    ("field", "stale_value"),
    [
        ("artifact_type", "household_result"),
        ("schema_version", "2"),
        ("year", 2025),
        ("scenario_revision", "stale-revision"),
        ("catalogue_version", "stale-catalogue"),
        ("dataset_version", "stale-dataset"),
        ("calculation_engine_version", "stale-engine"),
    ],
)
def test_each_declared_compatibility_dimension_rejects_a_stale_reference(
    field,
    stale_value,
):
    result = SocietyAnalysisResultRef(
        artifact_id="result-property",
        provenance=provenance(),
        year=2026,
        policy_scenario_artifact_id="scenario-property",
        scenario_revision="revision-current",
        catalogue_version="catalogue-current",
        dataset_version="dataset-current",
        calculation_engine_version="engine-current",
        default_profile_version="1",
        calculated_output_ids=("budgetary_impact",),
        outputs=(
            AggregateValue(
                output_id="budgetary_impact",
                metric_id="net_cost",
                label="Net cost",
                value=1,
                unit="GBP/year",
            ),
        ),
    )
    requirement_values = {
        "artifact_type": "society_analysis_result",
        "schema_version": "1",
        "year": 2026,
        "scenario_revision": "revision-current",
        "catalogue_version": "catalogue-current",
        "dataset_version": "dataset-current",
        "calculation_engine_version": "engine-current",
    }
    requirement_values[field] = stale_value

    compatibility = check_artifact_compatibility(
        result,
        ArtifactRequirements.model_validate(requirement_values),
    )

    assert compatibility.compatible is False
    assert len(compatibility.issues) == 1
    assert compatibility.issues[0].startswith(f"{field}:")
