"""Deterministic rendering for authorized gateway clarification reasons."""

from __future__ import annotations

from typing import Any

from gateway.policy import GatingReason

MAX_CLARIFICATION_QUESTIONS = 3


def _quoted_labels(labels: tuple[str, ...]) -> str:
    quoted = [f"“{label}”" for label in labels]
    if len(quoted) == 1:
        return quoted[0]
    if len(quoted) == 2:
        return f"{quoted[0]} or {quoted[1]}"
    return ", ".join(quoted[:-1]) + f", or {quoted[-1]}"


def render_gating_reason(reason: GatingReason) -> str | None:
    """Render one reason without inspecting arbitrary slot names."""

    if reason.code == "missing_reform":
        return "What policy change would you like me to model?"
    if reason.code == "missing_output":
        return (
            "What result would you like to see—for example, budgetary impact, "
            "poverty, decile impacts, or winners and losers?"
        )
    if reason.code == "missing_household_composition":
        return "What household composition should I model?"
    if reason.code == "missing_tool":
        return "What tax-benefit calculation would you like me to run?"
    if reason.code == "catalogue_choice" and reason.options:
        return (
            "Which supported parameter did you mean: "
            + _quoted_labels(reason.options)
            + "?"
        )
    if reason.code == "catalogue_no_match":
        return (
            "I couldn’t identify a supported PolicyEngine parameter for that reform. "
            "Could you name the specific tax, benefit, rate, threshold, or allowance "
            "you want to change?"
        )
    return None


def _binding_labels(bindings: Any) -> tuple[str, ...]:
    labels = []
    for binding in bindings or ():
        label = getattr(binding, "label", None)
        if isinstance(label, str) and label and label not in labels:
            labels.append(label)
    return tuple(labels)


def _proposal_description(intent: Any, bindings: Any) -> str | None:
    labels = _binding_labels(bindings)
    if not labels or intent is None:
        return None
    subject = _quoted_labels(labels).replace(" or ", " and ")
    amount = getattr(intent, "amount", None)
    action = getattr(intent, "action", None)
    if action == "increase" and amount:
        return f"increasing {subject} by {amount}"
    if action == "decrease" and amount:
        return f"decreasing {subject} by {amount}"
    if action == "set" and amount:
        return f"setting {subject} to {amount}"
    if action == "abolish":
        return f"abolishing {subject}"
    if action == "freeze":
        return f"freezing {subject}"
    if action == "uprate" and amount:
        return f"uprating {subject} by {amount}"
    if action == "replace" and amount:
        return f"replacing {subject} with {amount}"
    if action == "multiply" and amount == "2x":
        return f"doubling {subject}"
    return None


def _render_confirmation(verdict: Any) -> str | None:
    assessment = getattr(verdict, "reform_assessment", None)
    intent = getattr(verdict, "reform_intent", None)
    if assessment is None:
        return None
    proposal = _proposal_description(intent, assessment.parameter_bindings)
    if proposal is None:
        return None
    rendered = f"I would model this as {proposal}. Is that what you intended?"
    alternatives = [
        description
        for alternative in assessment.alternatives
        if (
            description := _proposal_description(
                intent,
                alternative.parameter_bindings,
            )
        )
    ]
    if alternatives:
        label = (
            "Other plausible interpretation"
            if len(alternatives) == 1
            else "Other plausible interpretations"
        )
        rendered += "\n\n" + label + ": " + "; ".join(alternatives) + "."
    return rendered


def render_clarification(verdict: Any) -> str | None:
    """Render at most three stable questions, or fail closed with ``None``."""

    rendered: list[str] = []
    seen: set[str] = set()
    for reason in getattr(verdict, "gating_reasons", ()):
        if reason.code == "confirm_reform":
            question = _render_confirmation(verdict)
        else:
            question = render_gating_reason(reason)
        if question is None:
            return None
        if question in seen:
            continue
        seen.add(question)
        rendered.append(question)
        if len(rendered) == MAX_CLARIFICATION_QUESTIONS:
            break
    if not rendered:
        return None
    if len(rendered) == 1:
        return rendered[0]
    return "\n".join(
        f"{index}. {question}" for index, question in enumerate(rendered, start=1)
    )
