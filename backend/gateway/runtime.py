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
from gateway.catalogue import CatalogueEvidence, CatalogueQuery, resolve_catalogue_queries
from gateway.policy import OUTPUT_VOCAB, SlotFact, gate
from prompts import (
    DEFAULT_SCOPE_DESCRIPTOR,
    GATEWAY_IRRELEVANT_DIRECTIVE,
    GATEWAY_NEEDS_PLAN_DIRECTIVE,
    GATEWAY_OUT_OF_SCOPE_DIRECTIVE,
    GATEWAY_PARTIAL_DIRECTIVE,
    gateway_system,
)
from tools.definitions import DEFAULT_SIMULATION_YEAR, TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

GATEWAY_MODEL = os.environ.get("POLICYENGINE_CHAT_GATEWAY_MODEL", DEFAULT_FAST_MODEL)
GATEWAY_MAX_TOKENS = int(os.environ.get("POLICYENGINE_CHAT_GATEWAY_MAX_TOKENS", "1024"))

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
            "in_domain": {
                "type": "boolean",
                "description": "Is the message about UK tax or benefit policy at all?",
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
            "unmodellable_outputs": {"type": "array", "items": {"type": "string"}},
            "catalogue_queries": {
                "type": "array",
                "maxItems": 4,
                "description": (
                    "Short policyengine.py catalogue searches for named reform "
                    "measures or variable concepts. Use an empty list when no "
                    "catalogue concept is named."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["reform_target", "variable"]},
                        "query": {"type": "string"},
                    },
                    "required": ["kind", "query"],
                },
            },
            "rationale": {"type": "string"},
        },
        "required": ["in_domain", "tool", "slots", "catalogue_queries"],
    },
}


def _catalogue_queries_from_plan(plan: dict) -> tuple[CatalogueQuery, ...]:
    queries: list[CatalogueQuery] = []
    for item in plan.get("catalogue_queries") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        query = item.get("query")
        if kind not in ("reform_target", "variable") or not isinstance(query, str):
            continue
        queries.append(CatalogueQuery(kind, query))
    return tuple(queries)


def apply_catalogue_evidence(
    verdict: GatewayVerdict,
    evidence: CatalogueEvidence,
) -> GatewayVerdict:
    """Combine deterministic catalogue evidence with the model-grounded plan.

    Evidence confirms only modelability. It cannot turn an under-specified or
    partial request into an executable one, so those outcomes are preserved.
    """

    verdict = replace(verdict, catalogue_evidence=evidence)
    if not evidence.available:
        return replace(verdict, outcome="ready", route="compute")
    if evidence.unresolved_queries:
        gating_slots = list(dict.fromkeys([*verdict.gating_slots, "model_catalogue"]))
        return replace(
            verdict,
            outcome="needs_plan",
            route="lightweight",
            gating_slots=gating_slots,
        )
    if evidence.matches and verdict.outcome in ("irrelevant", "out_of_scope"):
        return replace(verdict, outcome="ready", route="compute")
    return verdict


def _verdict_from_plan(
    plan: dict,
    prompt: str,
    catalogue_evidence: CatalogueEvidence,
) -> GatewayVerdict:
    """Build a server-gated verdict from the model's grounded plan. The model's
    own outcome is never trusted — the outcome is recomputed by gate()."""
    in_domain = bool(plan.get("in_domain", True))
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
        slots.append(SlotFact(name=str(s["name"]), source=source, kind=kind, value=s.get("value")))

    unmodellable = [str(x) for x in (plan.get("unmodellable_outputs") or []) if x]
    result = gate(in_domain, tool, slots, unmodellable, prompt)
    verdict = GatewayVerdict(
        outcome=result.outcome,
        route="compute" if result.outcome == "ready" else "lightweight",
        tool=tool,
        slots=slots,
        gating_slots=result.gating_slots,
        unmodellable_outputs=unmodellable,
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
        catalogue_evidence = resolve_catalogue_queries(_catalogue_queries_from_plan(plan))
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
    directive = _WRITER_DIRECTIVES.get(verdict.outcome)
    if directive is None:
        return ""
    parts = [directive]
    if verdict.outcome == "partial" and verdict.unmodellable_outputs:
        parts.append("Cannot model: " + ", ".join(verdict.unmodellable_outputs) + ".")
    if verdict.outcome == "needs_plan" and verdict.gating_slots:
        parts.append("Under-specified points to clarify: " + ", ".join(verdict.gating_slots) + ".")
    evidence = verdict.catalogue_evidence
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
    if evidence and evidence.matches:
        lines.append("MODEL CATALOGUE EVIDENCE (verified current policyengine.py candidates):")
        lines.extend(
            f"- {match.kind}: {match.label} (`{match.identifier}`)"
            for match in evidence.matches
        )
        lines.append(
            "Treat these as discovery candidates, not a resolution of user intent. "
            "Use them internally and ask a concise clarification where needed."
        )
    if not lines:
        return ""
    lines.append("Treat this as a starting point and verify it against the user's message.")
    return "\n".join(lines)
