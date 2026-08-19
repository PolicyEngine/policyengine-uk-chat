"""Provider-edge structured interpretation for every user turn."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import ValidationError

from analysis.binding import (
    ReformTargetSelection,
    ReformTargetSelectionRequest,
)
from analysis.candidate_validation import validate_candidate
from analysis.capabilities import CAPABILITY_REGISTRY, semantic_candidate_field_names
from analysis.catalogue import CatalogueCandidate
from analysis.common import AnalysisError, AnalysisErrorCode, stable_identifier
from analysis.models import (
    AnalysisSessionState,
    CANDIDATE_TURN_UPDATE_ADAPTER,
    CandidateTurnUpdate,
    ExecutionAttempt,
    ModelUsageEntry,
    PendingClarification,
    SemanticRequestRevision,
    ValidatedTurnUpdate,
)
from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_sync_client
from prompts.analysis import TURN_INTERPRETER_SYSTEM
from prompts.analysis import REFORM_BINDER_SYSTEM

INTERPRETER_MODEL = os.environ.get(
    "POLICYENGINE_CHAT_INTERPRETER_MODEL",
    DEFAULT_FAST_MODEL,
)
INTERPRETER_MAX_TOKENS = int(
    os.environ.get("POLICYENGINE_CHAT_INTERPRETER_MAX_TOKENS", "4096")
)
REFORM_CONSTRUCTION_MODEL = os.environ.get(
    "POLICYENGINE_CHAT_REFORM_CONSTRUCTION_MODEL",
    DEFAULT_FAST_MODEL,
)


@dataclass(frozen=True)
class InterpreterContext:
    state: AnalysisSessionState
    active_revision: SemanticRequestRevision | None
    active_clarification: PendingClarification | None
    executions: Mapping[str, ExecutionAttempt]
    latest_user_message: str
    recent_messages: tuple[dict[str, Any], ...] = ()
    permitted_revision_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class InterpretationUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class InterpretationResult:
    update: CandidateTurnUpdate
    validated_update: ValidatedTurnUpdate
    usage: InterpretationUsage
    retry_count: int = 0
    call_usages: tuple[InterpretationUsage, ...] = ()


class InterpretationFailure(AnalysisError):
    """Interpreter failure retaining one usage record per attempted model call."""

    def __init__(
        self,
        message: str,
        call_usages: tuple[InterpretationUsage, ...],
    ) -> None:
        super().__init__(AnalysisErrorCode.INVALID_CANDIDATE, message)
        self.call_usages = call_usages


def candidate_tool_definition() -> dict[str, Any]:
    update_schema = CANDIDATE_TURN_UPDATE_ADAPTER.json_schema()
    semantic_fields = sorted(semantic_candidate_field_names())
    evidence_schema = {"$ref": "#/$defs/EvidenceClaim"}
    field_value_schemas: dict[str, dict[str, Any]] = {}
    for name in ("analysis_kind", *semantic_fields):
        value_schema = CAPABILITY_REGISTRY.fields[name].adapter.json_schema(
            ref_template="#/$defs/{model}"
        )
        update_schema["$defs"].update(value_schema.pop("$defs", {}))
        field_value_schemas[name] = value_schema

    def candidate_field_schema(name: str) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "value": field_value_schemas[name],
                "evidence": evidence_schema,
            },
            "required": ["value", "evidence"],
            "additionalProperties": False,
        }

    def patch_schema(name: str) -> dict[str, Any]:
        return {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"op": {"const": "unchanged"}},
                    "required": ["op"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "set"},
                        "value": field_value_schemas[name],
                        "evidence": evidence_schema,
                    },
                    "required": ["op", "value", "evidence"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {
                        "op": {"const": "clear"},
                        "evidence": evidence_schema,
                    },
                    "required": ["op", "evidence"],
                    "additionalProperties": False,
                },
            ]
        }

    update_schema["$defs"]["CandidateAnalysis"]["properties"]["analysis_kind"] = (
        candidate_field_schema("analysis_kind")
    )
    update_schema["$defs"]["CandidateAnalysis"]["properties"]["fields"] = {
        "type": "object",
        "properties": {name: candidate_field_schema(name) for name in semantic_fields},
        "additionalProperties": False,
    }
    output_items = {"type": "string", "enum": sorted(CAPABILITY_REGISTRY.producers)}
    update_schema["$defs"]["CandidateAnalysis"]["properties"]["outputs"] = {
        "type": "array",
        "items": output_items,
    }
    for definition_name in ("AddOutputs", "RemoveOutputs", "ReplaceOutputs"):
        update_schema["$defs"][definition_name]["properties"]["outputs"] = {
            "type": "array",
            "items": output_items,
            **(
                {"minItems": 1}
                if definition_name in {"AddOutputs", "RemoveOutputs"}
                else {}
            ),
        }
    update_schema["$defs"]["ReviseAnalysis"]["properties"]["patches"] = {
        "type": "object",
        "properties": {name: patch_schema(name) for name in semantic_fields},
        "additionalProperties": False,
    }
    return {
        "name": "emit_turn_update",
        "description": "Emit one typed semantic update to the current workflow state.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "update": update_schema,
            },
            "required": ["update"],
            "additionalProperties": False,
        },
    }


def reform_target_selection_tool_definition(
    candidates: tuple[CatalogueCandidate, ...],
) -> dict[str, Any]:
    identifiers = [candidate.identifier for candidate in candidates]
    labels = [candidate.label for candidate in candidates]
    return {
        "name": "emit_reform_targets",
        "description": "Select only authoritative target identifiers supported by the user's words.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "selections": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "properties": {
                            "identifier": {"type": "string", "enum": identifiers},
                            "label": {"type": "string", "enum": labels},
                            "evidence": {"type": "string", "minLength": 1},
                        },
                        "required": ["identifier", "label", "evidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["selections"],
            "additionalProperties": False,
        },
    }


def _normalise_text(value: str) -> str:
    return " ".join(value.casefold().split())


def compact_context(context: InterpreterContext) -> dict[str, Any]:
    return {
        "workflow": context.state.model_dump(mode="json"),
        "active_request": (
            context.active_revision.model_dump(mode="json")
            if context.active_revision
            else None
        ),
        "active_clarification": (
            context.active_clarification.model_dump(mode="json")
            if context.active_clarification
            else None
        ),
        "executions": [
            execution.model_dump(mode="json")
            for execution in context.executions.values()
        ][-5:],
        "recent_messages": list(context.recent_messages[-4:]),
        "permitted_revision_ids": sorted(context.permitted_revision_ids),
        "latest_user_message": context.latest_user_message,
    }


def _usage(response: Any) -> InterpretationUsage:
    usage = getattr(response, "usage", None)
    return InterpretationUsage(
        input_tokens=getattr(usage, "input_tokens", 0),
        output_tokens=getattr(usage, "output_tokens", 0),
        cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0),
        cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
    )


def _add_usage(
    total: InterpretationUsage,
    added: InterpretationUsage,
) -> InterpretationUsage:
    return InterpretationUsage(
        input_tokens=total.input_tokens + added.input_tokens,
        output_tokens=total.output_tokens + added.output_tokens,
        cache_creation_input_tokens=(
            total.cache_creation_input_tokens
            + added.cache_creation_input_tokens
        ),
        cache_read_input_tokens=(
            total.cache_read_input_tokens + added.cache_read_input_tokens
        ),
    )


def _extract_update(response: Any) -> CandidateTurnUpdate:
    for block in getattr(response, "content", ()) or ():
        if (
            getattr(block, "type", None) == "tool_use"
            and getattr(block, "name", None) == "emit_turn_update"
        ):
            value = getattr(block, "input", None)
            if isinstance(value, dict) and "update" in value:
                return CANDIDATE_TURN_UPDATE_ADAPTER.validate_python(value["update"])
    raise AnalysisError(
        AnalysisErrorCode.INVALID_CANDIDATE,
        "interpreter returned no turn update",
    )


def interpret_turn(
    context: InterpreterContext,
    *,
    client: Any | None = None,
) -> InterpretationResult:
    """Request one validated candidate, retrying invalid output once."""

    resolved_client = client or get_sync_client()
    last_error: Exception | None = None
    retry_feedback: dict[str, str] | None = None
    total_usage = InterpretationUsage()
    call_usages: list[InterpretationUsage] = []
    for _attempt in range(2):
        payload = compact_context(context)
        if retry_feedback is not None:
            payload["retry_feedback"] = retry_feedback
        try:
            response = resolved_client.messages.create(
                model=INTERPRETER_MODEL,
                max_tokens=INTERPRETER_MAX_TOKENS,
                temperature=DEFAULT_TEMPERATURE,
                system=TURN_INTERPRETER_SYSTEM,
                tools=[candidate_tool_definition()],
                tool_choice={"type": "tool", "name": "emit_turn_update"},
                messages=[
                    {
                        "role": "user",
                        "content": json.dumps(payload, ensure_ascii=False),
                    }
                ],
            )
        except Exception as exc:
            call_usages.append(InterpretationUsage())
            raise InterpretationFailure(
                "interpreter model call failed",
                tuple(call_usages),
            ) from exc
        response_usage = _usage(response)
        call_usages.append(response_usage)
        total_usage = _add_usage(total_usage, response_usage)
        try:
            update = _extract_update(response)
            validated_update = validate_candidate(
                update,
                state=context.state,
                current_revision=context.active_revision,
                active_clarification=context.active_clarification,
                executions=context.executions,
                user_message=context.latest_user_message,
            )
            return InterpretationResult(
                update=update,
                validated_update=validated_update,
                usage=total_usage,
                retry_count=_attempt,
                call_usages=tuple(call_usages),
            )
        except (AnalysisError, ValidationError, TypeError, ValueError) as exc:
            last_error = exc
            retry_feedback = {
                "reason": (
                    exc.code.value
                    if isinstance(exc, AnalysisError)
                    else "schema_validation_failed"
                ),
                "instruction": (
                    "Return a different candidate that satisfies the supplied state, "
                    "identifier, and exact-evidence requirements. Numerical follow-ups "
                    "must be new or revised calculation work, not execution questions."
                ),
            }
    raise InterpretationFailure(
        "interpreter failed to return a valid state update after one retry",
        tuple(call_usages),
    ) from last_error


def select_reform_targets(
    request: ReformTargetSelectionRequest,
    *,
    client: Any | None = None,
) -> ReformTargetSelection | None:
    """Use a model only to select identifiers from authoritative candidates."""

    candidates = request.candidates
    if not candidates:
        return None
    resolved_client = client or get_sync_client()
    tool = reform_target_selection_tool_definition(candidates)
    try:
        response = resolved_client.messages.create(
            model=REFORM_CONSTRUCTION_MODEL,
            max_tokens=INTERPRETER_MAX_TOKENS,
            temperature=DEFAULT_TEMPERATURE,
            system=REFORM_BINDER_SYSTEM,
            tools=[tool],
            tool_choice={"type": "tool", "name": "emit_reform_targets"},
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "reform_intent": request.intent,
                            "year": request.year,
                            "candidates": [
                                {
                                    "identifier": candidate.identifier,
                                    "label": candidate.label,
                                }
                                for candidate in candidates
                            ],
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        )
    except Exception:
        response = None
    response_usage = _usage(response)
    usage_entry = ModelUsageEntry(
        usage_entry_id=stable_identifier(
            "model_usage",
            request.session_id,
            request.turn_id,
            "reform_target_selection",
        ),
        session_id=request.session_id,
        turn_id=request.turn_id,
        operation="reform_target_selection",
        model=REFORM_CONSTRUCTION_MODEL,
        input_tokens=response_usage.input_tokens,
        output_tokens=response_usage.output_tokens,
        cache_creation_input_tokens=response_usage.cache_creation_input_tokens,
        cache_read_input_tokens=response_usage.cache_read_input_tokens,
    )
    if response is None:
        return ReformTargetSelection(
            bindings=(),
            usage_entry=usage_entry,
            error="reform target selection model call failed",
        )
    allowed = {candidate.identifier: candidate for candidate in candidates}
    for block in getattr(response, "content", ()) or ():
        if (
            getattr(block, "type", None) != "tool_use"
            or getattr(block, "name", None) != "emit_reform_targets"
        ):
            continue
        raw = getattr(block, "input", None)
        if not isinstance(raw, dict):
            break
        selections = raw.get("selections")
        if not isinstance(selections, list) or not selections:
            break
        resolved_bindings = []
        for binding in selections:
            if not isinstance(binding, dict):
                break
            candidate = allowed.get(binding.get("identifier"))
            if candidate is None or binding.get("label") != candidate.label:
                break
            evidence = binding.get("evidence")
            if (
                not isinstance(evidence, str)
                or _normalise_text(evidence) not in _normalise_text(request.intent)
            ):
                break
            resolved_bindings.append(candidate)
        else:
            return ReformTargetSelection(
                bindings=tuple(resolved_bindings),
                usage_entry=usage_entry,
            )
        break
    return ReformTargetSelection(
        bindings=(),
        usage_entry=usage_entry,
        error=(
            "reform target selector returned identifiers outside the supplied candidates"
        ),
    )
