"""Gateway runtime: turn a user message into a grounded execution plan.

One cheap forced-tool call asks a fast model to fill an execution plan (which
tool, which slots, each tagged with a grounding `source`). The deterministic
`gate()` in `gateway.policy` then maps that plan to one of five outcomes. Only
`ready` runs the full compute loop; the other four reply on the lean lightweight
path. Fail-safe to `ready`/compute on any error, matching the chat's existing
"when in doubt, load the full background" bias.

Self-contained (builds its own sync client + system prompt) so the eval harness
can import and call `run_gateway` directly, mirroring the old `_route_scope`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace
from typing import List, Optional

from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_sync_client
from gateway.catalogue import (
    MAX_CATALOGUE_QUERIES,
    CatalogueEvidence,
    CatalogueQuery,
    resolve_catalogue_queries,
)
from gateway.policy import (
    CapabilityDecision,
    DomainDecision,
    OUTPUT_VOCAB,
    SlotFact,
    complete_slots,
    gate,
    normalise_slot_grounding,
)
from prompts import (
    DEFAULT_SCOPE_DESCRIPTOR,
    GATEWAY_IRRELEVANT_DIRECTIVE,
    GATEWAY_NEEDS_PLAN_DIRECTIVE,
    GATEWAY_OUT_OF_SCOPE_DIRECTIVE,
    GATEWAY_PARTIAL_CATALOGUE_DIRECTIVE,
    GATEWAY_PARTIAL_DIRECTIVE,
    gateway_system,
)
from tools.definitions import DEFAULT_SIMULATION_YEAR, TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

GATEWAY_MODEL = os.environ.get("POLICYENGINE_CHAT_GATEWAY_MODEL", DEFAULT_FAST_MODEL)
GATEWAY_MAX_TOKENS = int(os.environ.get("POLICYENGINE_CHAT_GATEWAY_MAX_TOKENS", "1024"))
MAX_UNMODELLABLE_OUTPUTS = 4

_TOOL_NAMES = [t["name"] for t in TOOL_DEFINITIONS]


def _build_tool_summary() -> str:
    """One line per tool (name — purpose; required params), derived from the
    tool schemas so it can't drift."""
    lines = []
    for t in TOOL_DEFINITIONS:
        required = t.get("input_schema", {}).get("required", []) or []
        purpose = (t.get("description") or "").strip().split(". ")[0].rstrip(".")
        req = ", ".join(required) if required else "none"
        lines.append(f"- {t['name']} — {purpose}. Required: {req}.")
    return "\n".join(lines)


TOOL_SUMMARY = _build_tool_summary()
SCOPE_DESCRIPTOR = DEFAULT_SCOPE_DESCRIPTOR
GATEWAY_SYSTEM = gateway_system(
    SCOPE_DESCRIPTOR, TOOL_SUMMARY, ", ".join(OUTPUT_VOCAB), DEFAULT_SIMULATION_YEAR
)


@dataclass
class GatewayVerdict:
    outcome: str
    route: str  # "compute" if outcome == "ready" else "lightweight"
    tool: Optional[str] = None
    slots: List[SlotFact] = field(default_factory=list)
    gating_slots: List[str] = field(default_factory=list)
    unmodellable_outputs: List[str] = field(default_factory=list)
    catalogue_evidence: CatalogueEvidence | None = None
    domain: DomainDecision = field(default_factory=DomainDecision)
    capability: CapabilityDecision = field(default_factory=CapabilityDecision)


def _fail_safe() -> GatewayVerdict:
    """The safe default: behave exactly like today (full compute background)."""
    return GatewayVerdict(outcome="ready", route="compute")


