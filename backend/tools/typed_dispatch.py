"""Typed adapters over the retained deterministic tool functions."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import cast

from pydantic import BaseModel

from tools.context import ToolExecutionContext
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from tools.dispatch import execute_tool
from tools.registry import tool_specs
from tools.typed_models import (
    AggregateResultOutput,
    AggregateResultInput,
    BudgetaryImpactOutput,
    DecileImpactsInput,
    DecileImpactsOutput,
    EmptyInput,
    GenerateChartInput,
    GenerateChartOutput,
    GetParameterInput,
    GetParameterOutput,
    GetVariableInput,
    GetVariableOutput,
    HouseholdInput,
    HouseholdSimulationOutput,
    InequalityMetricsOutput,
    ListEntitiesOutput,
    ListHouseholdVariablesInput,
    ListHouseholdVariablesOutput,
    ListReformTargetsInput,
    ListReformTargetsOutput,
    ListSocietyVariablesInput,
    ListSocietyVariablesOutput,
    ListSupportedOutputsInput,
    ListSupportedOutputsOutput,
    PovertyMetricsOutput,
    ProgramBreakdownInput,
    ProgramBreakdownOutput,
    SafeToolOutput,
    SearchParametersInput,
    SearchParametersOutput,
    SearchVariablesInput,
    SearchVariablesOutput,
    SimulationRefInput,
    SocietySimulationInput,
    SocietySimulationOutput,
    ValidateHouseholdOutput,
    ValidateReformInput,
    ValidateReformOutput,
    WinnersLosersOutput,
    WinnersLosersInput,
)


_INPUT_MODELS: dict[str, type[BaseModel]] = {
    "list_entities": EmptyInput,
    "search_variables": SearchVariablesInput,
    "get_variable": GetVariableInput,
    "search_parameters": SearchParametersInput,
    "get_parameter": GetParameterInput,
    "list_reform_targets": ListReformTargetsInput,
    "list_household_input_variables": ListHouseholdVariablesInput,
    "list_society_output_variables": ListSocietyVariablesInput,
    "list_supported_outputs": ListSupportedOutputsInput,
    "validate_reform": ValidateReformInput,
    "validate_household": HouseholdInput,
    "run_household_simulation": HouseholdInput,
    "run_society_simulation": SocietySimulationInput,
    "compute_budgetary_impact": SimulationRefInput,
    "compute_program_breakdown": ProgramBreakdownInput,
    "compute_decile_impacts": DecileImpactsInput,
    "compute_winners_losers": WinnersLosersInput,
    "compute_poverty_metrics": SimulationRefInput,
    "compute_inequality_metrics": SimulationRefInput,
    "aggregate_result": AggregateResultInput,
    "generate_chart": GenerateChartInput,
}

_PRIVATE_TOOLS = frozenset({"validate_reform", "validate_household"})

_OUTPUT_MODELS: dict[str, type[SafeToolOutput]] = {
    "list_entities": ListEntitiesOutput,
    "search_variables": SearchVariablesOutput,
    "get_variable": GetVariableOutput,
    "search_parameters": SearchParametersOutput,
    "get_parameter": GetParameterOutput,
    "list_reform_targets": ListReformTargetsOutput,
    "list_household_input_variables": ListHouseholdVariablesOutput,
    "list_society_output_variables": ListSocietyVariablesOutput,
    "list_supported_outputs": ListSupportedOutputsOutput,
    "validate_reform": ValidateReformOutput,
    "validate_household": ValidateHouseholdOutput,
    "run_household_simulation": HouseholdSimulationOutput,
    "run_society_simulation": SocietySimulationOutput,
    "compute_budgetary_impact": BudgetaryImpactOutput,
    "compute_program_breakdown": ProgramBreakdownOutput,
    "compute_decile_impacts": DecileImpactsOutput,
    "compute_winners_losers": WinnersLosersOutput,
    "compute_poverty_metrics": PovertyMetricsOutput,
    "compute_inequality_metrics": InequalityMetricsOutput,
    "aggregate_result": AggregateResultOutput,
    "generate_chart": GenerateChartOutput,
}


class DispatchTool(Tool[BaseModel, SafeToolOutput]):
    """One typed object around one retained deterministic dispatcher function."""

    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    async def run(
        self,
        tool_input: BaseModel,
        context: ToolCallContext,
    ) -> SafeToolOutput:
        payload = tool_input.model_dump(mode="json", exclude_none=True)
        execution_context = ToolExecutionContext(
            turn_id=context.turn_id,
            result_store=context.result_store,
        )
        result = await asyncio.to_thread(
            execute_tool,
            self.spec.identifier,
            payload,
            context=execution_context,
        )
        return cast(
            SafeToolOutput,
            self.spec.output_model.model_validate(result),
        )


def build_dispatch_tools() -> tuple[DispatchTool, ...]:
    """Create one explicitly typed object for each retained tool identifier."""

    descriptions = {spec.name: spec.description for spec in tool_specs()}
    tools: list[DispatchTool] = []
    for identifier, input_model in _INPUT_MODELS.items():
        private = identifier in _PRIVATE_TOOLS
        allowed_callers = (
            frozenset({CallerType.CAPABILITY, CallerType.TOOL})
            if private
            else frozenset({CallerType.MODEL, CallerType.CAPABILITY, CallerType.TOOL})
        )
        tools.append(
            DispatchTool(
                ToolSpec(
                    identifier=identifier,
                    version="1",
                    description=descriptions[identifier],
                    visibility=(Visibility.PRIVATE if private else Visibility.PUBLIC),
                    allowed_callers=allowed_callers,
                    input_model=input_model,
                    output_model=_OUTPUT_MODELS[identifier],
                )
            )
        )
    return tuple(tools)


def dispatch_tools_by_id(
    tools: Iterable[DispatchTool] | None = None,
) -> dict[str, DispatchTool]:
    return {tool.spec.identifier: tool for tool in tools or build_dispatch_tools()}
