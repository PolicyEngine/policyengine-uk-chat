"""Gateway policy: per-slot criticality and the deterministic gate.

The chat gateway splits responsibility deliberately: the *model grounds*, the
*server gates*. The gateway model call emits, per plan slot, only a value and a
``source`` (the grounding flag: ``prompt`` / ``default`` / ``assumed``). It does
NOT judge importance. This module owns the importance policy — a per-slot
``criticality`` — and the pure, deterministic ``gate()`` that turns a grounded
plan into one of five outcomes. Keeping this out of the model (and out of
``prompts/``/``chat/``) makes the gate auditable and unit-testable
offline, and lets the eval grader import it without dragging in the runtime.

Before gating, the server completes the selected tool's schema slots that the
model omitted as ``assumed``. A slot *gates* (forces a clarifying question) iff
its ``source`` is ``assumed`` AND its criticality is high/medium AND it is not
model-inferable. ``needs_plan`` iff any slot gates; otherwise ``ready`` (once
admissibility — irrelevant / out_of_scope / partial — is decided).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import List, Literal, Optional

from tools.definitions import DEFAULT_SIMULATION_YEAR, TOOL_DEFINITIONS

# The default simulation year as a string, for comparison against years parsed
# out of the prompt. Sourced from the single shared constant (which also feeds
# YEAR_SCHEMA) so it can't drift from the schema default. Only used to decide
# whether the prompt names a *non-default* year.
_DEFAULT_YEAR = str(DEFAULT_SIMULATION_YEAR)

Criticality = Literal["high", "medium", "low"]
SlotSource = Literal["prompt", "default", "assumed", "runtime"]
GatingReasonCode = Literal[
    "missing_tool",
    "missing_reform",
    "missing_output",
    "missing_household_composition",
    "catalogue_choice",
    "catalogue_no_match",
    "confirm_reform",
    "internal_slot",
]
DomainStatus = Literal["uk_or_unspecified", "explicit_non_uk", "unrelated"]
CapabilityStatus = Literal[
    "supported",
    "catalogue_uncertain",
    "explicitly_unmodellable",
]


@dataclass(frozen=True)
class DomainDecision:
    """Validated domain classification from the user's original wording."""

    status: DomainStatus = "uk_or_unspecified"
    evidence: Optional[str] = None


@dataclass(frozen=True)
class CapabilityDecision:
    """Validated statement about why the gateway could not select a tool."""

    status: CapabilityStatus = "supported"
    evidence: Optional[str] = None


@dataclass(frozen=True)
class SlotFact:
    """One slot of the execution plan, as grounded by the gateway model."""

    name: str
    source: SlotSource
    kind: str = "tool_input"  # "tool_input" | "output"
    value: Optional[str] = None


@dataclass(frozen=True)
class GatingReason:
    code: GatingReasonCode
    slot: str
    options: tuple[str, ...] = ()
    evidence: str | None = None


@dataclass(frozen=True)
class GateResult:
    outcome: str
    gating_reasons: List[GatingReason] = field(default_factory=list)

    @property
    def gating_slots(self) -> List[str]:
        return [reason.slot for reason in self.gating_reasons]


# ---------------------------------------------------------------------------
# Slot inventory, derived from TOOL_DEFINITIONS so it can't drift from the
# schemas. Each (tool, slot) is classified required / defaulted / optional.
# ---------------------------------------------------------------------------

def _build_slot_inventory() -> tuple[dict, dict]:
    requirements: dict = {}
    defaults: dict = {}
    for tool in TOOL_DEFINITIONS:
        name = tool["name"]
        schema = tool.get("input_schema", {})
        required = set(schema.get("required", []))
        props = schema.get("properties", {}) or {}
        for slot, spec in props.items():
            if slot in required:
                requirements[(name, slot)] = "required"
            elif isinstance(spec, dict) and "default" in spec:
                requirements[(name, slot)] = "defaulted"
                defaults[(name, slot)] = spec["default"]
            else:
                requirements[(name, slot)] = "optional"
    return requirements, defaults


TOOL_SLOT_REQUIREMENT, TOOL_SLOT_DEFAULTS = _build_slot_inventory()

_SOCIETY_DERIVATIVE_TOOLS = {
    "compute_budgetary_impact",
    "compute_program_breakdown",
    "compute_decile_impacts",
    "compute_winners_losers",
    "compute_poverty_metrics",
    "compute_inequality_metrics",
    "aggregate_result",
}

