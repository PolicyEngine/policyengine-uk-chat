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
import re
from dataclasses import dataclass, field
from typing import List, Optional

from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_sync_client
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

_UNMODELLABLE_TERM_GROUPS = (
    {"inflation", "price", "prices"},
    {"gdp", "growth", "macro", "macroeconomic"},
    {"employment", "job", "jobs", "work", "working", "labour", "labor"},
    {"behaviour", "behavioural", "behavior", "behavioral"},
    {"market", "markets"},
)
_GENERIC_UNMODELLABLE_TERMS = {
    "change",
    "changes",
    "effect",
    "effects",
    "impact",
    "impacts",
    "response",
    "responses",
}


def _tokens(value: str) -> set[str]:
    return set(re.findall(r"[a-z]+", value.lower().replace("_", " ")))


def _requested_unmodellable_outputs(
    prompt: str,
    outputs: list[str],
    *,
    has_modellable_tool: bool,
) -> list[str]:
    """Keep only explicitly requested riders on otherwise modellable asks.

    A no-tool refusal retains the classifier's reasons for the lightweight
    writer. When a modellable tool exists, an invented generic caveat must not
    turn a ready or under-specified request into ``partial``.
    """
    if not has_modellable_tool:
        return outputs

    prompt_tokens = _tokens(prompt)
    kept: list[str] = []
    for output in outputs:
        output_tokens = _tokens(output)
        shared_specific = (
            prompt_tokens & output_tokens
        ) - _GENERIC_UNMODELLABLE_TERMS
        shared_category = any(
            prompt_tokens & group and output_tokens & group
            for group in _UNMODELLABLE_TERM_GROUPS
        )
        if shared_specific or shared_category:
            kept.append(output)
    return kept


def _build_tool_summary() -> str:
    """One line per tool (name — purpose; required params), derived from the
    tool schemas so it can't drift."""
    lines = []
    for t in TOOL_DEFINITIONS:
        schema = t.get("input_schema", {})
        required = schema.get("required", []) or []
        defaulted = [
            f"{name}={spec['default']}"
            for name, spec in (schema.get("properties", {}) or {}).items()
            if isinstance(spec, dict) and "default" in spec
        ]
        purpose = (t.get("description") or "").strip().split(". ")[0].rstrip(".")
        req = ", ".join(required) if required else "none"
        defaults = ", ".join(defaulted) if defaulted else "none"
        lines.append(
            f"- {t['name']} — {purpose}. Required: {req}. Defaults: {defaults}."
        )
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
            "rationale": {"type": "string"},
        },
        "required": ["in_domain", "tool", "slots"],
    },
}


def _verdict_from_plan(plan: dict, prompt: str) -> GatewayVerdict:
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
        name = str(s["name"])
        value = s.get("value")
        if kind == "output" and name not in OUTPUT_VOCAB:
            canonical_value = str(value) if value in OUTPUT_VOCAB else None
            if canonical_value is None:
                continue
            name = canonical_value
        slots.append(
            SlotFact(
                name=name,
                source=source,
                kind=kind,
                value=value,
            )
        )

    raw_unmodellable = plan.get("unmodellable_outputs")
    unmodellable = (
        [str(item) for item in raw_unmodellable if item]
        if isinstance(raw_unmodellable, list)
        else []
    )
    unmodellable = _requested_unmodellable_outputs(
        prompt,
        unmodellable,
        has_modellable_tool=tool is not None,
    )
    result = gate(in_domain, tool, slots, unmodellable, prompt)
    return GatewayVerdict(
        outcome=result.outcome,
        route="compute" if result.outcome == "ready" else "lightweight",
        tool=tool,
        slots=slots,
        gating_slots=result.gating_slots,
        unmodellable_outputs=unmodellable,
    )


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
        return _verdict_from_plan(plan, last_user_message)
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
    return "\n\n".join(parts)


def serialise_plan_for_system(verdict: GatewayVerdict) -> str:
    """Compact plan text injected into the compute system blocks for `ready`, so
    the heavy model starts from the resolved tool + grounded args."""
    if verdict.tool is None:
        return ""
    grounded = [
        f"{s.name}={s.value}"
        for s in verdict.slots
        if s.value and s.source in ("prompt", "default")
    ]
    lines = [f"GATEWAY PLAN (pre-resolved by a routing pass): tool={verdict.tool}."]
    if grounded:
        lines.append("Resolved inputs: " + "; ".join(grounded) + ".")
    lines.append("Treat this as a starting point and verify it against the user's message.")
    return "\n".join(lines)
