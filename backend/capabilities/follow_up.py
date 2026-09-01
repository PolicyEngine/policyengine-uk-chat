"""Typed household and population result follow-up capability."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from capabilities.artifacts import (
    HouseholdResultRef,
    PolicyScenarioRef,
    SocietyAnalysisResultRef,
)
from capabilities.compatibility import ArtifactRequirements, check_artifact_compatibility
from capabilities.contracts import (
    ArtifactContract,
    Capability,
    CapabilityDependency,
    CapabilitySpec,
    Completed,
    NeedsInput,
    Unsupported,
)
from capabilities.society import (
    SOCIETY_DEFAULT_PROFILE_VERSION,
    SocietyAnalysisOutput,
    current_dataset_version,
)
from tools.analysis_support import (
    ExtractResultFindingsOutput,
    NumericalFact,
    SelectSupportedOutputsOutput,
)
from tools.contracts import CallerType, Visibility


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisFollowUpInput(StrictModel):
    question: str
    referenced_result_id: str | None = None
    requested_outputs: tuple[str, ...] = ()


class AnalysisFollowUpOutput(StrictModel):
    source_artifact_id: str
    result: HouseholdResultRef | SocietyAnalysisResultRef
    reran_provider: bool = False
    narration_facts: tuple[NumericalFact, ...] = ()
    numerical_verification: Literal["disabled"] | None = None


class AnalysisFollowUpCapability(
    Capability[AnalysisFollowUpInput, AnalysisFollowUpOutput]
):
    spec = CapabilitySpec(
        identifier="analysis_follow_up",
        version="1",
        description=(
            "Explain or extend a compatible typed household or population result "
            "without parsing earlier assistant prose."
        ),
        required_use=(
            "Use for later questions whose authoritative basis is an existing "
            "household or population result."
        ),
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL, CallerType.CAPABILITY}),
        input_model=AnalysisFollowUpInput,
        output_model=AnalysisFollowUpOutput,
        accepted_artifacts=(
            ArtifactContract(artifact_type="household_result", schema_version="1"),
            ArtifactContract(
                artifact_type="society_analysis_result",
                schema_version="1",
            ),
        ),
        dependencies=(
            CapabilityDependency(
                capability_id="society_analysis",
                artifact=ArtifactContract(
                    artifact_type="society_analysis_result",
                    schema_version="1",
                ),
            ),
        ),
        tool_dependencies=("select_supported_outputs", "extract_result_findings"),
    )

    async def run(self, capability_input: AnalysisFollowUpInput, context):
        candidates = [
            *await context.find_artifacts(HouseholdResultRef),
            *await context.find_artifacts(SocietyAnalysisResultRef),
        ]
        if capability_input.referenced_result_id is not None:
            candidates = [
                item
                for item in candidates
                if item.artifact_id == capability_input.referenced_result_id
            ]
        if not candidates:
            return NeedsInput(
                prompt="Which completed household or population result should I use?",
                missing_fields=("referenced_result_id",),
                partial_input=capability_input.model_dump(mode="json", exclude_none=True),
            )
        if len(candidates) > 1:
            distinctions = ", ".join(
                f"{item.artifact_type} {item.artifact_id} ({item.year})"
                for item in candidates
            )
            return NeedsInput(
                prompt=f"Please choose one prior result: {distinctions}.",
                missing_fields=("referenced_result_id",),
                partial_input=capability_input.model_dump(mode="json", exclude_none=True),
            )
        source = candidates[0]
        result = source
        reran = False
        if isinstance(source, SocietyAnalysisResultRef):
            compatibility = check_artifact_compatibility(
                source,
                ArtifactRequirements(
                    artifact_type="society_analysis_result",
                    schema_version="1",
                    year=source.year,
                    scenario_revision=source.scenario_revision,
                    dataset_version=current_dataset_version(),
                    calculation_engine_version=source.calculation_engine_version,
                ),
            )
            if not compatibility.compatible:
                return Unsupported(
                    reason="The retained population result is not compatible with this operation."
                )
            if source.default_profile_version != SOCIETY_DEFAULT_PROFILE_VERSION:
                return Unsupported(
                    reason="The retained population result uses an unsupported default profile."
                )
            selected = await context.invoke_tool(
                "select_supported_outputs",
                {"requested_outputs": list(capability_input.requested_outputs)},
            )
            if not isinstance(selected, SelectSupportedOutputsOutput):
                raise TypeError("Follow-up output selection returned an incompatible result.")
            missing = set(selected.output_ids) - set(source.calculated_output_ids)
            if missing:
                scenario = await self._scenario(source, context)
                if scenario is None:
                    return Unsupported(
                        reason=(
                            "The requested aggregate was not retained and its verified "
                            "policy scenario is unavailable for rerun."
                        )
                    )
                rerun = await context.invoke_capability(
                    "society_analysis",
                    {
                        "referenced_policy_scenario_id": scenario.artifact_id,
                        "year": source.year,
                        "requested_outputs": list(capability_input.requested_outputs),
                    },
                )
                if not isinstance(rerun, Completed) or not isinstance(
                    rerun.value,
                    SocietyAnalysisOutput,
                ):
                    return rerun
                result = rerun.value.result
                reran = True

        facts: tuple[NumericalFact, ...] = ()
        numerical_verification = None
        if isinstance(result, SocietyAnalysisResultRef):
            numerical_verification = "disabled"
        else:
            extracted = await context.invoke_tool(
                "extract_result_findings",
                {
                    "outputs": [
                        output.model_dump(mode="json") for output in result.outputs
                    ]
                },
            )
            if not isinstance(extracted, ExtractResultFindingsOutput):
                raise TypeError(
                    "Follow-up finding extraction returned an incompatible result."
                )
            facts = tuple(
                NumericalFact(
                    label=finding.label,
                    value=finding.value,
                    unit=finding.unit,
                )
                for finding in extracted.findings
                if finding.value is not None
            )
        return Completed(
            value=AnalysisFollowUpOutput(
                source_artifact_id=source.artifact_id,
                result=result,
                reran_provider=reran,
                narration_facts=facts,
                numerical_verification=numerical_verification,
            )
        )

    @staticmethod
    async def _scenario(result, context):
        scenarios = await context.find_artifacts(PolicyScenarioRef)
        return next(
            (
                scenario
                for scenario in scenarios
                if scenario.artifact_id == result.policy_scenario_artifact_id
                and scenario.scenario_revision == result.scenario_revision
            ),
            None,
        )