# Forced-use tool that carries the structured plan. Local to the gateway — must
# NOT be added to TOOL_DEFINITIONS or it would leak into the compute loop.
_EMIT_PLAN_TOOL = {
    "name": "emit_plan",
    "description": "Emit the structured execution plan for the user's message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "domain": {
                "type": "object",
                "description": (
                    "Whether the request is UK tax-benefit work, explicitly "
                    "non-UK, or unrelated. Negative decisions require an exact "
                    "quote from the user's message."
                ),
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "uk_or_unspecified",
                            "explicit_non_uk",
                            "unrelated",
                        ],
                    },
                    "evidence": {
                        "type": "string",
                        "maxLength": 300,
                        "description": (
                            "An exact quote supporting explicit_non_uk or "
                            "unrelated; omit for uk_or_unspecified."
                        ),
                    },
                },
                "required": ["status"],
            },
            "capability": {
                "type": "object",
                "description": (
                    "Whether the requested work is supported, needs catalogue "
                    "confirmation, or explicitly asks only for an unmodellable "
                    "effect. Non-supported decisions require an exact quote."
                ),
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": [
                            "supported",
                            "catalogue_uncertain",
                            "explicitly_unmodellable",
                        ],
                    },
                    "evidence": {
                        "type": "string",
                        "maxLength": 300,
                        "description": (
                            "An exact quote supporting catalogue_uncertain or "
                            "explicitly_unmodellable; omit for supported."
                        ),
                    },
                },
                "required": ["status"],
            },
            "tool": {
                "type": "string",
                "enum": _TOOL_NAMES + ["none"],
                "description": "Best-fitting tool for the modelled part, or 'none'.",
            },
            "slots": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "kind": {"type": "string", "enum": ["tool_input", "output"]},
                        "value": {"type": "string"},
                        "source": {"type": "string", "enum": ["prompt", "default", "assumed"]},
                    },
                    "required": ["name", "kind", "source"],
                },
            },
            "unmodellable_outputs": {
                "type": "array",
                "maxItems": MAX_UNMODELLABLE_OUTPUTS,
                "description": (
                    "Outputs explicitly requested by the user that the tool chain "
                    "cannot calculate. Every item must cite an exact quote from "
                    "the user's message that requests that output."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "maxLength": 100,
                            "description": "Concise name of the unmodellable output.",
                        },
                        "evidence": {
                            "type": "string",
                            "maxLength": 300,
                            "description": (
                                "A short exact quote from the user's message that "
                                "explicitly requests this output."
                            ),
                        },
                    },
                    "required": ["name", "evidence"],
                },
            },
            "catalogue_queries": {
                "type": "array",
                "maxItems": MAX_CATALOGUE_QUERIES,
                "description": (
                    "Short policyengine.py catalogue searches for named reform "
                    "measures or variable concepts. Every query must cite an "
                    "exact quote from the user's message containing the query. "
                    "Use an empty list when no catalogue concept is named."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["reform_target", "variable"]},
                        "query": {"type": "string"},
                        "evidence": {
                            "type": "string",
                            "maxLength": 300,
                            "description": (
                                "An exact quote from the user's message that "
                                "contains this catalogue search term."
                            ),
                        },
                    },
                    "required": ["kind", "query", "evidence"],
                },
            },
            "rationale": {"type": "string"},
        },
        "required": [
            "domain",
            "capability",
            "tool",
            "slots",
            "catalogue_queries",
        ],
    },
}


def _catalogue_queries_from_plan(
    plan: dict,
    prompt: str,
) -> tuple[CatalogueQuery, ...]:
    """Accept only catalogue searches grounded in an exact user quote."""

    queries: list[CatalogueQuery] = []
    for item in plan.get("catalogue_queries") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        query = item.get("query")
        if kind not in ("reform_target", "variable") or not isinstance(query, str):
            continue
        query = query.strip()
        evidence = _validated_quote(item.get("evidence"), prompt)
        if (
            not query
            or evidence is None
            or _normalise_evidence_text(query)
            not in _normalise_evidence_text(evidence)
        ):
            continue
        queries.append(CatalogueQuery(kind, query, evidence))
    return tuple(queries)


def _normalise_evidence_text(value: str) -> str:
    """Normalise case and whitespace while preserving phrase boundaries."""

    return " ".join(value.casefold().split())


def _validated_quote(value: object, prompt: str) -> str | None:
    """Return a prompt-grounded quote, or ``None`` for invented evidence."""

    if not isinstance(value, str):
        return None
    quote = value.strip()
    quote_text = _normalise_evidence_text(quote)
    if not quote_text or quote_text not in _normalise_evidence_text(prompt):
        return None
    return quote


def _domain_from_plan(plan: dict, prompt: str) -> DomainDecision:
    raw = plan.get("domain")
    if not isinstance(raw, dict):
        return DomainDecision()
    status = raw.get("status")
    if status == "uk_or_unspecified":
        return DomainDecision()
    if status not in ("explicit_non_uk", "unrelated"):
        return DomainDecision()
    evidence = _validated_quote(raw.get("evidence"), prompt)
    if evidence is None:
        return DomainDecision()
    return DomainDecision(status=status, evidence=evidence)


def _capability_from_plan(plan: dict, prompt: str) -> CapabilityDecision:
    raw = plan.get("capability")
    if not isinstance(raw, dict):
        return CapabilityDecision()
    status = raw.get("status")
    if status == "supported":
        return CapabilityDecision()
    if status not in ("catalogue_uncertain", "explicitly_unmodellable"):
        return CapabilityDecision()
    evidence = _validated_quote(raw.get("evidence"), prompt)
    if evidence is None:
        return CapabilityDecision()
    return CapabilityDecision(status=status, evidence=evidence)


