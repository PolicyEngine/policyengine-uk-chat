"""Strict input and JSON-safe output models for deterministic UK Chat tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    TypeAdapter,
    model_validator,
)


CAPABILITY_DEFAULT_YEAR = 2026


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(StrictInput):
    pass


class SearchVariablesInput(StrictInput):
    query: str = ""
    entity: str | None = None
    limit: int = Field(default=25, ge=1, le=100)


class GetVariableInput(StrictInput):
    name: str


class SearchParametersInput(StrictInput):
    query: str = ""
    limit: int = Field(default=25, ge=1, le=100)


class GetParameterInput(StrictInput):
    path: str
    year: int = CAPABILITY_DEFAULT_YEAR


class ListReformTargetsInput(StrictInput):
    query: str = ""
    limit: int = Field(default=25, ge=1, le=100)


class ListHouseholdVariablesInput(StrictInput):
    entity: str | None = None


Entity = Literal["person", "benunit", "household"]


class ListSocietyVariablesInput(StrictInput):
    entity: Entity | None = None


class ListSupportedOutputsInput(StrictInput):
    scope: Literal["household", "society", "derivative", "artifact"] | None = None


class ValidateReformInput(StrictInput):
    reform: dict[str, JsonValue]
    year: int = CAPABILITY_DEFAULT_YEAR


class HouseholdInput(StrictInput):
    people: tuple[dict[str, JsonValue], ...]
    benunit: dict[str, JsonValue] | None = None
    household: dict[str, JsonValue] | None = None
    year: int = CAPABILITY_DEFAULT_YEAR
    reform: dict[str, JsonValue] | None = None
    extra_variables: tuple[str, ...] | None = None


class SocietyExtraVariables(StrictInput):
    person: tuple[str, ...] = ()
    benunit: tuple[str, ...] = ()
    household: tuple[str, ...] = ()


class SocietySimulationInput(StrictInput):
    year: int = CAPABILITY_DEFAULT_YEAR
    reform: dict[str, JsonValue] | None = None
    extra_variables: SocietyExtraVariables | None = None


class SimulationRefInput(StrictInput):
    simulation_id: str


class ProgramBreakdownInput(SimulationRefInput):
    programs: tuple[str, ...] | None = None


class DecileImpactsInput(SimulationRefInput):
    decile_concept: Literal[
        "household_net_income",
        "equivalised_hbai_net_income",
        "wealth",
    ] = "household_net_income"


class WinnersLosersInput(SimulationRefInput):
    decile_concept: Literal[
        "household_net_income",
        "equivalised_hbai_net_income",
        "wealth",
    ] = "household_net_income"


FilterValue = float | int | str | bool


class AggregateResultInput(SimulationRefInput):
    target: Literal["baseline", "reform", "change"] = "reform"
    entity: Entity
    variable: str
    operation: Literal["sum", "mean", "count"]
    filter_variable: str | None = None
    filter_variable_eq: FilterValue | None = None
    filter_variable_leq: FilterValue | None = None
    filter_variable_geq: FilterValue | None = None


ChartKind = Literal[
    "budget_waterfall",
    "program_budget_waterfall",
    "decile_absolute_bar",
    "decile_relative_bar",
    "winners_losers_stacked_bar",
    "poverty_relative_bar",
    "inequality_relative_bar",
    "earnings_variation_line",
    "generic_line",
    "generic_bar",
    "generic_area",
    "generic_scatter",
]
ChartFormat = Literal[
    "currency",
    "percent",
    "percent_decimal",
    "number",
    "compact",
    "year",
]


class GenerateChartInput(StrictInput):
    chart_kind: ChartKind
    result_id: str | None = None
    data: JsonValue | None = None
    title: str | None = None
    subtitle: str | None = None
    source: str | None = None
    x_field: str | None = None
    y_fields: tuple[str, ...] | None = None
    x_label: str | None = None
    y_label: str | None = None
    x_format: ChartFormat | None = None
    y_format: ChartFormat | None = None
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    series_labels: tuple[str, ...] | None = None
    series_styles: tuple[Literal["solid", "dashed", "dotted"], ...] | None = None
    series_curves: tuple[Literal["smooth", "step", "linear"], ...] | None = None
    arrangement: Literal["grouped", "stacked"] | None = None


_ROW_LEVEL_KEYS = frozenset(
    {
        "microdata",
        "row_data",
        "row_level_data",
        "records",
        "survey_records",
    }
)


def _contains_row_level_key(value: JsonValue) -> bool:
    if isinstance(value, dict):
        if _ROW_LEVEL_KEYS.intersection(value):
            return True
        return any(_contains_row_level_key(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_row_level_key(item) for item in value)
    return False


class SafeToolOutput(RootModel[dict[str, JsonValue]]):
    """Base for JSON-only operation-specific retained-tool results.

    Concrete subclasses declare the fields and types that identify their own
    operation.  Keeping the raw dictionary as the root preserves the retained
    dispatcher contract while preventing one tool's result from validating as
    another tool's output.
    """

    required_fields: ClassVar[frozenset[str]] = frozenset()
    allowed_fields: ClassVar[frozenset[str] | None] = None
    field_types: ClassVar[Mapping[str, Any]] = {}
    any_field_groups: ClassVar[tuple[frozenset[str], ...]] = ()

    _error_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "status",
            "error",
            "detail",
            "suggestions",
            "available_entities",
            "validation",
        }
    )

    @model_validator(mode="after")
    def exclude_row_level_data(self) -> "SafeToolOutput":
        if _contains_row_level_key(self.root):
            raise ValueError("Tool output contains row-level data.")
        if "error" in self.root:
            if not isinstance(self.root["error"], str):
                raise ValueError("Tool error output requires a string error.")
            unexpected = set(self.root) - self._error_fields
            if unexpected:
                raise ValueError(
                    f"Tool error output contains unexpected fields: {sorted(unexpected)}"
                )
            return self

        missing = self.required_fields - set(self.root)
        if missing:
            raise ValueError(
                f"Tool output is missing required fields: {sorted(missing)}"
            )
        if self.allowed_fields is not None:
            unexpected = set(self.root) - self.allowed_fields
            if unexpected:
                raise ValueError(
                    f"Tool output contains unexpected fields: {sorted(unexpected)}"
                )
        for group in self.any_field_groups:
            if not group.intersection(self.root):
                raise ValueError(
                    "Tool output requires one of: " + ", ".join(sorted(group))
                )
        for field, expected_type in self.field_types.items():
            if field not in self.root:
                continue
            TypeAdapter(expected_type).validate_python(self.root[field])
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return self.root


class EntitySummary(StrictInput):
    name: str
    variable_count: int = Field(ge=0)


class VariableSummary(StrictInput):
    name: str
    label: str | None = None
    entity: str | None = None
    description: str | None = None
    definition_period: str | None = None
    unit: str | None = None
    quantity_type: str | None = None
    reference: JsonValue | None = None
    defined_for: JsonValue | None = None
    min_value: JsonValue | None = None
    max_value: JsonValue | None = None
    is_period_size_independent: bool | None = None
    metadata: JsonValue | None = None
    value_type: str | None = None
    default_value: JsonValue | None = None
    possible_values: JsonValue | None = None
    is_default_society_output: bool | None = None
    default_output_entities: tuple[str, ...] = ()


class ParameterSummary(StrictInput):
    path: str
    label: str | None = None
    description: str | None = None
    unit: str | None = None
    aliases: tuple[str, ...] = ()
    year: int | None = None
    value: JsonValue | None = None


class ReformTargetSummary(StrictInput):
    path: str
    label: str | None = None
    description: str | None = None
    aliases: tuple[str, ...] = ()


class SupportedOutputSummary(StrictInput):
    scope: str
    name: str
    description: str | None = None


class ListEntitiesOutput(SafeToolOutput):
    required_fields = frozenset({"status", "entities"})
    allowed_fields = required_fields
    field_types = {
        "status": Literal["success"],
        "entities": tuple[EntitySummary, ...],
    }


class SearchVariablesOutput(SafeToolOutput):
    required_fields = frozenset({"status", "variables"})
    allowed_fields = frozenset({"status", "query", "entity", "variables"})
    field_types = {
        "status": Literal["success"],
        "query": str,
        "entity": str | None,
        "variables": tuple[VariableSummary, ...],
    }


class GetVariableOutput(SafeToolOutput):
    required_fields = frozenset({"status", "variable"})
    allowed_fields = required_fields
    field_types = {
        "status": Literal["success"],
        "variable": VariableSummary,
    }


class SearchParametersOutput(SafeToolOutput):
    required_fields = frozenset({"status", "parameters"})
    allowed_fields = frozenset({"status", "query", "parameters"})
    field_types = {
        "status": Literal["success"],
        "query": str,
        "parameters": tuple[ParameterSummary, ...],
    }


class GetParameterOutput(SafeToolOutput):
    required_fields = frozenset({"status", "parameter"})
    allowed_fields = required_fields
    field_types = {
        "status": Literal["success"],
        "parameter": ParameterSummary,
    }


class ListReformTargetsOutput(SafeToolOutput):
    required_fields = frozenset({"status", "targets"})
    allowed_fields = frozenset({"status", "query", "targets"})
    field_types = {
        "status": Literal["success"],
        "query": str,
        "targets": tuple[ReformTargetSummary, ...],
    }


class ListHouseholdVariablesOutput(SearchVariablesOutput):
    required_fields = frozenset(
        {"status", "query", "entity", "variables", "input_contract"}
    )
    allowed_fields = required_fields
    field_types = {
        **SearchVariablesOutput.field_types,
        "input_contract": str,
    }


class ListSocietyVariablesOutput(SafeToolOutput):
    required_fields = frozenset(
        {
            "status",
            "entity",
            "default_variables_by_entity",
            "default_variable_count",
            "extra_variables_contract",
        }
    )
    allowed_fields = required_fields
    field_types = {
        "status": Literal["success"],
        "entity": str | None,
        "default_variables_by_entity": dict[str, tuple[str, ...]],
        "default_variable_count": int,
        "extra_variables_contract": str,
    }


class ListSupportedOutputsOutput(SafeToolOutput):
    required_fields = frozenset({"status", "scope", "outputs"})
    allowed_fields = required_fields
    field_types = {
        "status": Literal["success"],
        "scope": str | None,
        "outputs": tuple[SupportedOutputSummary, ...],
    }


class ValidateReformOutput(SafeToolOutput):
    required_fields = frozenset({"valid"})
    allowed_fields = frozenset(
        {
            "valid",
            "normalized_reform",
            "reform_object",
            "parameter_paths",
            "warnings",
            "errors",
            "valid_targets_sample",
        }
    )
    field_types = {
        "valid": bool,
        "normalized_reform": dict[str, JsonValue],
        "parameter_paths": tuple[str, ...],
        "warnings": tuple[JsonValue, ...],
        "errors": tuple[dict[str, JsonValue], ...],
        "valid_targets_sample": tuple[str, ...],
    }


class ValidateHouseholdOutput(SafeToolOutput):
    required_fields = frozenset({"valid"})
    allowed_fields = frozenset(
        {
            "valid",
            "year",
            "people_count",
            "extra_variables_by_entity",
            "normalized_reform",
            "reform_object",
            "warnings",
            "errors",
        }
    )
    field_types = {
        "valid": bool,
        "year": int,
        "people_count": int,
        "extra_variables_by_entity": dict[str, tuple[str, ...]],
        "normalized_reform": dict[str, JsonValue],
        "warnings": tuple[JsonValue, ...],
        "errors": tuple[dict[str, JsonValue], ...],
    }


class HouseholdSimulationOutput(SafeToolOutput):
    required_fields = frozenset({"status", "year", "reform_applied", "result_id"})
    field_types = {
        "status": Literal["success"],
        "year": int,
        "reform_applied": bool,
        "result_id": str,
    }


class SocietySimulationOutput(SafeToolOutput):
    required_fields = frozenset({"status", "year", "result_id"})
    allowed_fields = frozenset(
        {"status", "fiscal_year", "year", "dataset", "reform_applied", "result_id"}
    )
    field_types = {
        "status": Literal["success"],
        "fiscal_year": str,
        "year": int,
        "dataset": dict[str, JsonValue],
        "reform_applied": bool,
        "result_id": str,
    }


class BudgetaryImpactOutput(SafeToolOutput):
    required_fields = frozenset({"status", "simulation_id", "result_id"})
    allowed_fields = frozenset(
        {
            "status",
            "simulation_id",
            "tax_revenue",
            "benefit_spending",
            "net_budgetary_impact",
            "net_cost",
            "result_id",
        }
    )
    any_field_groups = (frozenset({"net_budgetary_impact", "net_cost"}),)
    field_types = {
        "status": Literal["success"],
        "simulation_id": str,
        "tax_revenue": float | dict[str, float],
        "benefit_spending": float | dict[str, float],
        "net_budgetary_impact": float,
        "net_cost": float,
        "result_id": str,
    }


class ProgramBreakdownOutput(SafeToolOutput):
    required_fields = frozenset({"status", "simulation_id", "result_id"})
    allowed_fields = frozenset(
        {
            "status",
            "simulation_id",
            "programs",
            "programmes",
            "net_budgetary_impact",
            "result_id",
        }
    )
    any_field_groups = (frozenset({"programs", "programmes"}),)
    field_types = {
        "status": Literal["success"],
        "simulation_id": str,
        "programs": tuple[dict[str, JsonValue], ...],
        "programmes": tuple[dict[str, JsonValue], ...],
        "net_budgetary_impact": float,
        "result_id": str,
    }


class DecileImpactsOutput(SafeToolOutput):
    required_fields = frozenset({"status", "simulation_id", "deciles", "result_id"})
    allowed_fields = frozenset(
        {
            "status",
            "simulation_id",
            "decile_concept",
            "basis",
            "income_variable",
            "decile_variable",
            "grouping_variable",
            "entity",
            "quantiles",
            "measure_label",
            "grouping_label",
            "deciles",
            "result_id",
        }
    )
    field_types = {
        "status": Literal["success"],
        "simulation_id": str,
        "decile_concept": str,
        "basis": str,
        "income_variable": str,
        "decile_variable": str | None,
        "grouping_variable": str,
        "entity": str,
        "quantiles": int,
        "measure_label": str,
        "grouping_label": str,
        "deciles": tuple[dict[str, JsonValue], ...],
        "result_id": str,
    }


class WinnersLosersOutput(SafeToolOutput):
    required_fields = frozenset({"status", "simulation_id", "result_id"})
    allowed_fields = frozenset(
        {
            "status",
            "simulation_id",
            "decile_concept",
            "basis",
            "income_variable",
            "decile_variable",
            "grouping_variable",
            "entity",
            "quantiles",
            "measure_label",
            "grouping_label",
            "deciles",
            "winners",
            "losers",
            "unchanged",
            "result_id",
        }
    )
    any_field_groups = (frozenset({"deciles", "winners"}),)
    field_types = {
        "status": Literal["success"],
        "simulation_id": str,
        "decile_concept": str,
        "basis": str,
        "income_variable": str,
        "decile_variable": str | None,
        "grouping_variable": str,
        "entity": str,
        "quantiles": int,
        "measure_label": str,
        "grouping_label": str,
        "deciles": tuple[dict[str, JsonValue], ...],
        "winners": float,
        "losers": float,
        "unchanged": float,
        "result_id": str,
    }


class PovertyMetricsOutput(SafeToolOutput):
    required_fields = frozenset({"status", "simulation_id", "result_id"})
    allowed_fields = frozenset(
        {
            "status",
            "simulation_id",
            "rates",
            "overall_rate",
            "change",
            "result_id",
        }
    )
    any_field_groups = (frozenset({"rates", "overall_rate"}),)
    field_types = {
        "status": Literal["success"],
        "simulation_id": str,
        "rates": tuple[dict[str, JsonValue], ...],
        "overall_rate": float,
        "change": float,
        "result_id": str,
    }


class InequalityMetricsOutput(SafeToolOutput):
    required_fields = frozenset({"status", "simulation_id", "result_id"})
    allowed_fields = frozenset(
        {"status", "simulation_id", "metrics", "gini", "result_id"}
    )
    any_field_groups = (frozenset({"metrics", "gini"}),)
    field_types = {
        "status": Literal["success"],
        "simulation_id": str,
        "metrics": dict[str, dict[str, float | None]],
        "gini": float,
        "result_id": str,
    }


class AggregateResultOutput(SafeToolOutput):
    required_fields = frozenset(
        {"status", "simulation_id", "result", "privacy", "result_id"}
    )
    allowed_fields = required_fields
    field_types = {
        "status": Literal["success"],
        "simulation_id": str,
        "result": dict[str, JsonValue],
        "privacy": str,
        "result_id": str,
    }


class GenerateChartOutput(SafeToolOutput):
    required_fields = frozenset({"status", "chart_markdown", "spec"})
    allowed_fields = required_fields
    field_types = {
        "status": Literal["success"],
        "chart_markdown": str,
        "spec": dict[str, JsonValue],
    }
