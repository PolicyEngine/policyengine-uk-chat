"""Typed input, storage, and result schemas for household axes."""

from __future__ import annotations

from typing import Literal, TypeAlias, TypedDict

from schema_types import JsonObject


AxisNumber: TypeAlias = int | float
AxisValue: TypeAlias = AxisNumber | None
AxesTarget: TypeAlias = Literal["baseline", "reform"]
HouseholdEntity: TypeAlias = Literal["person", "benunit", "household"]


class _OptionalAxesInput(TypedDict, total=False):
    index: int


class AxesInput(_OptionalAxesInput):
    """Model-facing definition of one numeric input sweep."""

    name: str
    min: AxisNumber
    max: AxisNumber
    count: int


class NormalizedAxis(TypedDict):
    """Validated axis stored with every axes simulation."""

    name: str
    index: int
    min: AxisNumber
    max: AxisNumber
    count: int


class HouseholdInput(TypedDict):
    """Synthetic household fields returned with a retrieved series."""

    people: list[JsonObject]
    benunit: JsonObject
    household: JsonObject
    year: int


class AxesOutputMetadata(TypedDict):
    """One selected output advertised by the simulation handle."""

    name: str
    entity: HouseholdEntity
    entity_count: int


class AxesSimulationMetadata(TypedDict):
    """Metadata retained with an axes simulation before handle assignment."""

    status: Literal["success"]
    year: int
    axis: NormalizedAxis
    outputs: list[AxesOutputMetadata]
    targets: list[AxesTarget]
    point_count: int


class AxesSimulationResult(AxesSimulationMetadata):
    """Successful model-facing result from ``run_axes_simulation``."""

    simulation_id: str


class AxesSeriesAxis(TypedDict):
    """Axis identity repeated on a compact retrieved series."""

    name: str
    index: int


class AxesSeriesMetadata(TypedDict):
    """Identity of the selected output series."""

    name: str
    index: int
    target: AxesTarget


class AxesSeriesResult(TypedDict):
    """Complete aligned coordinates returned by ``get_axes_series``."""

    household_input: HouseholdInput
    axis: AxesSeriesAxis
    series: AxesSeriesMetadata
    x: list[AxisNumber]
    y: list[AxisValue]


class ToolError(TypedDict):
    """Standard error result returned by a model-facing tool."""

    error: str


class AxesSeriesLimitError(ToolError):
    """Error returned instead of truncating an oversized axes series."""

    detail: str
    actual_char_count: int
    max_char_count: int


class AxesChartDefaults(TypedDict):
    """Chart fields derived deterministically from axes series metadata."""

    x_field: str
    y_fields: list[str]
    x_label: str
    y_label: str


AxesSeriesByVariable: TypeAlias = dict[str, list[list[AxisValue]]]
AxesSeriesByTarget: TypeAlias = dict[AxesTarget, AxesSeriesByVariable]
AxesOutputEntities: TypeAlias = dict[str, HouseholdEntity]
AxesSeriesToolResult: TypeAlias = AxesSeriesResult | AxesSeriesLimitError
RunAxesToolResult: TypeAlias = AxesSimulationResult | ToolError