def _unmodellable_outputs_from_plan(plan: dict, prompt: str) -> list[str]:
    """Accept only limitations backed by an exact phrase from the user.

    The classifier can explain capability boundaries, but it cannot promote a
    merely possible behavioural or macroeconomic caveat into a requested output.
    Requiring quoted prompt evidence keeps that distinction deterministic.
    """

    prompt_text = _normalise_evidence_text(prompt)
    outputs: list[str] = []
    seen: set[str] = set()
    raw_outputs = plan.get("unmodellable_outputs")
    if not isinstance(raw_outputs, list):
        return outputs

    for item in raw_outputs:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        evidence = item.get("evidence")
        if not isinstance(name, str) or not isinstance(evidence, str):
            continue
        name = name.strip()
        evidence_text = _normalise_evidence_text(evidence)
        key = name.casefold()
        if (
            not name
            or not evidence_text
            or evidence_text not in prompt_text
            or key in seen
        ):
            continue
        seen.add(key)
        outputs.append(name)
        if len(outputs) == MAX_UNMODELLABLE_OUTPUTS:
            break
    return outputs


def apply_catalogue_evidence(
    verdict: GatewayVerdict,
    evidence: CatalogueEvidence,
) -> GatewayVerdict:
    """Combine deterministic catalogue evidence with the model-grounded plan.

    Evidence confirms only modelability. It cannot turn an under-specified or
    partial request into an executable one, so those outcomes are preserved.
    """

    verdict = replace(verdict, catalogue_evidence=evidence)
    if verdict.outcome == "irrelevant":
        return verdict
    if not evidence.available:
        if verdict.outcome == "out_of_scope":
            return replace(verdict, outcome="ready", route="compute")
        return verdict
    if evidence.unresolved_queries:
        gating_slots = list(dict.fromkeys([*verdict.gating_slots, "model_catalogue"]))
        if verdict.outcome in ("needs_plan", "partial"):
            return replace(verdict, gating_slots=gating_slots)
        return replace(
            verdict,
            outcome="needs_plan",
            route="lightweight",
            gating_slots=gating_slots,
        )
    catalogue_can_supply_missing_tool = (
        verdict.outcome == "needs_plan"
        and verdict.tool is None
        and verdict.gating_slots == ["tool"]
    )
    if evidence.authoritative_matches and (
        verdict.outcome == "out_of_scope" or catalogue_can_supply_missing_tool
    ):
        return replace(verdict, outcome="ready", route="compute")
    return verdict


def _verdict_from_plan(
    plan: dict,
    prompt: str,
    catalogue_evidence: CatalogueEvidence,
) -> GatewayVerdict:
    """Build a server-gated verdict from the model's grounded plan. The model's
    own outcome is never trusted — the outcome is recomputed by gate()."""
    domain = _domain_from_plan(plan, prompt)
    capability = _capability_from_plan(plan, prompt)
    in_domain = domain.status == "uk_or_unspecified"
    raw_tool = plan.get("tool")
    tool = raw_tool if raw_tool in _TOOL_NAMES else None

    slots: List[SlotFact] = []
    for s in plan.get("slots") or []:
        if not isinstance(s, dict) or "name" not in s:
            continue
        source = s.get("source", "assumed")
        if source not in ("prompt", "default", "assumed"):
            source = "assumed"
        kind = s.get("kind", "tool_input")
        if kind not in ("tool_input", "output"):
            kind = "tool_input"
        value = s.get("value")
        slots.append(
            SlotFact(
                name=str(s["name"]),
                source=source,
                kind=kind,
                value=value if isinstance(value, str) else None,
            )
        )

    slots = normalise_slot_grounding(tool, slots)
    slots = complete_slots(tool, slots)
    unmodellable = _unmodellable_outputs_from_plan(plan, prompt)
    result = gate(
        in_domain,
        tool,
        slots,
        unmodellable,
        prompt,
        explicitly_unmodellable=(
            capability.status == "explicitly_unmodellable"
        ),
    )
    verdict = GatewayVerdict(
        outcome=result.outcome,
        route="compute" if result.outcome == "ready" else "lightweight",
        tool=tool,
        slots=slots,
        gating_slots=result.gating_slots,
        unmodellable_outputs=unmodellable,
        domain=domain,
        capability=capability,
    )
    return apply_catalogue_evidence(verdict, catalogue_evidence)


