"""Deterministic population analysis with mandatory default aggregates."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from pydantic import BaseModel, ConfigDict

from capabilities.artifacts import (
    AggregateDimension,
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
from engine.py_runtime import resolve_dataset
from tools.analysis_support import (
    ExtractResultFindingsOutput,
    NumericalFact,
    SelectSupportedOutputsOutput,
)
from tools.contracts import CallerType, Visibility
from tools.typed_models import SafeToolOutput


SOCIETY_DEFAULT_PROFILE_VERSION = "1"
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
    narration_facts: tuple[NumericalFact, ...]
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
            "extract_result_findings",
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
            aggregate_values.extend(self._flatten(output_id, derivative.root))

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
        extracted = await context.invoke_tool(
            "extract_result_findings",
            {
                "outputs": [
                    output.model_dump(mode="json") for output in result.outputs
                ]
            },
        )
        if not isinstance(extracted, ExtractResultFindingsOutput):
            raise TypeError("Society finding extraction returned an incompatible output.")
        narration_facts = tuple(
            NumericalFact(label=finding.label, value=finding.value, unit=finding.unit)
            for finding in extracted.findings
            if finding.value is not None
        )
        return Completed(
            value=SocietyAnalysisOutput(
                result=result,
                year_source=resolved_year.source,
                narration_facts=narration_facts,
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

    @classmethod
    def _flatten(cls, output_id: str, payload: dict) -> tuple[AggregateValue, ...]:
        values: list[AggregateValue] = []
        ignored = {
            "status",
            "simulation_id",
            "result_id",
            "year",
            "quantiles",
            "decile_concept",
        }

        def visit(value, path: tuple[str, ...], dimensions=()):
            if isinstance(value, bool) or value is None:
                return
            if isinstance(value, (int, float)):
                metric = ".".join(path) or "value"
                values.append(
                    AggregateValue(
                        output_id=output_id,
                        metric_id=metric,
                        label=cls._label(output_id, path),
                        value=value,
                        unit=cls._unit(output_id, path),
                        dimensions=dimensions,
                    )
                )
                return
            if isinstance(value, dict):
                local_dimensions = list(dimensions)
                for key in ("decile", "group", "age_group", "poverty_type"):
                    if key in value and isinstance(value[key], (str, int)):
                        local_dimensions.append(
                            AggregateDimension(name=key, value=str(value[key]))
                        )
                for key, nested in value.items():
                    if key in ignored or key in {
                        "decile",
                        "group",
                        "age_group",
                        "poverty_type",
                        "label",
                        "description",
                    }:
                        continue
                    visit(nested, (*path, str(key)), tuple(local_dimensions))
                return
            if isinstance(value, list):
                for item in value:
                    visit(item, path, dimensions)

        visit(payload, ())
        return tuple(values)

    @staticmethod
    def _label(output_id, path):
        suffix = " ".join(path).replace("_", " ").title()
        prefix = output_id.replace("_", " ").title()
        return f"{prefix}: {suffix}" if suffix else prefix

    @staticmethod
    def _unit(output_id, path):
        metric = " ".join(path).casefold()
        if any(word in metric for word in ("rate", "share", "relative", "percent")):
            return "ratio"
        if output_id == "winners_losers" and any(
            word in metric for word in ("winner", "loser", "unchanged")
        ):
            return "people"
        if output_id in {"budgetary_impact", "program_statistics", "decile_impacts"}:
            return "GBP/year"
        return "number"

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
