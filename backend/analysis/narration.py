"""Numerically constrained narration and execution-method answers."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.facts import approved_non_result_values
from analysis.models import (
    ExecutionAttempt,
    ExecutionPlan,
    FactRegister,
    SemanticRequestRevision,
)
from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_sync_client
from prompts.analysis import NARRATOR_SYSTEM


NARRATION_MODEL = os.environ.get("POLICYENGINE_CHAT_NARRATION_MODEL", DEFAULT_FAST_MODEL)
_NUMERIC_TEXT = re.compile(
    r"(?:£|\$|€)?\b\d+(?:[.,]\d+)*(?:\s*(?:%|percent|million|billion))?",
    re.IGNORECASE,
)


class _Segment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextSegment(_Segment):
    type: Literal["text"] = "text"
    text: str


class FactReferenceSegment(_Segment):
    type: Literal["fact"] = "fact"
    fact_id: str


class ApprovedNumberSegment(_Segment):
    type: Literal["approved_number"] = "approved_number"
    value_id: str


NarrationSegment = Annotated[
    TextSegment | FactReferenceSegment | ApprovedNumberSegment,
    Field(discriminator="type"),
]


class NarrationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    segments: tuple[NarrationSegment, ...] = Field(min_length=1)


NARRATION_DRAFT_ADAPTER = TypeAdapter(NarrationDraft)


@dataclass(frozen=True)
class NarrationResult:
    content: str
    model: str
    usage: dict[str, int]
    call_usages: tuple[dict[str, int], ...] = ()


class NarrationFailure(AnalysisError):
    """Narration provider failure retaining attempted-call accounting."""

    def __init__(self, call_usages: tuple[dict[str, int], ...]) -> None:
        super().__init__(
            AnalysisErrorCode.NARRATION_INVALID,
            "narration model call failed",
        )
        self.call_usages = call_usages


def _response_usage(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "cache_creation_input_tokens": getattr(
            usage, "cache_creation_input_tokens", 0
        ),
        "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
    }


def _add_usage(total: dict[str, int], added: dict[str, int]) -> None:
    for name, value in added.items():
        total[name] = total.get(name, 0) + value


def narration_tool_definition() -> dict[str, Any]:
    return {
        "name": "emit_narration",
        "description": "Emit prose with every numerical insertion represented by a reference.",
        # Anthropic strict tool schemas reject the discriminated segment union's
        # generated ``oneOf``. The draft remains untrusted until Pydantic and the
        # numerical-reference validator accept it below.
        "input_schema": NARRATION_DRAFT_ADAPTER.json_schema(),
    }


def _request_summary(revision: SemanticRequestRevision) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for name, field in revision.fields.items():
        value = field.value
        if isinstance(value, bool):
            fields[name] = value
        elif isinstance(value, (int, float)):
            fields[name] = {"numeric_fact_required": True}
        elif isinstance(value, dict):
            fields[name] = {"structured_value": True}
        elif isinstance(value, list):
            fields[name] = {"item_count": len(value), "numeric_fact_required": True}
        else:
            fields[name] = value
    return {
        "relationship": revision.relationship,
        "fields": fields,
        "outputs": revision.outputs,
    }


def _operation_summary_projection(
    summaries: tuple[dict[str, Any], ...],
    facts: FactRegister,
) -> list[dict[str, Any]]:
    fact_ids_by_step: dict[str, list[str]] = {}
    for fact in facts.facts:
        fact_ids_by_step.setdefault(fact.source_step_id, []).append(fact.fact_id)
    return [
        {
            "step_id": summary.get("step_id"),
            "operation": summary.get("operation"),
            "status": (
                summary.get("summary", {}).get("status")
                if isinstance(summary.get("summary"), dict)
                else None
            ),
            "available_fact_ids": fact_ids_by_step.get(summary.get("step_id"), []),
        }
        for summary in summaries
    ]


def narration_input(
    *,
    revision: SemanticRequestRevision,
    plan: ExecutionPlan,
    summaries: tuple[dict[str, Any], ...],
    facts: FactRegister,
    caveats: tuple[str, ...],
) -> dict[str, Any]:
    approved = approved_non_result_values(
        revision,
        plan_maximum_iterations=(
            plan.max_model_iterations if plan.max_model_iterations else None
        ),
        plan_maximum_operation_calls=(
            plan.max_operation_calls if plan.max_operation_calls else None
        ),
    )
    return {
        "request": _request_summary(revision),
        "assumptions": plan.assumptions,
        "operation_summaries": _operation_summary_projection(summaries, facts),
        "caveats": caveats,
        "fact_register": [fact.model_dump(mode="json") for fact in facts.facts],
        "approved_non_result_values": approved,
    }


def validate_narration(
    draft: NarrationDraft,
    *,
    facts: FactRegister,
    approved_values: dict[str, str],
) -> None:
    fact_ids = facts.by_id()
    for segment in draft.segments:
        if isinstance(segment, TextSegment) and _NUMERIC_TEXT.search(segment.text):
            raise AnalysisError(
                AnalysisErrorCode.NARRATION_INVALID,
                "narration text contains an unsupported numerical insertion",
            )
        if isinstance(segment, FactReferenceSegment) and segment.fact_id not in fact_ids:
            raise AnalysisError(
                AnalysisErrorCode.NARRATION_INVALID,
                "narration references an unknown fact",
            )
        if (
            isinstance(segment, ApprovedNumberSegment)
            and segment.value_id not in approved_values
        ):
            raise AnalysisError(
                AnalysisErrorCode.NARRATION_INVALID,
                "narration references an unapproved structural number",
            )


def render_narration(
    draft: NarrationDraft,
    *,
    facts: FactRegister,
    approved_values: dict[str, str],
) -> str:
    validate_narration(draft, facts=facts, approved_values=approved_values)
    by_id = facts.by_id()
    rendered = []
    for segment in draft.segments:
        if isinstance(segment, TextSegment):
            rendered.append(segment.text)
        elif isinstance(segment, FactReferenceSegment):
            rendered.append(by_id[segment.fact_id].display_value)
        else:
            rendered.append(approved_values[segment.value_id])
    return "".join(rendered)


def deterministic_fact_summary(facts: FactRegister, caveats: tuple[str, ...] = ()) -> str:
    if not facts.facts:
        return "The analysis completed, but it produced no reportable numerical facts."
    lines = ["The validated results are:"]
    lines.extend(
        f"- {fact.label}: {fact.display_value}" for fact in facts.facts
    )
    if caveats:
        lines.append("\nCaveats: " + " ".join(caveats))
    return "\n".join(lines)


def _extract_draft(response: Any) -> NarrationDraft:
    for block in getattr(response, "content", ()) or ():
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == "emit_narration"
        ):
            value = getattr(block, "input", None)
            if isinstance(value, str):
                value = json.loads(value)
            return NARRATION_DRAFT_ADAPTER.validate_python(value)
    raise AnalysisError(
        AnalysisErrorCode.NARRATION_INVALID,
        "narrator returned no structured narration",
    )


def narrate_execution_result(
    *,
    revision: SemanticRequestRevision,
    plan: ExecutionPlan,
    summaries: tuple[dict[str, Any], ...],
    facts: FactRegister,
    caveats: tuple[str, ...] = (),
    client: Any | None = None,
) -> NarrationResult:
    payload = narration_input(
        revision=revision,
        plan=plan,
        summaries=summaries,
        facts=facts,
        caveats=caveats,
    )
    approved = payload["approved_non_result_values"]
    resolved_client = client or get_sync_client()
    total_usage: dict[str, int] = {}
    call_usages: list[dict[str, int]] = []
    retry_feedback: dict[str, str] | None = None
    for _attempt in range(2):
        request_payload = dict(payload)
        if retry_feedback is not None:
            request_payload["retry_feedback"] = retry_feedback
        try:
            response = resolved_client.messages.create(
                model=NARRATION_MODEL,
                max_tokens=4096,
                temperature=DEFAULT_TEMPERATURE,
                system=NARRATOR_SYSTEM,
                tools=[narration_tool_definition()],
                tool_choice={"type": "tool", "name": "emit_narration"},
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            request_payload,
                            ensure_ascii=False,
                            default=str,
                        ),
                    }
                ],
            )
        except Exception as exc:
            call_usages.append(_response_usage(None))
            raise NarrationFailure(tuple(call_usages)) from exc
        response_usage = _response_usage(response)
        call_usages.append(response_usage)
        _add_usage(total_usage, response_usage)
        try:
            draft = _extract_draft(response)
            return NarrationResult(
                content=render_narration(
                    draft,
                    facts=facts,
                    approved_values=approved,
                ),
                model=NARRATION_MODEL,
                usage=total_usage,
                call_usages=tuple(call_usages),
            )
        except (AnalysisError, ValidationError, TypeError, ValueError) as exc:
            retry_feedback = {
                "validation_error": str(exc),
                "instruction": (
                    "Return a different emit_narration draft. Text segments must "
                    "contain no numerical characters. Insert every result through "
                    "a fact segment and every permitted structural number through "
                    "an approved_number segment using only supplied identifiers."
                ),
            }
            continue
    return NarrationResult(
        content=deterministic_fact_summary(facts, caveats),
        model=NARRATION_MODEL,
        usage=total_usage,
        call_usages=tuple(call_usages),
    )


def narrate_execution(
    *,
    revision: SemanticRequestRevision,
    plan: ExecutionPlan,
    summaries: tuple[dict[str, Any], ...],
    facts: FactRegister,
    caveats: tuple[str, ...] = (),
    client: Any | None = None,
) -> str:
    return narrate_execution_result(
        revision=revision,
        plan=plan,
        summaries=summaries,
        facts=facts,
        caveats=caveats,
        client=client,
    ).content


def execution_question_requires_rerun(question: str) -> bool:
    return bool(
        re.search(
            r"\b(?:how much|what amount|what value|numerical|number|impact|result|rate|cost)\b",
            question,
            re.IGNORECASE,
        )
    )


def answer_execution_question(
    *,
    question: str,
    revision: SemanticRequestRevision,
    plan: ExecutionPlan,
    execution: ExecutionAttempt,
) -> str:
    if execution_question_requires_rerun(question):
        raise AnalysisError(
            AnalysisErrorCode.CLARIFICATION_REQUIRED,
            "the requested numerical result is no longer available and must be recalculated",
        )
    operations = ", ".join(item.operation for item in execution.operations) or "none"
    year = revision.fields.get("year")
    year_text = str(year.value) if year is not None else "not recorded"
    assumptions = "; ".join(plan.assumptions) or "none recorded"
    return (
        f"That execution used dataset {execution.dataset_identifier}, analysis year "
        f"{year_text}, and operations {operations}. Its status was {execution.status}. "
        f"Recorded assumptions: {assumptions}."
    )