def run_gateway(last_user_message: str) -> GatewayVerdict:
    """One cheap forced-tool call → grounded plan → server-gated verdict.

    Fail-safe to ready/compute on empty input, any API error, a missing plan
    block, or an unparseable plan.
    """
    if not last_user_message or not last_user_message.strip():
        return _fail_safe()
    try:
        client = get_sync_client()
        response = client.messages.create(
            model=GATEWAY_MODEL,
            max_tokens=GATEWAY_MAX_TOKENS,
            temperature=DEFAULT_TEMPERATURE,
            system=GATEWAY_SYSTEM,
            tools=[_EMIT_PLAN_TOOL],
            tool_choice={"type": "tool", "name": "emit_plan"},
            messages=[{"role": "user", "content": last_user_message[:4000]}],
        )
        plan = None
        for block in response.content or []:
            if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "emit_plan":
                plan = block.input if isinstance(block.input, dict) else {}
                break
        # No plan block, an empty plan, or one missing the routing decision is a
        # parse failure — fall back to compute. A real refusal (`tool: "none"`,
        # `in_domain: false`) is a well-formed plan and is NOT caught here, so a
        # degenerate response can never masquerade as an out_of_scope refusal.
        if not plan or "tool" not in plan:
            return _fail_safe()
        catalogue_evidence = resolve_catalogue_queries(
            _catalogue_queries_from_plan(plan, last_user_message)
        )
        return _verdict_from_plan(plan, last_user_message, catalogue_evidence)
    except Exception as e:  # noqa: BLE001 — any failure falls back to full compute
        logger.warning(f"[GATEWAY] failed; defaulting to ready/compute: {e}")
        return _fail_safe()


_WRITER_DIRECTIVES = {
    "irrelevant": GATEWAY_IRRELEVANT_DIRECTIVE,
    "out_of_scope": GATEWAY_OUT_OF_SCOPE_DIRECTIVE,
    "partial": GATEWAY_PARTIAL_DIRECTIVE,
    "needs_plan": GATEWAY_NEEDS_PLAN_DIRECTIVE,
}


def gateway_writer_directive(verdict: GatewayVerdict) -> str:
    """Per-outcome directive plus concrete facts, appended to the lightweight
    system for the single no-tool reply turn on a non-`ready` outcome."""
    evidence = verdict.catalogue_evidence
    if verdict.outcome == "partial" and evidence and evidence.unresolved_queries:
        directive = GATEWAY_PARTIAL_CATALOGUE_DIRECTIVE
    else:
        directive = _WRITER_DIRECTIVES.get(verdict.outcome)
    if directive is None:
        return ""
    parts = [directive]
    if verdict.outcome == "partial" and verdict.unmodellable_outputs:
        parts.append("Cannot model: " + ", ".join(verdict.unmodellable_outputs) + ".")
    if verdict.outcome == "needs_plan" and verdict.gating_slots:
        parts.append("Under-specified points to clarify: " + ", ".join(verdict.gating_slots) + ".")
    if evidence and evidence.unresolved_queries:
        queries = ", ".join(query.query for query in evidence.unresolved_queries)
        parts.append(
            "The current model catalogue did not resolve: "
            + queries
            + ". Ask what supported policy measure or variable the user means; "
            "do not say that it is unmodelled."
        )
    elif evidence and evidence.matches and verdict.outcome == "needs_plan":
        labels = ", ".join(dict.fromkeys(match.label for match in evidence.matches))
        parts.append(
            "Relevant model catalogue candidates (use labels, not internal paths): "
            + labels
            + "."
        )
    return "\n\n".join(parts)


def serialise_plan_for_system(verdict: GatewayVerdict) -> str:
    """Compact plan text injected into the compute system blocks for `ready`, so
    the heavy model starts from the resolved tool + grounded args."""
    grounded = [
        f"{s.name}={s.value}"
        for s in verdict.slots
        if s.value and s.source in ("prompt", "default")
    ]
    lines = []
    if verdict.tool is not None:
        lines.append(f"GATEWAY PLAN (pre-resolved by a routing pass): tool={verdict.tool}.")
    if grounded:
        lines.append("Resolved inputs: " + "; ".join(grounded) + ".")
    evidence = verdict.catalogue_evidence
    if evidence and evidence.authoritative_matches:
        lines.append("MODEL CATALOGUE EVIDENCE (verified current policyengine.py candidates):")
        lines.extend(
            f"- {match.kind}: {match.label} (`{match.identifier}`)"
            for match in evidence.authoritative_matches
        )
        lines.append(
            "Treat these as discovery candidates, not a resolution of user intent. "
            "Use them internally and ask a concise clarification where needed."
        )
    if not lines:
        return ""
    lines.append("Treat this as a starting point and verify it against the user's message.")
    return "\n".join(lines)
