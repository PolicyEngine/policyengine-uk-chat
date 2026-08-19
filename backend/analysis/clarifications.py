"""Typed clarification construction and deterministic user-facing text."""

from __future__ import annotations

from analysis.capabilities import CAPABILITY_REGISTRY
from analysis.common import stable_identifier
from analysis.models import (
    ClarificationId,
    ClarificationChoiceMode,
    PendingClarification,
    SemanticRequestRevision,
)


_PROMPTS = {
    "missing_analysis_kind": "What type of analysis would you like me to run?",
    "missing_household": "Please describe the people in the household.",
    "missing_parameter": "Which tax or benefit parameter should I look up?",
    "missing_output": "Which result would you like to see?",
    "missing_aggregate_variable": "Which model variable should I aggregate?",
    "missing_aggregate_entity": "Should I aggregate people, benefit units, or households?",
    "missing_aggregate_operation": "Should I calculate a sum, mean, or count?",
    "ambiguous_catalogue_match": "Which of these policy parameters did you mean?",
    "confirm_reform": "Should I run the analysis with this exact policy change?",
    "unsupported_partial_request": "Which supported tax or benefit result should I calculate?",
}


def create_clarification(
    *,
    revision: SemanticRequestRevision,
    target_field: str,
    reason_code: str,
    choices: tuple[str, ...] = (),
    prompt: str | None = None,
    attempt_count: int = 0,
    choice_mode: ClarificationChoiceMode | None = None,
    target_contract: str | None = None,
) -> PendingClarification:
    if target_field == "outputs":
        resolved_contract = target_contract or "requested_outputs"
    else:
        field_spec = CAPABILITY_REGISTRY.fields.get(target_field)
        resolved_contract = target_contract or (
            field_spec.clarification_contract if field_spec else target_field
        ) or target_field
    resolved_choice_mode = choice_mode or (
        ClarificationChoiceMode.ADVISORY
        if choices
        else ClarificationChoiceMode.OPEN
    )
    return PendingClarification(
        question_id=ClarificationId(
            stable_identifier(
                "question",
                revision.session_id,
                revision.revision_id,
                target_field,
                reason_code,
                attempt_count,
            )
        ),
        session_id=revision.session_id,
        request_revision_id=revision.revision_id,
        target_field=target_field,
        target_contract=resolved_contract,
        choice_mode=resolved_choice_mode,
        reason_code=reason_code,
        prompt=prompt or _PROMPTS[reason_code],
        permitted_choices=choices,
        attempt_count=attempt_count,
        created_at=revision.created_at,
    )


def retry_clarification(
    clarification: PendingClarification,
    *,
    prompt: str | None = None,
) -> PendingClarification:
    return clarification.model_copy(
        update={
            "attempt_count": clarification.attempt_count + 1,
            "prompt": prompt or clarification.prompt,
            "question_id": stable_identifier(
                "question",
                clarification.session_id,
                clarification.request_revision_id,
                clarification.target_field,
                clarification.reason_code,
                clarification.attempt_count + 1,
            ),
        }
    )


def render_clarification(clarification: PendingClarification) -> str:
    if not clarification.permitted_choices:
        return clarification.prompt
    choices = "\n".join(
        f"{index}. {choice}"
        for index, choice in enumerate(clarification.permitted_choices, start=1)
    )
    return f"{clarification.prompt}\n\n{choices}"