RUNTIME_PROVIDED_SLOTS = {
    *((tool, "simulation_id") for tool in _SOCIETY_DERIVATIVE_TOOLS),
    ("generate_chart", "result_id"),
}

# Curated overrides where the schema's required/default flags don't match the
# real importance. Kept tiny and commented to limit drift.
_CRITICALITY_OVERRIDES: dict = {
    # run_society_simulation has required=[] in the schema, but a society-wide
    # simulation with no reform is just a baseline snapshot — almost never the
    # intent. Treat the reform as load-bearing.
    ("run_society_simulation", "reform"): "high",
}

# Schema-required (or otherwise high) slots that the model can reliably INFER
# rather than ask the user about. Without this, every household calc would ask
# "which benefit unit?" — this is the primary bound on over-asking.
INFERABLE: set = {
    ("run_household_simulation", "benunit"),
    ("run_household_simulation", "household"),
    ("generate_chart", "chart_kind"),
    ("generate_chart", "title"),
    ("generate_chart", "x_field"),
    ("generate_chart", "y_fields"),
    ("generate_chart", "data"),  # comes from an upstream tool, not the user
}


def _missing_slot_fact(tool: str, name: str) -> SlotFact:
    key = (tool, name)
    if key in RUNTIME_PROVIDED_SLOTS:
        return SlotFact(name=name, source="runtime")
    if key in TOOL_SLOT_DEFAULTS:
        return SlotFact(name=name, source="default", value=str(TOOL_SLOT_DEFAULTS[key]))
    return SlotFact(name=name, source="assumed")


def complete_slots(tool: Optional[str], slots: List[SlotFact]) -> List[SlotFact]:
    """Return a complete gateway slot state for a selected tool.

    The gateway model can ground any subset of the plan. Missing schema slots
    must still reach ``gate()`` as ``assumed`` rather than being silently
    treated as known. ``output`` is a synthetic, user-requested deliverable
    slot, so it is added when the model did not name one.
    """

    completed = list(slots)
    present_tool_inputs = {
        slot.name for slot in slots if slot.kind == "tool_input"
    }
    if tool is not None:
        for candidate_tool, name in TOOL_SLOT_REQUIREMENT:
            if candidate_tool == tool and name not in present_tool_inputs:
                completed.append(_missing_slot_fact(tool, name))

    if tool is not None and not any(slot.kind == "output" for slot in slots):
        completed.append(SlotFact(name="output", source="assumed", kind="output"))
    return completed

# Closed vocabulary for the synthetic "output" (deliverable) slot. The single
# source of truth for the output labels: the gateway runtime injects these into
# the classifier prompt (via gateway_system) so the model and this module can't
# drift apart on the label set.
OUTPUT_VOCAB = (
    "budgetary_impact",
    "tax_revenue",
    "benefit_spending",
    "poverty_impact",
    "inequality_impact",
    "decile_impact",
    "winners_losers",
    "caseload",
    "marginal_rate",
    "net_income",
    "benefit_entitlement",
    "parameter_lookup",
    "reform_validity",
)

# Most safe defaults are expressed directly in a tool schema. Current law is
# also the documented baseline for a society simulation, even though a missing
# reform is represented by ``None`` rather than a JSON-schema ``default``.
_EXPLICIT_SAFE_DEFAULTS = {
    ("run_society_simulation", "reform"),
}


def normalise_slot_grounding(
    tool: Optional[str],
    slots: List[SlotFact],
) -> List[SlotFact]:
    """Reject empty or unsupported claims that a slot is already grounded."""

    normalised: list[SlotFact] = []
    for slot in slots:
        value = slot.value.strip() if isinstance(slot.value, str) else None
        if slot.kind == "output":
            output = value or slot.name
            if slot.source == "prompt" and output in OUTPUT_VOCAB:
                normalised.append(replace(slot, value=output))
            else:
                normalised.append(replace(slot, source="assumed", value=None))
            continue

        key = (tool, slot.name)
        if key in RUNTIME_PROVIDED_SLOTS:
            normalised.append(replace(slot, source="runtime", value=None))
            continue
        if key in TOOL_SLOT_DEFAULTS and slot.source != "prompt":
            normalised.append(
                replace(
                    slot,
                    source="default",
                    value=str(TOOL_SLOT_DEFAULTS[key]),
                )
            )
            continue
        has_schema_default = TOOL_SLOT_REQUIREMENT.get(key) == "defaulted"
        can_default = has_schema_default or key in _EXPLICIT_SAFE_DEFAULTS
        if slot.source == "prompt" and not value:
            normalised.append(replace(slot, source="assumed", value=None))
        elif slot.source == "default" and not can_default:
            normalised.append(replace(slot, source="assumed", value=None))
        else:
            normalised.append(replace(slot, value=value))
    return normalised


