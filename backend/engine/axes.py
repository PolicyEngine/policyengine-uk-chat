"""Turn-local axes simulations for illustrative synthetic households."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from numbers import Number
from typing import cast

from engine.axes_schemas import (
    AxesOutputEntities,
    AxesOutputMetadata,
    AxesSeriesAxis,
    AxesSeriesByTarget,
    AxesSeriesByVariable,
    AxesSeriesLimitError,
    AxesSeriesMetadata,
    AxesSeriesResult,
    AxesSeriesToolResult,
    AxesSimulationMetadata,
    AxesTarget,
    AxisNumber,
    AxisValue,
    HouseholdEntity,
    HouseholdInput,
    NormalizedAxis,
)
from engine.households import validate_household_dict
from engine.py_runtime import calculate_household_py, uk_model_version
from engine.serialization import json_safe
from schema_types import JsonObject, JsonValue


MIN_AXIS_POINTS = 2
MAX_AXIS_POINTS = 101
MAX_AXIS_OUTPUTS = 5
MAX_AXES_SERIES_CHARS = 12_000


@dataclass(frozen=True)
class AxesSimulationRun:
    """Selected household series retained behind a turn-local result handle."""

    household_input: HouseholdInput
    axis: NormalizedAxis
    output_entities: AxesOutputEntities
    series_by_target: AxesSeriesByTarget
    x: list[AxisNumber]

    def metadata(self) -> AxesSimulationMetadata:
        return AxesSimulationMetadata(
            status="success",
            year=self.household_input["year"],
            axis=NormalizedAxis(**self.axis),
            outputs=[
                AxesOutputMetadata(
                    name=name,
                    entity=entity,
                    entity_count=len(
                        self.series_by_target["baseline"][name]
                    ),
                )
                for name, entity in self.output_entities.items()
            ],
            targets=list(self.series_by_target),
            point_count=len(self.x),
        )

    def get_series(
        self,
        *,
        variable: str,
        target: AxesTarget = "baseline",
        index: int = 0,
    ) -> AxesSeriesToolResult:
        if target not in self.series_by_target:
            available = ", ".join(self.series_by_target)
            raise ValueError(
                f"Target {target!r} is not available; choose one of: {available}."
            )
        if variable not in self.output_entities:
            available = ", ".join(self.output_entities)
            raise ValueError(
                f"Output {variable!r} was not selected for this axes simulation; "
                f"choose one of: {available}."
            )
        if isinstance(index, bool) or not isinstance(index, int) or index < 0:
            raise ValueError("Series index must be a non-negative integer.")

        values_by_index = self.series_by_target[target][variable]
        if index >= len(values_by_index):
            raise ValueError(
                f"Series index {index} is out of range for {variable!r}; "
                f"the {self.output_entities[variable]} entity has "
                f"{len(values_by_index)} value set(s)."
            )

        result = AxesSeriesResult(
            household_input=self.household_input,
            axis=AxesSeriesAxis(
                name=self.axis["name"],
                index=self.axis["index"],
            ),
            series=AxesSeriesMetadata(
                name=variable,
                index=index,
                target=target,
            ),
            x=self.x,
            y=values_by_index[index],
        )
        result_chars = len(json.dumps(result, ensure_ascii=False, default=str))
        if result_chars > MAX_AXES_SERIES_CHARS:
            return AxesSeriesLimitError(
                error=(
                    "The complete axes series is too large to return safely "
                    f"({result_chars:,} JSON characters; limit "
                    f"{MAX_AXES_SERIES_CHARS:,})."
                ),
                detail=(
                    "No partial x/y series was returned. Run a new axes "
                    "simulation with fewer points or a smaller household input, "
                    "then call get_axes_series with the new simulation_id."
                ),
                actual_char_count=result_chars,
                max_char_count=MAX_AXES_SERIES_CHARS,
            )
        return result


def _numeric_variable(name: str) -> tuple[object, HouseholdEntity]:
    model = uk_model_version()
    variable = model.variables_by_name.get(name)
    if variable is None:
        raise ValueError(f"Unknown household variable: {name}.")

    value_type = getattr(variable, "value_type", None)
    try:
        is_numeric = (
            isinstance(value_type, type)
            and not issubclass(value_type, bool)
            and issubclass(value_type, Number)
        )
    except TypeError:
        is_numeric = False
    if not is_numeric:
        type_name = getattr(value_type, "__name__", str(value_type))
        raise ValueError(
            f"Household variable {name!r} must be numeric, not {type_name}."
        )

    entity = getattr(variable, "entity", None)
    if entity not in {"person", "benunit", "household"}:
        raise ValueError(
            f"Household variable {name!r} has unsupported entity {entity!r}."
        )
    return variable, cast(HouseholdEntity, entity)


def _entity_count(entity: HouseholdEntity, people: list[JsonObject]) -> int:
    return len(people) if entity == "person" else 1


def _finite_axis_number(field: str, value: object) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"axis.{field} must be a finite number.")
    return value


def _normalise_axis(
    axis: Mapping[str, object],
    *,
    people: list[JsonObject],
) -> tuple[NormalizedAxis, HouseholdEntity]:
    if not isinstance(axis, Mapping):
        raise ValueError("axis must be an object.")

    allowed = {"name", "index", "min", "max", "count"}
    unexpected = sorted(set(axis) - allowed)
    if unexpected:
        raise ValueError(f"axis contains unsupported fields: {unexpected}.")

    name = axis.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("axis.name must be a non-empty string.")
    _, entity = _numeric_variable(name)

    index = axis.get("index", 0)
    if isinstance(index, bool) or not isinstance(index, int) or index < 0:
        raise ValueError("axis.index must be a non-negative integer.")
    entity_count = _entity_count(entity, people)
    if index >= entity_count:
        raise ValueError(
            f"axis.index {index} is out of range for {name!r}; "
            f"the {entity} entity has {entity_count} member(s)."
        )

    lower = _finite_axis_number("min", axis.get("min"))
    upper = _finite_axis_number("max", axis.get("max"))
    if lower >= upper:
        raise ValueError("axis.min must be less than axis.max.")

    count = axis.get("count")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or not MIN_AXIS_POINTS <= count <= MAX_AXIS_POINTS
    ):
        raise ValueError(
            f"axis.count must be an integer from {MIN_AXIS_POINTS} "
            f"through {MAX_AXIS_POINTS}."
        )

    return NormalizedAxis(
        name=name,
        index=index,
        min=lower,
        max=upper,
        count=count,
    ), entity


def _normalise_outputs(
    outputs: list[str],
) -> AxesOutputEntities:
    if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_AXIS_OUTPUTS:
        raise ValueError(
            f"outputs must contain between 1 and {MAX_AXIS_OUTPUTS} variables."
        )
    if any(not isinstance(name, str) or not name for name in outputs):
        raise ValueError("outputs must contain non-empty variable names.")
    if len(set(outputs)) != len(outputs):
        raise ValueError("outputs must not contain duplicate variables.")
    return {name: _numeric_variable(name)[1] for name in outputs}


def _extract_series(
    result: JsonObject,
    *,
    variable: str,
    entity: HouseholdEntity,
    index: int,
) -> list[AxisValue]:
    if entity == "person":
        rows = result.get("person", [])
        if not isinstance(rows, list) or index >= len(rows):
            raise ValueError(
                f"Calculator returned no person index {index} for {variable!r}."
            )
        row = rows[index]
        values = row.get(variable) if isinstance(row, dict) else None
    else:
        if index != 0:
            raise ValueError(f"{entity} variables only support index 0.")
        entity_result = result.get(entity, {})
        values = (
            entity_result.get(variable)
            if isinstance(entity_result, dict)
            else None
        )

    if not isinstance(values, list):
        raise ValueError(
            f"Calculator did not return an axes series for {variable!r}."
        )
    if any(
        value is not None
        and (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
        )
        for value in values
    ):
        raise ValueError(
            f"Calculator returned a non-numeric axes value for {variable!r}."
        )
    return cast(list[AxisValue], values)


def _selected_series(
    result: JsonObject,
    *,
    output_entities: AxesOutputEntities,
    people: list[JsonObject],
) -> AxesSeriesByVariable:
    return {
        name: [
            _extract_series(
                result,
                variable=name,
                entity=entity,
                index=index,
            )
            for index in range(_entity_count(entity, people))
        ]
        for name, entity in output_entities.items()
    }


def _axis_coordinates(values: list[AxisValue]) -> list[AxisNumber]:
    if any(value is None for value in values):
        raise ValueError("Calculator returned a null axis coordinate.")
    return cast(list[AxisNumber], values)


def _json_object(value: object) -> JsonObject:
    """Serialize a calculator result and assert its object root."""

    serialized = json_safe(value)
    if not isinstance(serialized, dict):
        raise TypeError("Axes calculator results must serialize to an object.")
    return cast(JsonObject, serialized)


def build_axes_simulation(
    *,
    people: list[JsonObject],
    benunit: JsonObject | None,
    household: JsonObject | None,
    year: int,
    reform: Mapping[str, JsonValue] | None,
    axis: Mapping[str, object],
    outputs: list[str],
) -> AxesSimulationRun:
    """Run one numeric household axis and retain only selected output series."""

    people_input = [dict(person) for person in people]
    benunit_input = dict(benunit or {})
    household_entity_input = dict(household or {})
    normalized_axis, axis_entity = _normalise_axis(axis, people=people_input)
    output_entities = _normalise_outputs(outputs)

    extra_variables = list(dict.fromkeys([normalized_axis["name"], *outputs]))
    validation = validate_household_dict(
        people=people_input,
        benunit=benunit_input,
        household=household_entity_input,
        year=year,
        reform=reform,
        extra_variables=extra_variables,
    )
    if not validation.get("valid"):
        errors = validation.get("errors") or []
        message = errors[0].get("message") if errors else "Invalid household input."
        raise ValueError(message)

    effective_year = validation["year"]
    normalized_reform = validation["normalized_reform"]
    calculation_kwargs = {
        "people": people_input,
        "benunit": benunit_input,
        "household": household_entity_input,
        "year": effective_year,
        "extra_variables": extra_variables,
        "axes": [normalized_axis],
    }
    baseline = _json_object(calculate_household_py(**calculation_kwargs))
    targets: AxesSeriesByTarget = {
        "baseline": _selected_series(
            baseline,
            output_entities=output_entities,
            people=people_input,
        )
    }
    if normalized_reform:
        reformed = _json_object(
            calculate_household_py(
                **calculation_kwargs,
                reform=normalized_reform,
            )
        )
        targets["reform"] = _selected_series(
            reformed,
            output_entities=output_entities,
            people=people_input,
        )

    x = _axis_coordinates(
        _extract_series(
            baseline,
            variable=normalized_axis["name"],
            entity=axis_entity,
            index=normalized_axis["index"],
        )
    )
    expected_count = normalized_axis["count"]
    all_series = [
        x,
        *[
            values
            for target_series in targets.values()
            for variable_series in target_series.values()
            for values in variable_series
        ],
    ]
    if any(len(values) != expected_count for values in all_series):
        raise ValueError(
            "Calculator returned an axes series with an unexpected point count."
        )

    return AxesSimulationRun(
        household_input=HouseholdInput(
            people=people_input,
            benunit=benunit_input,
            household=household_entity_input,
            year=effective_year,
        ),
        axis=normalized_axis,
        output_entities=output_entities,
        series_by_target=targets,
        x=x,
    )
