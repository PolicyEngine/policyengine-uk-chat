"""Internal, structured observability for completed gateway decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from gateway.assessment import REFORM_RESOLVER_MODEL

if TYPE_CHECKING:
    from gateway.runtime import GatewayVerdict


@dataclass(frozen=True, slots=True)
class GatewayTraceSlot:
    name: str
    kind: str
    source: str
    value: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayTraceReason:
    code: str
    slot: str
    options: tuple[str, ...] = ()
    evidence: str | None = None


@dataclass(frozen=True, slots=True)
class GatewayTraceBinding:
    parameter_path: str
    label: str
    catalogue_evidence: str


@dataclass(frozen=True, slots=True)
class GatewayTraceAlternative:
    summary: str
    parameter_bindings: tuple[GatewayTraceBinding, ...]
    reform: dict[str, Any]


@dataclass(frozen=True, slots=True)
class GatewayTrace:
    selected_tool: str | None = None
    target_tool: str | None = None
    slots: tuple[GatewayTraceSlot, ...] = ()
    gating_reasons: tuple[GatewayTraceReason, ...] = ()
    defaults_applied: dict[str, Any] = field(default_factory=dict)
    reform_confidence: int | None = None
    reform_summary: str | None = None
    reform_search_queries: tuple[str, ...] = ()
    catalogue_version: str | None = None
    resolver_model: str | None = None
    parameter_bindings: tuple[GatewayTraceBinding, ...] = ()
    alternatives: tuple[GatewayTraceAlternative, ...] = ()
    catalogue_recovery_used: bool = False
    proposal_resumed: bool = False


def _trace_value(value: str) -> Any:
    """Retain text defaults while representing JSON scalar defaults naturally."""

    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    return parsed if isinstance(parsed, (int, float, bool)) or parsed is None else value


def _binding(value: Any) -> GatewayTraceBinding:
    return GatewayTraceBinding(
        parameter_path=value.parameter_path,
        label=value.label,
        catalogue_evidence=value.catalogue_evidence,
    )


def gateway_trace_from_verdict(
    verdict: GatewayVerdict | None,
) -> GatewayTrace | None:
    """Project a verdict into a stable internal/eval-safe trace."""

    if verdict is None:
        return None
    execution = verdict.execution_plan
    assessment = verdict.reform_assessment
    defaults = {
        slot.name: _trace_value(slot.value)
        for slot in verdict.slots
        if slot.source == "default" and slot.value is not None
    }
    if execution is not None:
        defaults.update(
            {
                item.name: _trace_value(item.value)
                for item in execution.inputs
                if item.source == "default"
            }
        )
    return GatewayTrace(
        selected_tool=verdict.tool,
        target_tool=execution.target_tool if execution is not None else None,
        slots=tuple(
            GatewayTraceSlot(
                name=slot.name,
                kind=slot.kind,
                source=slot.source,
                value=slot.value,
            )
            for slot in verdict.slots
        ),
        gating_reasons=tuple(
            GatewayTraceReason(
                code=reason.code,
                slot=reason.slot,
                options=tuple(reason.options),
                evidence=reason.evidence,
            )
            for reason in verdict.gating_reasons
        ),
        defaults_applied=defaults,
        reform_confidence=(assessment.confidence if assessment is not None else None),
        reform_summary=(assessment.summary if assessment is not None else None),
        reform_search_queries=(
            tuple(assessment.search_queries) if assessment is not None else ()
        ),
        catalogue_version=(
            assessment.catalogue_version if assessment is not None else None
        ),
        resolver_model=REFORM_RESOLVER_MODEL if assessment is not None else None,
        parameter_bindings=(
            tuple(_binding(value) for value in assessment.parameter_bindings)
            if assessment is not None
            else ()
        ),
        alternatives=(
            tuple(
                GatewayTraceAlternative(
                    summary=alternative.summary,
                    parameter_bindings=tuple(
                        _binding(value)
                        for value in alternative.parameter_bindings
                    ),
                    reform=dict(alternative.reform),
                )
                for alternative in assessment.alternatives
            )
            if assessment is not None
            else ()
        ),
        catalogue_recovery_used=verdict.catalogue_recovery_used,
        proposal_resumed=verdict.proposal_resumed,
    )
