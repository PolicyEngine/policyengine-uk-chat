"""Deterministic chart creation from typed population result artifacts."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from capabilities.artifacts import (
    ArtifactProvenance,
    ChartArtifactRef,
    ChartPresentation,
    PolicyScenarioRef,
    SocietyAnalysisResultRef,
)
from capabilities.contracts import (
    ArtifactContract,
    Capability,
    CapabilityDependency,
    CapabilitySpec,
    Completed,
    NeedsInput,
    Unsupported,
)
from capabilities.society import SocietyAnalysisInput, SocietyAnalysisOutput
from tools.contracts import CallerType, Visibility
from tools.typed_models import SafeToolOutput


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SocietyChartInput(StrictModel):
    referenced_result_id: str | None = None
    requested_output: str | None = None
    title: str | None = None
    analysis: SocietyAnalysisInput | None = None


class SocietyChartOutput(StrictModel):
    chart: ChartArtifactRef
    source_result: SocietyAnalysisResultRef


class SocietyChartCapability(Capability[SocietyChartInput, SocietyChartOutput]):
    spec = CapabilitySpec(
        identifier="society_chart",
        version="1",
        description=(
            "Create a deterministic chart from a compatible population result, "
            "running population analysis first only when required inputs are complete."
        ),
        required_use="Use when the user asks to chart a population-analysis result.",
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL, CallerType.CAPABILITY}),
        input_model=SocietyChartInput,
        output_model=SocietyChartOutput,
        accepted_artifacts=(
            ArtifactContract(
                artifact_type="society_analysis_result",
                schema_version="1",
            ),
        ),
        produced_artifacts=(
            ArtifactContract(artifact_type="chart", schema_version="1"),
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
        tool_dependencies=("generate_chart",),
    )

    async def run(self, capability_input: SocietyChartInput, context):
        results = list(await context.find_artifacts(SocietyAnalysisResultRef))
        if capability_input.referenced_result_id is not None:
            results = [
                result
                for result in results
                if result.artifact_id == capability_input.referenced_result_id
            ]
        result = results[0] if len(results) == 1 else None
        if result is None and capability_input.analysis is not None:
            analysis_input = capability_input.analysis.model_copy(
                update={
                    "requested_outputs": tuple(
                        dict.fromkeys(
                            [
                                *capability_input.analysis.requested_outputs,
                                *(
                                    [capability_input.requested_output]
                                    if capability_input.requested_output
                                    else []
                                ),
                            ]
                        )
                    )
                }
            )
            analysis = await context.invoke_capability(
                "society_analysis",
                analysis_input,
            )
            if not isinstance(analysis, Completed) or not isinstance(
                analysis.value,
                SocietyAnalysisOutput,
            ):
                return analysis
            result = analysis.value.result
        if result is None:
            return NeedsInput(
                prompt=(
                    "Which population result should I chart, or what policy scenario "
                    "should I calculate first?"
                ),
                missing_fields=("referenced_result_id", "analysis"),
                partial_input=capability_input.model_dump(mode="json", exclude_none=True),
            )

        output_id = self._output_id(capability_input, result)
        if output_id not in result.calculated_output_ids:
            scenarios = await context.find_artifacts(PolicyScenarioRef)
            scenario = next(
                (
                    item
                    for item in scenarios
                    if item.artifact_id == result.policy_scenario_artifact_id
                    and item.scenario_revision == result.scenario_revision
                ),
                None,
            )
            if scenario is None or capability_input.requested_output is None:
                return Unsupported(
                    reason=(
                        "The retained result does not contain the requested chart metric "
                        "and its verified scenario is unavailable for rerun."
                    )
                )
            rerun = await context.invoke_capability(
                "society_analysis",
                {
                    "referenced_policy_scenario_id": scenario.artifact_id,
                    "year": result.year,
                    "requested_outputs": [capability_input.requested_output],
                },
            )
            if not isinstance(rerun, Completed) or not isinstance(
                rerun.value,
                SocietyAnalysisOutput,
            ):
                return rerun
            result = rerun.value.result
            output_id = self._output_id(capability_input, result)
            if output_id not in result.calculated_output_ids:
                return Unsupported(reason="The requested chart metric is unsupported.")
        selected = [output for output in result.outputs if output.output_id == output_id]
        if not selected:
            return Unsupported(reason="The requested chart metric has no retained values.")
        rows = [
            {
                "label": self._row_label(output),
                "value": output.value,
            }
            for output in selected
            if output.value is not None
        ]
        if not rows:
            return Unsupported(reason="The requested chart metric has no numeric values.")
        title = capability_input.title or output_id.replace("_", " ").title()
        chart_result = await context.invoke_tool(
            "generate_chart",
            {
                "chart_kind": "generic_bar",
                "data": rows,
                "title": title,
                "x_field": "label",
                "y_fields": ["value"],
                "source": "PolicyEngine UK",
            },
        )
        if not isinstance(chart_result, SafeToolOutput):
            raise TypeError("Chart generation returned an incompatible output.")
        spec = chart_result.root.get("spec")
        markdown = chart_result.root.get("chart_markdown")
        if not isinstance(spec, dict) or not isinstance(markdown, str):
            return Unsupported(reason="Chart generation did not return a supported artifact.")
        chart = ChartArtifactRef(
            provenance=ArtifactProvenance(
                conversation_id=context.conversation_id,
                turn_id=context.turn_id,
                capability_id=self.spec.identifier,
                capability_version=self.spec.version,
                invocation_id=context.capability_invocation_id,
                sources=(result.artifact_id, output_id),
            ),
            source_result_artifact_id=result.artifact_id,
            source_result_schema_version=result.schema_version,
            year=result.year,
            scenario_revision=result.scenario_revision,
            calculation_engine_version=result.calculation_engine_version,
            presentation=ChartPresentation(
                chart_type="generic_bar",
                title=title,
                serialized_spec=json.dumps(spec, sort_keys=True),
            ),
        )
        chart = await context.save_artifact(chart)
        return Completed(value=SocietyChartOutput(chart=chart, source_result=result))

    @staticmethod
    def _output_id(capability_input, result):
        if capability_input.requested_output:
            normalized = capability_input.requested_output.casefold().replace(" ", "_")
            aliases = {
                "budget": "budgetary_impact",
                "winners_and_losers": "winners_losers",
                "deciles": "decile_impacts",
                "poverty_rate": "poverty",
            }
            return aliases.get(normalized, normalized)
        if "decile_impacts" in result.calculated_output_ids:
            return "decile_impacts"
        return result.calculated_output_ids[0]

    @staticmethod
    def _row_label(output):
        dimensions = ", ".join(
            f"{dimension.name} {dimension.value}" for dimension in output.dimensions
        )
        return dimensions or output.label