# ---------------------------------------------------------------------------
# Context promotions: raise criticality when a default would be actively wrong,
# not merely absent. Deterministic keyword scans over the prompt.
# ---------------------------------------------------------------------------

_REFORM_INTENT_KEYWORDS = (
    "reform", "raise", "cut", "increase", "decrease", "abolish", "scrap",
    "introduce", "freeze", "uprate", "replace", "change the", "set the",
)


def _prompt_names_reform(prompt: str) -> bool:
    p = prompt.lower()
    return any(kw in p for kw in _REFORM_INTENT_KEYWORDS)


def _prompt_implies_nondefault_year(prompt: str) -> bool:
    import re

    years = re.findall(r"\b(?:19|20)\d{2}\b", prompt)
    return any(y != _DEFAULT_YEAR for y in years)


def _base_criticality(tool: Optional[str], slot: SlotFact) -> Criticality:
    if slot.kind == "output":
        return "high"
    key = (tool, slot.name)
    if key in _CRITICALITY_OVERRIDES:
        return _CRITICALITY_OVERRIDES[key]
    requirement = TOOL_SLOT_REQUIREMENT.get(key)
    if requirement == "required":
        return "high"
    # defaulted, optional, or an unrecognised (possibly hallucinated) slot.
    return "low"


def criticality(tool: Optional[str], slot: SlotFact, prompt: str = "") -> Criticality:
    """Resolved criticality for a slot: static base + context promotions."""
    base = _base_criticality(tool, slot)
    if slot.kind == "output":
        return base
    if slot.name == "reform" and _prompt_names_reform(prompt):
        return "high"
    if slot.name == "year" and base == "low" and _prompt_implies_nondefault_year(prompt):
        return "medium"
    return base


def is_inferable(tool: Optional[str], slot_name: str) -> bool:
    return (tool, slot_name) in INFERABLE


def slot_gates(tool: Optional[str], slot: SlotFact, prompt: str = "") -> bool:
    """True if this slot should force a clarifying question."""
    if slot.source != "assumed":
        return False
    if is_inferable(tool, slot.name):
        return False
    return criticality(tool, slot, prompt) in ("high", "medium")


def _gating_reason(slot: SlotFact) -> GatingReason:
    if slot.kind == "output":
        return GatingReason(code="missing_output", slot=slot.name)
    if slot.name == "reform":
        return GatingReason(code="missing_reform", slot=slot.name)
    if slot.name in {"people", "benunit", "household"}:
        return GatingReason(code="missing_household_composition", slot=slot.name)
    return GatingReason(code="internal_slot", slot=slot.name)


def gate(
    in_domain: bool,
    tool: Optional[str],
    slots: List[SlotFact],
    unmodellable_outputs: List[str],
    prompt: str = "",
    *,
    explicitly_unmodellable: bool = False,
) -> GateResult:
    """Deterministically map a grounded plan to one of the five outcomes.

    Admissibility is decided first (irrelevant / out_of_scope / partial); only an
    admissible, fully-grounded plan reaches ``ready``. Fail-safe directions are
    the caller's job (run_gateway defaults to ``ready`` on any error); this
    function is pure.
    """
    if not in_domain:
        return GateResult("irrelevant")

    if tool is None:
        if unmodellable_outputs or explicitly_unmodellable:
            # A refusal needs positive, prompt-grounded capability evidence.
            return GateResult("out_of_scope")
        # Failure to choose a tool is uncertainty, not proof of incapability.
        return GateResult(
            "needs_plan",
            [GatingReason(code="missing_tool", slot="tool")],
        )
    if unmodellable_outputs:
        # Some of the ask is modellable, some isn't → confirm-first partial.
        # Deliberately takes precedence over needs_plan: resolve scope first;
        # under-specified inputs get clarified on the next turn if the user
        # proceeds. Both are lightweight, so neither wrongly refuses.
        return GateResult("partial")

    gating = [_gating_reason(s) for s in slots if slot_gates(tool, s, prompt)]
    if gating:
        return GateResult("needs_plan", gating)
    return GateResult("ready")
