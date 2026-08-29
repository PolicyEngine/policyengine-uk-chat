"""Strict input and JSON-safe output models for deterministic UK Chat tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, RootModel, model_validator


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
    basis: Literal["income", "wealth"] = "income"


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
    """JSON-only output that rejects common row-level provider payload fields."""

    @model_validator(mode="after")
    def exclude_row_level_data(self) -> "SafeToolOutput":
        if _contains_row_level_key(self.root):
            raise ValueError("Tool output contains row-level data.")
        return self

    def as_dict(self) -> dict[str, JsonValue]:
        return self.root
