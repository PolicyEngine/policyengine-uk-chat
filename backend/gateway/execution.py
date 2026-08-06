"""Dependency-aware execution plans produced from a ready gateway verdict."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from engine.constants import UK_CHAT_DATASET
from engine.decile_concepts import DEFAULT_DECILE_CONCEPT
from gateway.assessment import ReformAssessment, ValidatedParameterBinding
from gateway.intent import ReformIntent
from gateway.policy import SlotFact
from tools.definitions import DEFAULT_SIMULATION_YEAR

OUTPUT_TO_ANALYSIS_TOOL = {
    "budgetary_impact": "compute_budgetary_impact",
    "tax_revenue": "compute_budgetary_impact",
    "benefit_spending": "compute_budgetary_impact",
    "poverty_impact": "compute_poverty_metrics",
    "inequality_impact": "compute_inequality_metrics",
    "decile_impact": "compute_decile_impacts",
    "winners_losers": "compute_winners_losers",
}

TOOL_DEPENDENCIES = {
    "compute_budgetary_impact": ("run_society_simulation",),
    "compute_program_breakdown": ("run_society_simulation",),
    "compute_decile_impacts": ("run_society_simulation",),
    "compute_winners_losers": ("run_society_simulation",),
    "compute_poverty_metrics": ("run_society_simulation",),
    "compute_inequality_metrics": ("run_society_simulation",),
    "aggregate_result": ("run_society_simulation",),
}

_SOCIETY_TOOLS = {"run_society_simulation", *TOOL_DEPENDENCIES}


@dataclass(frozen=True)
class ExecutionInput:
    name: str
    value: str
    source: Literal["prompt", "default"]


@dataclass(frozen=True)
class ExecutionConvention:
    name: str
    value: str


@dataclass(frozen=True)
class GatewayExecutionPlan:
    target_tool: str | None
    prerequisites: tuple[str, ...]
    inputs: tuple[ExecutionInput, ...]
    conventions: tuple[ExecutionConvention, ...]
    parameter_bindings: tuple[ValidatedParameterBinding, ...]
    approved_reform: dict[str, Any] | None = None


def analysis_tool_for_output(
    selected_tool: str | None,
    output: str | None,
) -> str | None:
    """Upgrade society analysis to the derivative requested by the user."""

    if selected_tool not in _SOCIETY_TOOLS:
        return selected_tool
    return OUTPUT_TO_ANALYSIS_TOOL.get(output or "", selected_tool)


def _slot_value(slots: list[SlotFact], name: str, *, kind: str | None = None) -> str | None:
    return next(
        (
            slot.value
            for slot in slots
            if slot.name == name
            and (kind is None or slot.kind == kind)
            and slot.value is not None
        ),
        None,
    )


def build_execution_plan(
    selected_tool: str | None,
    slots: list[SlotFact],
    reform_intent: ReformIntent | None,
    prompt: str,
    reform_assessment: ReformAssessment | Any | None,
) -> GatewayExecutionPlan:
    """Build an ordered, exact plan for the compute model to execute."""

    del prompt  # reserved for future deterministic convention selection
    output = _slot_value(slots, "output", kind="output")
    target = analysis_tool_for_output(selected_tool, output)
    is_society = target in _SOCIETY_TOOLS
    if is_society and reform_intent is not None and (
        reform_assessment is None or reform_assessment.reform is None
    ):
        raise ValueError("society reform execution requires a validated assessment")

    inputs = [
        ExecutionInput(slot.name, slot.value, slot.source)
        for slot in slots
        if slot.kind == "tool_input"
        and slot.source in ("prompt", "default")
        and slot.value is not None
    ]
    if is_society and not any(item.name == "year" for item in inputs):
        inputs.append(
            ExecutionInput("year", str(DEFAULT_SIMULATION_YEAR), "default")
        )

    conventions: list[ExecutionConvention] = []
    if is_society:
        conventions.extend(
            [
                ExecutionConvention("comparator", "current law"),
                ExecutionConvention("population", "full modelled population"),
                ExecutionConvention(
                    "jurisdictions",
                    "applicable modelled UK jurisdictions",
                ),
                ExecutionConvention("method", "direct static microsimulation"),
                ExecutionConvention("dataset", UK_CHAT_DATASET.label),
            ]
        )
    if target == "compute_decile_impacts":
        conventions.append(
            ExecutionConvention(
                "decile_concept",
                _slot_value(slots, "decile_concept")
                or DEFAULT_DECILE_CONCEPT.value,
            )
        )

    bindings = (
        tuple(reform_assessment.parameter_bindings)
        if reform_assessment is not None
        else ()
    )
    approved_reform = (
        dict(reform_assessment.reform)
        if reform_assessment is not None and reform_assessment.reform is not None
        else None
    )
    return GatewayExecutionPlan(
        target_tool=target,
        prerequisites=TOOL_DEPENDENCIES.get(target, ()),
        inputs=tuple(inputs),
        conventions=tuple(conventions),
        parameter_bindings=bindings,
        approved_reform=approved_reform,
    )
