"""Request-local reportable facts and deterministic numeric formatting."""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Any, Iterable

from analysis.common import stable_identifier
from analysis.models import Fact, FactRegister, SemanticRequestRevision


REPORTABLE_OPERATIONS = frozenset(
    {
        "get_parameter",
        "run_household_simulation",
        "run_society_simulation",
        "compute_budgetary_impact",
        "compute_program_breakdown",
        "compute_decile_impacts",
        "compute_winners_losers",
        "compute_poverty_metrics",
        "compute_inequality_metrics",
        "aggregate_result",
        "generate_chart",
    }
)

# These are structural values rather than calculated policy outcomes. A
# narrator may use them only through an explicitly named literal segment.
APPROVED_NON_RESULT_NUMERIC_FIELDS = frozenset(
    {"year", "person_count", "maximum_iterations", "maximum_operation_calls"}
)


def _value(
    revision: SemanticRequestRevision,
    name: str,
    default: Any = None,
) -> Any:
    field = revision.fields.get(name)
    return field.value if field is not None else default


def _unit(path: str) -> str:
    lowered = path.casefold()
    if any(token in lowered for token in ("rate", "relative", "percent", "share")):
        return "percent"
    if any(
        token in lowered
        for token in (
            "income",
            "revenue",
            "spending",
            "budget",
            "amount",
            "cost",
            "tax",
            "benefit",
        )
    ):
        return "GBP"
    if any(token in lowered for token in ("count", "caseload", "people", "households")):
        return "count"
    return "number"


def display_value(value: int | float, unit: str) -> str:
    if unit == "GBP":
        absolute = abs(float(value))
        if absolute >= 1_000_000_000:
            return f"£{value / 1_000_000_000:,.2f} billion"
        if absolute >= 1_000_000:
            return f"£{value / 1_000_000:,.2f} million"
        return f"£{value:,.2f}" if not float(value).is_integer() else f"£{value:,.0f}"
    if unit == "percent":
        numeric = float(value)
        if abs(numeric) <= 1:
            numeric *= 100
        return f"{numeric:,.1f}%"
    if unit == "count":
        return f"{value:,.0f}"
    return f"{value:,.2f}" if not float(value).is_integer() else f"{value:,.0f}"


def _flatten_numbers(
    value: Any,
    path: tuple[str, ...] = (),
) -> Iterator[tuple[tuple[str, ...], int | float]]:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        yield path, value
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"result_id", "simulation_id", "status"}:
                continue
            yield from _flatten_numbers(item, (*path, str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _flatten_numbers(item, (*path, str(index + 1)))


def _label(operation: str, path: tuple[str, ...]) -> str:
    label = " ".join(path).replace("_", " ").strip()
    return f"{operation.replace('_', ' ')}: {label}" if label else operation


def _fact(
    *,
    source_step_id: str,
    path: tuple[str, ...],
    value: int | float,
    label: str,
    caveats: tuple[str, ...] = (),
) -> Fact:
    path_text = ".".join(path)
    unit = _unit(path_text)
    return Fact(
        fact_id=stable_identifier("fact", source_step_id, path_text),
        raw_value=value,
        unit=unit,
        display_value=display_value(value, unit),
        label=label,
        source_step_id=source_step_id,
        caveats=caveats,
    )


def build_fact_register(
    *,
    revision: SemanticRequestRevision,
    operation_summaries: Iterable[dict[str, Any]],
    caveats: tuple[str, ...] = (),
) -> FactRegister:
    facts: list[Fact] = []
    year = _value(revision, "year")
    if isinstance(year, int) and not isinstance(year, bool):
        facts.append(
            _fact(
                source_step_id="request",
                path=("year",),
                value=year,
                label="Analysis year",
            )
        )
    reform = _value(revision, "reform")
    if isinstance(reform, dict):
        for path, value in reform.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                facts.append(
                    _fact(
                        source_step_id="request",
                        path=("reform", str(path)),
                        value=value,
                        label=f"Requested value for {path}",
                    )
                )

    for summary in operation_summaries:
        operation = summary.get("operation")
        step_id = summary.get("step_id")
        payload = summary.get("summary")
        if (
            operation not in REPORTABLE_OPERATIONS
            or not isinstance(step_id, str)
            or not isinstance(payload, dict)
        ):
            continue
        for path, value in _flatten_numbers(payload):
            facts.append(
                _fact(
                    source_step_id=step_id,
                    path=path,
                    value=value,
                    label=_label(operation, path),
                    caveats=caveats,
                )
            )
    unique = {fact.fact_id: fact for fact in facts}
    return FactRegister(facts=tuple(unique.values()))


def approved_non_result_values(
    revision: SemanticRequestRevision,
    *,
    plan_maximum_iterations: int | None = None,
    plan_maximum_operation_calls: int | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    year = _value(revision, "year")
    if isinstance(year, int) and not isinstance(year, bool):
        values["year"] = str(year)
    people = _value(revision, "people")
    if isinstance(people, list):
        values["person_count"] = str(len(people))
    if plan_maximum_iterations is not None:
        values["maximum_iterations"] = str(plan_maximum_iterations)
    if plan_maximum_operation_calls is not None:
        values["maximum_operation_calls"] = str(plan_maximum_operation_calls)
    assert set(values).issubset(APPROVED_NON_RESULT_NUMERIC_FIELDS)
    return values
