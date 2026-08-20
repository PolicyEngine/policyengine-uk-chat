"""Validation boundary from untrusted model candidates to semantic updates."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from analysis.capabilities import (
    CAPABILITY_REGISTRY,
    CapabilityRegistry,
    EvidencePolicy,
    SemanticFieldSpec,
    semantic_candidate_field_names,
)
from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.models import (
    AbolishReform,
    AddOutputs,
    AnalysisSessionState,
    ApplyNamedReformTransformation,
    AnswerClarification,
    AskAboutExecution,
    CandidateField,
    CandidateTurnUpdate,
    CancelAnalysis,
    ChangeReformByAmount,
    ChangeReformByPercent,
    ClearPatch,
    DirectionOnlyReform,
    EvidenceClaim,
    EvidenceReference,
    InheritOutputs,
    ClarificationId,
    PendingClarification,
    RemoveOutputs,
    ReplaceOutputs,
    RevisionId,
    ReviseAnalysis,
    SemanticRequestRevision,
    SetExactReform,
    SetPatch,
    SetReformToggle,
    StartAnalysis,
    StartRelationship,
    UnchangedPatch,
    ValidatedAddOutputs,
    ValidatedAnswerClarification,
    ValidatedAskAboutExecution,
    ValidatedCancelAnalysis,
    ValidatedCandidateAnalysis,
    ValidatedCandidateField,
    ValidatedClearPatch,
    ValidatedInheritOutputs,
    ValidatedRemoveOutputs,
    ValidatedOutputPatch,
    ValidatedReplaceOutputs,
    ValidatedReviseAnalysis,
    ValidatedSetPatch,
    ValidatedStartAnalysis,
    ValidatedTurnUpdate,
    ValidatedUnchangedPatch,
    WorkflowPhase,
)


def _normalise_text(value: str) -> str:
    return " ".join(value.casefold().split())


def evidence_reference(claim: EvidenceClaim, message: str) -> EvidenceReference:
    if _normalise_text(claim.quote) not in _normalise_text(message):
        raise AnalysisError(
            AnalysisErrorCode.INVALID_EVIDENCE,
            "candidate evidence is absent from the current user message",
        )
    return EvidenceReference(
        quote=claim.quote,
        message_sha256=hashlib.sha256(message.encode("utf-8")).hexdigest(),
    )


def _number_present(value: int | float, quote: str) -> bool:
    candidates = {str(value), f"{value:,}"}
    if isinstance(value, float) and value.is_integer():
        integer = int(value)
        candidates.update({str(integer), f"{integer:,}"})
    normalized = quote.replace("£", "").replace(",", "")
    return any(
        re.search(rf"(?<![\d.]){re.escape(candidate.replace(',', ''))}(?![\d.])", normalized)
        for candidate in candidates
    )


def _boolean_present(value: bool, quote: str) -> bool:
    normalized = _normalise_text(quote)
    tokens = (
        ("true", "yes", "on", "enable", "enabled")
        if value
        else ("false", "no", "off", "disable", "disabled")
    )
    return any(re.search(rf"\b{token}\b", normalized) for token in tokens)


def _exact_value_present(value: Any, quote: str) -> bool:
    if isinstance(value, bool):
        return _boolean_present(value, quote)
    if isinstance(value, (int, float)):
        return _number_present(value, quote)
    if isinstance(value, str):
        return _normalise_text(value) in _normalise_text(quote)
    if isinstance(value, SetExactReform):
        return _exact_value_present(value.value, quote)
    if isinstance(value, SetReformToggle):
        return _boolean_present(value.value, quote)
    if isinstance(value, ChangeReformByAmount):
        return _number_present(value.amount, quote)
    if isinstance(value, ChangeReformByPercent):
        return _number_present(value.percent, quote) and (
            "%" in quote or "percent" in _normalise_text(quote)
        )
    if isinstance(value, AbolishReform):
        return any(
            word in _normalise_text(quote)
            for word in ("abolish", "remove", "eliminate", "scrap")
        )
    if isinstance(value, DirectionOnlyReform):
        return value.direction in _normalise_text(quote) or (
            value.direction == "uprate" and "raise" in _normalise_text(quote)
        )
    if isinstance(value, ApplyNamedReformTransformation):
        return (
            not value.arguments
            and _normalise_text(value.identifier) in _normalise_text(quote)
        )
    return True


def _controlled_value(
    spec: SemanticFieldSpec,
    value: Any,
    quote: str,
) -> Any:
    raw = _normalise_text(str(value))
    normalized = spec.controlled_values.get(raw, value)
    for phrase, mapped in spec.controlled_values.items():
        if mapped == normalized and phrase in _normalise_text(quote):
            return spec.validate(normalized)
    if _normalise_text(str(normalized)) in _normalise_text(quote):
        return spec.validate(normalized)
    raise AnalysisError(
        AnalysisErrorCode.INVALID_EVIDENCE,
        f"candidate value for {spec.name} is not supported by its evidence",
    )


def _validate_field(
    field: CandidateField,
    *,
    name: str,
    analysis_kind: str,
    user_message: str,
    registry: CapabilityRegistry,
) -> ValidatedCandidateField:
    if name not in semantic_candidate_field_names(registry):
        raise AnalysisError(
            AnalysisErrorCode.INVALID_CANDIDATE,
            f"semantic field {name} is server-derived and cannot be set by a candidate",
        )
    spec = registry.field_for(name, analysis_kind)
    reference = evidence_reference(field.evidence, user_message)
    if spec.evidence_policy == EvidencePolicy.CONTROLLED:
        value = _controlled_value(spec, field.value, reference.quote)
    else:
        value = spec.validate(field.value)
    if spec.evidence_policy == EvidencePolicy.EXACT and not _exact_value_present(
        value, reference.quote
    ):
        raise AnalysisError(
            AnalysisErrorCode.INVALID_EVIDENCE,
            f"candidate value for {name} differs from its exact evidence",
        )
    return ValidatedCandidateField(value=value, evidence=reference)


def _validate_analysis_kind(
    field: CandidateField,
    *,
    user_message: str,
    registry: CapabilityRegistry,
) -> ValidatedCandidateField:
    spec = registry.fields["analysis_kind"]
    reference = evidence_reference(field.evidence, user_message)
    if spec.evidence_policy == EvidencePolicy.CONTROLLED:
        value = _controlled_value(spec, field.value, reference.quote)
    else:
        # The analysis kind is a closed server enum inferred from ordinary user
        # language. Its evidence grounds the classification in this message;
        # users do not have to state an internal category label verbatim.
        value = spec.validate(field.value)
    registry.capability_for(value)
    return ValidatedCandidateField(value=value, evidence=reference)


def _validate_outputs(
    outputs: tuple[str, ...],
    evidence: EvidenceClaim | None,
    *,
    analysis_kind: str,
    user_message: str,
    registry: CapabilityRegistry,
) -> tuple[tuple[str, ...], EvidenceReference | None]:
    normalized = tuple(dict.fromkeys(outputs))
    if normalized and evidence is None:
        raise AnalysisError(
            AnalysisErrorCode.INVALID_EVIDENCE,
            "requested outputs require evidence",
        )
    reference = evidence_reference(evidence, user_message) if evidence else None
    for output in normalized:
        registry.producer_for(analysis_kind, output)
    return normalized, reference


def _analysis_kind_for_revision(
    current_revision: SemanticRequestRevision | None,
) -> str:
    field = current_revision.fields.get("analysis_kind") if current_revision else None
    if field is None or not isinstance(field.value, str):
        raise AnalysisError(
            AnalysisErrorCode.STATE_PRECONDITION_FAILED,
            "the active request has no valid analysis kind",
        )
    return field.value


def validate_candidate(
    update: CandidateTurnUpdate,
    *,
    state: AnalysisSessionState,
    current_revision: SemanticRequestRevision | None,
    active_clarification: PendingClarification | None,
    executions: Mapping[str, Any],
    user_message: str,
    registry: CapabilityRegistry = CAPABILITY_REGISTRY,
) -> ValidatedTurnUpdate:
    """Validate one untrusted candidate against semantic and loaded-state contracts."""

    if isinstance(update, StartAnalysis):
        kind_field = _validate_analysis_kind(
            update.candidate.analysis_kind,
            user_message=user_message,
            registry=registry,
        )
        analysis_kind = kind_field.value
        if update.relationship == StartRelationship.RELATED:
            if (
                current_revision is None
                or update.related_revision_id != current_revision.revision_id
            ):
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "a related start must name the active request revision",
                )
        elif update.related_revision_id is not None:
            raise AnalysisError(
                AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                "a new or unrelated start cannot name a source revision",
            )
        fields = {
            name: _validate_field(
                field,
                name=name,
                analysis_kind=analysis_kind,
                user_message=user_message,
                registry=registry,
            )
            for name, field in update.candidate.fields.items()
        }
        outputs, output_evidence = _validate_outputs(
            update.candidate.outputs,
            update.candidate.output_evidence,
            analysis_kind=analysis_kind,
            user_message=user_message,
            registry=registry,
        )
        return ValidatedStartAnalysis(
            candidate=ValidatedCandidateAnalysis(
                analysis_kind=kind_field,
                fields=fields,
                outputs=outputs,
                output_evidence=output_evidence,
            ),
            relationship=update.relationship,
            related_revision_id=(
                RevisionId(update.related_revision_id)
                if update.related_revision_id is not None
                else None
            ),
        )

    analysis_kind = _analysis_kind_for_revision(current_revision)
    if isinstance(update, ReviseAnalysis):
        if current_revision is None or update.base_revision_id != current_revision.revision_id:
            raise AnalysisError(
                AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                "revision update does not reference the active request revision",
            )
        patches: dict[str, Any] = {}
        for name, patch in update.patches.items():
            spec = registry.field_for(name, analysis_kind)
            if isinstance(patch, UnchangedPatch):
                patches[name] = ValidatedUnchangedPatch()
            elif isinstance(patch, SetPatch):
                if not spec.allow_set:
                    raise AnalysisError(
                        AnalysisErrorCode.INVALID_CANDIDATE,
                        f"semantic field {name} cannot be set",
                    )
                validated = _validate_field(
                    CandidateField(value=patch.value, evidence=patch.evidence),
                    name=name,
                    analysis_kind=analysis_kind,
                    user_message=user_message,
                    registry=registry,
                )
                patches[name] = ValidatedSetPatch(
                    value=validated.value,
                    evidence=validated.evidence,
                )
            elif isinstance(patch, ClearPatch):
                if not spec.allow_clear:
                    raise AnalysisError(
                        AnalysisErrorCode.INVALID_CANDIDATE,
                        f"semantic field {name} cannot be cleared",
                    )
                patches[name] = ValidatedClearPatch(
                    evidence=evidence_reference(patch.evidence, user_message)
                )
        output_patch = update.outputs
        validated_outputs: ValidatedOutputPatch
        if isinstance(output_patch, InheritOutputs):
            validated_outputs = ValidatedInheritOutputs()
        else:
            outputs = tuple(dict.fromkeys(output_patch.outputs))
            for output in outputs:
                registry.producer_for(analysis_kind, output)
            reference = evidence_reference(output_patch.evidence, user_message)
            if isinstance(output_patch, AddOutputs):
                validated_outputs = ValidatedAddOutputs(outputs=outputs, evidence=reference)
            elif isinstance(output_patch, RemoveOutputs):
                validated_outputs = ValidatedRemoveOutputs(outputs=outputs, evidence=reference)
            elif isinstance(output_patch, ReplaceOutputs):
                validated_outputs = ValidatedReplaceOutputs(outputs=outputs, evidence=reference)
            else:  # pragma: no cover - discriminated union is exhaustive
                raise AnalysisError(AnalysisErrorCode.INVALID_CANDIDATE, "unknown output patch")
        return ValidatedReviseAnalysis(
            base_revision_id=RevisionId(update.base_revision_id),
            patches=patches,
            outputs=validated_outputs,
            relationship=update.relationship,
        )

    if isinstance(update, AnswerClarification):
        clarification = active_clarification
        if (
            clarification is None
            or state.active_clarification_id != update.question_id
            or clarification.question_id != update.question_id
            or current_revision is None
            or clarification.request_revision_id != current_revision.revision_id
        ):
            raise AnalysisError(
                AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                "clarification answer does not reference the active question",
            )
        if clarification.target_field == "outputs":
            reference = evidence_reference(update.evidence, user_message)
            if isinstance(update.answer, str):
                values = (update.answer,)
            elif isinstance(update.answer, (list, tuple)):
                values = tuple(update.answer)
            else:
                raise AnalysisError(
                    AnalysisErrorCode.INVALID_CANDIDATE_TYPE,
                    "output clarification requires one or more output names",
                )
            for output in values:
                if not isinstance(output, str):
                    raise AnalysisError(
                        AnalysisErrorCode.INVALID_CANDIDATE_TYPE,
                        "output clarification requires output names",
                    )
                registry.producer_for(analysis_kind, output)
                spoken_output = output.replace("_", " ")
                if _normalise_text(spoken_output) not in _normalise_text(
                    reference.quote
                ):
                    raise AnalysisError(
                        AnalysisErrorCode.INVALID_EVIDENCE,
                        "output clarification answer differs from its evidence",
                    )
            answer: Any = tuple(dict.fromkeys(values))
        else:
            validated = _validate_field(
                CandidateField(value=update.answer, evidence=update.evidence),
                name=clarification.target_field,
                analysis_kind=analysis_kind,
                user_message=user_message,
                registry=registry,
            )
            answer = validated.value
            reference = validated.evidence
        if (
            clarification.choice_mode.value == "closed"
            and clarification.permitted_choices
        ):
            choices = set(clarification.permitted_choices)
            selected = answer if isinstance(answer, tuple) else (answer,)
            if any(str(item) not in choices for item in selected):
                raise AnalysisError(
                    AnalysisErrorCode.INVALID_CANDIDATE,
                    "clarification answer is outside the permitted choices",
                )
        return ValidatedAnswerClarification(
            question_id=ClarificationId(update.question_id),
            answer=answer,
            evidence=reference,
        )

    if isinstance(update, AskAboutExecution):
        if re.search(
            r"\b(?:how much|what amount|what value|numerical|number|impact|result|rate|cost)\b",
            update.question,
            re.IGNORECASE,
        ):
            raise AnalysisError(
                AnalysisErrorCode.INVALID_CANDIDATE,
                "a numerical follow-up must be new or revised calculation work",
            )
        execution_id = update.execution_id or state.latest_execution_id
        execution = executions.get(execution_id or "")
        if execution is None or execution.session_id != state.session_id:
            raise AnalysisError(
                AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                "execution question does not reference a known session execution",
            )
        return ValidatedAskAboutExecution(
            execution_id=execution.execution_id,
            question=update.question,
            evidence=evidence_reference(update.evidence, user_message),
        )

    if isinstance(update, CancelAnalysis):
        if state.phase not in {
            WorkflowPhase.AWAITING_CLARIFICATION,
            WorkflowPhase.READY,
            WorkflowPhase.EXECUTING,
        }:
            raise AnalysisError(
                AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                "the current analysis session is not cancellable",
            )
        if update.request_revision_id and update.request_revision_id != state.active_revision_id:
            raise AnalysisError(
                AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                "cancellation does not reference the active request",
            )
        return ValidatedCancelAnalysis(request_revision_id=state.active_revision_id)

    raise AnalysisError(AnalysisErrorCode.INVALID_CANDIDATE, "unknown turn update")
