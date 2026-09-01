"""Deterministic population analysis with mandatory default aggregates."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Literal

from pydantic import BaseModel, ConfigDict

from capabilities.artifacts import (
    AggregateValue,
    ArtifactProvenance,
    PolicyScenarioRef,
    RequestedOutputIssue,
    SocietyAnalysisResultRef,
    SocietyDatasetProvenance,
)
from capabilities.contracts import (
    ArtifactContract,
    Capability,
    CapabilityDependency,
    CapabilitySpec,
    Completed,
    Failed,
    NeedsInput,
)
from capabilities.input_resolution import InputSource, resolve_policy_year
from capabilities.policy_reform import PolicyReformOutput
from capabilities.society_outputs import validated_aggregate_values
from engine.py_runtime import resolve_dataset
from tools.analysis_support import (
    SelectSupportedOutputsOutput,
)
from tools.contracts import CallerType, Visibility
from tools.typed_models import SafeToolOutput


SOCIETY_DEFAULT_PROFILE_VERSION = "2"
SOCIETY_DEFAULT_OUTPUTS = (
    "budgetary_impact",
    "winners_losers",
    "decile_impacts",
)
SOCIETY_DEFAULT_DECILE_CONCEPT = "household_net_income"
SOCIETY_EXTRA_VARIABLES_BY_OUTPUT: dict[str, dict[str, tuple[str, ...]]] = {
    "budgetary_impact": {},
    "program_statistics": {},
    "decile_impacts": {},
    "winners_losers": {},
    "poverty": {},
    "inequality": {},
}


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unavailable"


def current_dataset_version() -> str:
    return resolve_dataset().revision


def current_dataset_provenance() -> SocietyDatasetProvenance:
    dataset = resolve_dataset()
    return SocietyDatasetProvenance(
        logical_name=dataset.name,
        title=dataset.title,
        data_package_name=dataset.data_package_name,
        data_package_version=dataset.data_package_version,
        revision=dataset.revision,
        sha256=dataset.sha256,
        certification_basis=dataset.certification_basis,
        certified_for_model_version=dataset.certified_for_model_version,
    )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SocietyAnalysisInput(StrictModel):
    reform_instruction: str | None = None
    referenced_policy_scenario_id: str | None = None
    year: int | None = None
    requested_outputs: tuple[str, ...] = ()


class SocietyAnalysisOutput(StrictModel):
    result: SocietyAnalysisResultRef
    year_source: InputSource
    numerical_verification: Literal["disabled"] = "disabled"
    required_output_ids: tuple[str, ...] = SOCIETY_DEFAULT_OUTPUTS
    narration_requirement: str = (
        "Present budgetary impact, winners/losers/unchanged, and income-decile "
        "impacts, plus every successfully calculated requested output and any issue."
    )


_DERIVATIVE_TOOL = {
    "budgetary_impact": "compute_budgetary_impact",
    "program_statistics": "compute_program_breakdown",
    "decile_impacts": "compute_decile_impacts",
    "winners_losers": "compute_winners_losers",
    "poverty": "compute_poverty_metrics",
    "inequality": "compute_inequality_metrics",
}

_DERIVATIVE_EXECUTION_PRIORITY = (
    "decile_impacts",
    "winners_losers",
    "budgetary_impact",
)


class SocietyAnalysisCapability(Capability[SocietyAnalysisInput, SocietyAnalysisOutput]):
    spec = CapabilitySpec(
        identifier="society_analysis",
        version="1",
        description=(
            "Run deterministic UK population analysis with mandatory budget, "
            "winners/losers, and household-income-decile aggregates plus supported "
            "requested outputs."
        ),
        required_use=(
            "Required for population-wide reform costs, distributional effects, "
            "poverty, inequality, winners/losers, or aggregate benefit impacts."
        ),
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL, CallerType.CAPABILITY}),
        input_model=SocietyAnalysisInput,
        output_model=SocietyAnalysisOutput,
        accepted_artifacts=(
            ArtifactContract(artifact_type="policy_scenario", schema_version="1"),
        ),
        produced_artifacts=(
            ArtifactContract(
                artifact_type="society_analysis_result",
                schema_version="1",
            ),
        ),
        dependencies=(
            CapabilityDependency(
                capability_id="policy_reform",
                artifact=ArtifactContract(
                    artifact_type="policy_scenario",
                    schema_version="1",
                ),
            ),
        ),
        tool_dependencies=(
            "select_supported_outputs",
            "run_society_simulation",
            "compute_budgetary_impact",
            "compute_program_breakdown",
            "compute_decile_impacts",
            "compute_winners_losers",
            "compute_poverty_metrics",
            "compute_inequality_metrics",
        ),
    )

    async def run(self, capability_input: SocietyAnalysisInput, context):
        scenario = await self._scenario(capability_input, context)
        resolved_year = resolve_policy_year(
            explicit_year=capability_input.year,
            referenced_year=scenario.year if scenario is not None else None,
        )
        if capability_input.reform_instruction:
            scenario_outcome = await context.invoke_capability(
                "policy_reform",
                {
                    "instruction": capability_input.reform_instruction,
                    "year": resolved_year.year,
                    "referenced_policy_scenario_id": (
                        scenario.artifact_id if scenario is not None else None
                    ),
                },
            )
            if not isinstance(scenario_outcome, Completed) or not isinstance(
                scenario_outcome.value,
                PolicyReformOutput,
            ):
                return await self._forward_reform_outcome(
                    scenario_outcome,
                    capability_input,
                    context,
                )
            scenario = scenario_outcome.value.scenario
        elif scenario is None:
            scenario_outcome = await context.invoke_capability(
                "policy_reform",
                {"instruction": "current law", "year": resolved_year.year},
            )
            if not isinstance(scenario_outcome, Completed) or not isinstance(
                scenario_outcome.value,
                PolicyReformOutput,
            ):
                return await self._forward_reform_outcome(
                    scenario_outcome,
                    capability_input,
                    context,
                )
            scenario = scenario_outcome.value.scenario

        selected = await context.invoke_tool(
            "select_supported_outputs",
            {"requested_outputs": list(capability_input.requested_outputs)},
        )
        if not isinstance(selected, SelectSupportedOutputsOutput):
            raise TypeError("Society output selection returned an incompatible output.")
        reform = {
            change.parameter_path: change.value for change in scenario.verified_changes
        }
        simulation_input = {
            "year": resolved_year.year,
            "reform": reform or None,
        }
        extra_variables = self._extra_variables(selected.output_ids)
        if extra_variables:
            simulation_input["extra_variables"] = extra_variables
        simulation = await context.invoke_tool(
            "run_society_simulation",
            simulation_input,
        )
        if not isinstance(simulation, SafeToolOutput):
            raise TypeError("Society simulation returned an incompatible output.")
        simulation_id = simulation.root.get("result_id")
        if not isinstance(simulation_id, str) or "error" in simulation.root:
            return Failed(
                safe_message="The deterministic population calculation failed.",
                error_code="society_simulation_failed",
            )

        aggregate_values: list[AggregateValue] = []
        for output_id in self._execution_order(selected.output_ids):
            tool_id = _DERIVATIVE_TOOL.get(output_id)
            if tool_id is None:
                continue
            tool_input = {"simulation_id": simulation_id}
            if output_id == "decile_impacts":
                tool_input["decile_concept"] = SOCIETY_DEFAULT_DECILE_CONCEPT
            derivative = await context.invoke_tool(tool_id, tool_input)
            if not isinstance(derivative, SafeToolOutput):
                raise TypeError(f"{tool_id} returned an incompatible output.")
            if "error" in derivative.root:
                if output_id in SOCIETY_DEFAULT_OUTPUTS:
                    return Failed(
                        safe_message=f"The required {output_id} calculation failed.",
                        error_code="society_default_output_failed",
                    )
                continue
            try:
                aggregate_values.extend(
                    validated_aggregate_values(output_id, derivative.root)
                )
            except ValueError:
                return Failed(
                    safe_message=(
                        f"The calculated {output_id} values did not pass validation."
                    ),
                    error_code="society_output_validation_failed",
                )

        calculated_ids = tuple(
            output_id
            for output_id in selected.output_ids
            if any(value.output_id == output_id for value in aggregate_values)
        )
        missing_defaults = set(SOCIETY_DEFAULT_OUTPUTS) - set(calculated_ids)
        if missing_defaults:
            return Failed(
                safe_message=(
                    "The population calculation did not produce the complete default "
                    "aggregate profile."
                ),
                error_code="society_default_profile_incomplete",
            )
        issues = tuple(
            RequestedOutputIssue(
                request=issue.request,
                kind=issue.kind,
                guidance=issue.guidance,
            )
            for issue in selected.issues
        )
        dataset = current_dataset_provenance()
        result = SocietyAnalysisResultRef(
            provenance=ArtifactProvenance(
                conversation_id=context.conversation_id,
                turn_id=context.turn_id,
                capability_id=self.spec.identifier,
                capability_version=self.spec.version,
                invocation_id=context.capability_invocation_id,
                sources=(
                    "certified default dataset",
                    "verified scenario",
                    "aggregate derivatives",
                ),
            ),
            year=resolved_year.year,
            policy_scenario_artifact_id=scenario.artifact_id,
            scenario_revision=scenario.scenario_revision,
            catalogue_version=scenario.catalogue_version,
            dataset_version=dataset.revision,
            dataset=dataset,
            calculation_engine_version=scenario.calculation_engine_version,
            default_profile_version=SOCIETY_DEFAULT_PROFILE_VERSION,
            calculated_output_ids=calculated_ids,
            outputs=tuple(aggregate_values),
            requested_output_issues=issues,
        )
        result = await context.save_artifact(result)
        return Completed(
            value=SocietyAnalysisOutput(
                result=result,
                year_source=resolved_year.source,
            )
        )

    @staticmethod
    async def _forward_reform_outcome(outcome, capability_input, context):
        if not isinstance(outcome, NeedsInput):
            return outcome
        await context.persist_waiting(capability_input)
        return NeedsInput(
            prompt=outcome.prompt,
            missing_fields=("reform_instruction",),
            partial_input=capability_input.model_dump(
                mode="json",
                exclude_none=True,
                exclude_defaults=True,
            ),
        )

    @staticmethod
    async def _scenario(capability_input, context):
        if capability_input.referenced_policy_scenario_id is None:
            return None
        scenarios = await context.find_artifacts(PolicyScenarioRef)
        return next(
            (
                scenario
                for scenario in scenarios
                if scenario.artifact_id
                == capability_input.referenced_policy_scenario_id
            ),
            None,
        )

    @staticmethod
    def _execution_order(output_ids):
        priority = {
            output_id: index
            for index, output_id in enumerate(_DERIVATIVE_EXECUTION_PRIORITY)
        }
        return tuple(
            sorted(
                output_ids,
                key=lambda output_id: priority.get(
                    output_id,
                    len(priority),
                ),
            )
        )

    @staticmethod
    def _extra_variables(output_ids):
        by_entity: dict[str, list[str]] = {}
        for output_id in output_ids:
            for entity, variables in SOCIETY_EXTRA_VARIABLES_BY_OUTPUT.get(
                output_id,
                {},
            ).items():
                selected = by_entity.setdefault(entity, [])
                for variable in variables:
                    if variable not in selected:
                        selected.append(variable)
        return by_entity
