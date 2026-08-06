"""Deterministic extraction of bounded output and reform intent."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Literal

from gateway.policy import SlotFact, TOOL_SLOT_REQUIREMENT

OutputKind = Literal[
    "budgetary_impact",
    "tax_revenue",
    "benefit_spending",
    "poverty_impact",
    "inequality_impact",
    "decile_impact",
    "winners_losers",
]

ReformAction = Literal[
    "increase",
    "decrease",
    "set",
    "abolish",
    "freeze",
    "uprate",
    "replace",
    "multiply",
]

ReformScope = Literal["unspecified", "all", "every", "both"]


@dataclass(frozen=True)
class OutputIntent:
    value: OutputKind
    evidence: str


@dataclass(frozen=True)
class ReformIntent:
    policy_phrase: str
    action: ReformAction
    amount: str | None
    scope: ReformScope
    evidence: str


_OUTPUT_PATTERNS: tuple[tuple[OutputKind, tuple[re.Pattern[str], ...]], ...] = (
    (
        "winners_losers",
        (
            re.compile(r"\bgain(?:s|ed)?\s+or\s+los(?:e|es|t)\b", re.I),
            re.compile(r"\bhow many\s+(?:people|households)\s+(?:would\s+)?(?:gain|lose|be affected)\b", re.I),
            re.compile(r"\bhouseholds?\s+(?:would\s+)?gain\b", re.I),
            re.compile(r"\baffected[- ]household counts?\b", re.I),
        ),
    ),
    (
        "decile_impact",
        (
            re.compile(r"\bby\s+(?:income\s+)?decile\b", re.I),
            re.compile(r"\bdecile\s+impacts?\b", re.I),
            re.compile(r"\bdistributional impact\s+by\s+decile\b", re.I),
        ),
    ),
    (
        "poverty_impact",
        (re.compile(r"\b(?:child\s+)?poverty\b", re.I),),
    ),
    (
        "inequality_impact",
        (
            re.compile(r"\binequality\b", re.I),
            re.compile(r"\bgini\b", re.I),
        ),
    ),
    (
        "tax_revenue",
        (
            re.compile(r"\bannual revenue\b", re.I),
            re.compile(r"\btax revenue\b", re.I),
            re.compile(r"\brevenue from\b", re.I),
        ),
    ),
    (
        "benefit_spending",
        (
            re.compile(r"\bbenefit spending\b", re.I),
            re.compile(r"\bbenefit expenditure\b", re.I),
        ),
    ),
    (
        "budgetary_impact",
        (
            re.compile(r"\bbudgetary (?:cost|impact)\b", re.I),
            re.compile(r"\bfiscal (?:cost|impact)\b", re.I),
            re.compile(r"\bannual cost\b", re.I),
            re.compile(
                r"\bcost of\s+(?:increas\w*|rais\w*|reduc\w*|lower\w*|cut\w*|abolish\w*|freez\w*|uprat\w*|replac\w*|doubl\w*|sett?\w*)\b",
                re.I,
            ),
        ),
    ),
)


def output_from_prompt(prompt: str) -> OutputIntent | None:
    """Return the highest-precedence directly modelled output in ``prompt``."""

    for value, patterns in _OUTPUT_PATTERNS:
        matches = [match for pattern in patterns if (match := pattern.search(prompt))]
        if matches:
            match = min(matches, key=lambda candidate: candidate.start())
            return OutputIntent(value=value, evidence=match.group(0))
    return None


_AMOUNT = (
    r"(?:"
    r"£\s?\d[\d,]*(?:\.\d+)?(?:\s+per\s+(?:week|month|year))?"
    r"|\d+(?:\.\d+)?\s*%"
    r"|(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+(?:\.\d+)?)"
    r"\s*(?:percentage points?|pp)"
    r")"
)
_POLICY = r"(?P<policy>[^?.!,;]+?)"
_SCOPE_TAIL = r"(?:\s+for\s+(?:all|every|both)\b[^?.!,;]*)?"

_AMOUNT_ACTION = re.compile(
    rf"\b(?P<verb>increas(?:e|es|ed|ing)|rais(?:e|es|ed|ing)|"
    rf"reduc(?:e|es|ed|ing)|lower(?:s|ed|ing)?|cut(?:s|ting)?|"
    rf"uprat(?:e|es|ed|ing))\s+{_POLICY}\s+(?:by|to)\s+"
    rf"(?P<amount>{_AMOUNT}){_SCOPE_TAIL}",
    re.I,
)
_FROM_TO_ACTION = re.compile(
    rf"\b(?P<verb>increas(?:e|es|ed|ing)|rais(?:e|es|ed|ing)|"
    rf"reduc(?:e|es|ed|ing)|lower(?:s|ed|ing)?|cut(?:s|ting)?)\s+"
    rf"{_POLICY}\s+from\s+{_AMOUNT}\s+to\s+(?P<amount>{_AMOUNT})"
    rf"{_SCOPE_TAIL}",
    re.I,
)
_SET_ACTION = re.compile(
    rf"\bset(?:s|ting)?\s+{_POLICY}\s+to\s+(?P<amount>{_AMOUNT}){_SCOPE_TAIL}",
    re.I,
)
_REPLACE_ACTION = re.compile(
    rf"\breplac(?:e|es|ed|ing)\s+{_POLICY}\s+with\s+(?P<amount>[^?.!,;]+)",
    re.I,
)
_NO_AMOUNT_ACTION = re.compile(
    r"\b(?P<verb>abolish(?:es|ed|ing)?|scrap(?:s|ped|ping)?|"
    r"freez(?:e|es|ing)|froze)\s+(?P<policy>[^?.!,;]+)",
    re.I,
)
_MULTIPLY_ACTION = re.compile(
    r"\b(?P<verb>double|doubles|doubled|doubling)\s+(?P<policy>[^?.!,;]+)",
    re.I,
)

_DECREASE_VERBS = ("reduc", "lower", "cut")
_UPRATE_VERBS = ("uprat",)
_GENERIC_POLICIES = {
    "it",
    "this",
    "that",
    "them",
    "the reform",
    "a reform",
    "reform",
    "the policy",
    "policy",
    "the two reforms",
    "two reforms",
}


def _scope(evidence: str) -> ReformScope:
    lowered = evidence.casefold()
    for scope in ("all", "every", "both"):
        if re.search(rf"\b{scope}\b", lowered):
            return scope  # type: ignore[return-value]
    return "unspecified"


def _clean_policy(value: str) -> str | None:
    policy = value.strip(" \t\n\r-–—")
    policy = re.sub(r"^(?:the|a|an)\s+", "", policy, flags=re.I)
    policy = re.sub(r"^(?:all|every|both)\s+", "", policy, flags=re.I)
    policy = policy.strip()
    if not policy or policy.casefold() in _GENERIC_POLICIES:
        return None
    if len(re.findall(r"[A-Za-z]", policy)) < 3:
        return None
    return policy


def reform_intent_from_prompt(prompt: str) -> ReformIntent | None:
    """Extract a complete, grounded natural-language reform operation."""

    match = _MULTIPLY_ACTION.search(prompt)
    if match:
        policy = _clean_policy(match.group("policy"))
        if policy:
            return ReformIntent(policy, "multiply", "2x", _scope(match.group(0)), match.group(0))

    match = _REPLACE_ACTION.search(prompt)
    if match:
        policy = _clean_policy(match.group("policy"))
        amount = match.group("amount").strip()
        if policy and amount:
            return ReformIntent(policy, "replace", amount, _scope(match.group(0)), match.group(0))

    match = _SET_ACTION.search(prompt)
    if match:
        policy = _clean_policy(match.group("policy"))
        if policy:
            return ReformIntent(
                policy,
                "set",
                match.group("amount").strip(),
                _scope(match.group(0)),
                match.group(0),
            )

    # A fully specified "from X to Y" proposal gives a final value. Treat it
    # as a set operation so assessment does not compare the destination with a
    # possibly different current catalogue value and reject the stated
    # direction (for example a historical 18% -> 20% change).
    match = _FROM_TO_ACTION.search(prompt)
    if match:
        policy = _clean_policy(match.group("policy"))
        if policy:
            return ReformIntent(
                policy,
                "set",
                match.group("amount").strip(),
                _scope(match.group(0)),
                match.group(0),
            )

    match = _AMOUNT_ACTION.search(prompt)
    if match:
        policy = _clean_policy(match.group("policy"))
        verb = match.group("verb").casefold()
        if policy:
            action: ReformAction
            if verb.startswith(_DECREASE_VERBS):
                action = "decrease"
            elif verb.startswith(_UPRATE_VERBS):
                action = "uprate"
            else:
                action = "increase"
            return ReformIntent(
                policy,
                action,
                match.group("amount").strip(),
                _scope(match.group(0)),
                match.group(0),
            )

    match = _NO_AMOUNT_ACTION.search(prompt)
    if match:
        policy = _clean_policy(match.group("policy"))
        if policy:
            verb = match.group("verb").casefold()
            action = "freeze" if verb.startswith(("freez", "froze")) else "abolish"
            return ReformIntent(policy, action, None, _scope(match.group(0)), match.group(0))
    return None


def upsert_output_slot(slots: list[SlotFact], intent: OutputIntent) -> list[SlotFact]:
    """Ground a missing/assumed output without overriding explicit evidence."""

    output_index = next(
        (index for index, slot in enumerate(slots) if slot.kind == "output"),
        None,
    )
    grounded = SlotFact(
        name="output",
        source="prompt",
        kind="output",
        value=intent.value,
    )
    if output_index is None:
        return [*slots, grounded]
    if slots[output_index].source == "prompt":
        return list(slots)
    updated = list(slots)
    updated[output_index] = replace(grounded)
    return updated


def upsert_prompt_year(
    tool: str | None,
    slots: list[SlotFact],
    prompt: str,
) -> list[SlotFact]:
    """Preserve one explicit prompt year for tools whose schema accepts it."""

    if tool is None or (tool, "year") not in TOOL_SLOT_REQUIREMENT:
        return list(slots)
    years = list(dict.fromkeys(re.findall(r"\b(?:19|20)\d{2}\b", prompt)))
    if len(years) != 1:
        return list(slots)
    grounded = SlotFact(name="year", source="prompt", value=years[0])
    year_index = next(
        (
            index
            for index, slot in enumerate(slots)
            if slot.kind == "tool_input" and slot.name == "year"
        ),
        None,
    )
    if year_index is None:
        return [*slots, grounded]
    updated = list(slots)
    updated[year_index] = grounded
    return updated
