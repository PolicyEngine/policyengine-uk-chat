"""Pure reduction of validated semantic updates into immutable revisions."""

from __future__ import annotations

from datetime import datetime

from analysis.capabilities import semantic_revision_field_names
from analysis.common import AnalysisError, AnalysisErrorCode, stable_identifier
from analysis.models import (
    AnalysisSessionState,
    FieldProvenance,
    Invalidation,
    PendingClarification,
    RequestField,
    RevisionId,
    SemanticRequestRevision,
    SessionId,
    TurnId,
    ValidatedAddOutputs,
    ValidatedAnswerClarification,
    ValidatedClearPatch,
    ValidatedInheritOutputs,
    ValidatedRemoveOutputs,
    ValidatedReplaceOutputs,
    ValidatedReviseAnalysis,
    ValidatedSetPatch,
    ValidatedStartAnalysis,
    ValidatedTurnUpdate,
    ValidatedUnchangedPatch,
)


_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "analysis_kind": ("bindings", "readiness", "plan"),
    "reform": ("reform_binding", "readiness", "plan"),
    "reform_intent": ("reform_binding", "readiness", "plan"),
    "reform_instruction": ("reform_binding", "readiness", "plan"),
    "year": ("readiness", "plan"),
    "people": ("household_binding", "readiness", "plan"),
    "benunit": ("household_binding", "readiness", "plan"),
    "household": ("household_binding", "readiness", "plan"),
    "comparison_basis": ("readiness", "plan"),
    "outputs": ("readiness", "plan"),
}


def _inherited_fields(
    revision: SemanticRequestRevision,
) -> dict[str, RequestField]:
    semantic_fields = semantic_revision_field_names() | {"analysis_kind"}
    return {
        name: RequestField(
            value=field.value,
            provenance=FieldProvenance.INHERITED,
            evidence=field.evidence,
            inherited_from_revision_id=revision.revision_id,
        )
        for name, field in revision.fields.items()
        if name in semantic_fields
    }


def _invalidations(changed: set[str]) -> tuple[Invalidation, ...]:
    invalidated: dict[str, str] = {}
    for field in sorted(changed):
        for derived in _DEPENDENCIES.get(field, ("readiness", "plan")):
            invalidated.setdefault(derived, f"{field} changed")
    return tuple(
        Invalidation(field=field, reason=reason)
        for field, reason in invalidated.items()
    )


def _revision(
    *,
    session_id: str,
    turn_id: str,
    revision_number: int,
    relationship: str,
    fields: dict[str, RequestField],
    outputs: tuple[str, ...],
    base_revision_id: str | None,
    changed: set[str],
    created_at: datetime,
) -> SemanticRequestRevision:
    revision_id = stable_identifier(
        "rev",
        session_id,
        turn_id,
        revision_number,
        base_revision_id,
        relationship,
    )
    return SemanticRequestRevision(
        revision_id=RevisionId(revision_id),
        session_id=SessionId(session_id),
        revision_number=revision_number,
        turn_id=TurnId(turn_id),
        base_revision_id=(
            RevisionId(base_revision_id) if base_revision_id is not None else None
        ),
        relationship=relationship,
        fields=fields,
        outputs=outputs,
        invalidations=_invalidations(changed),
        created_at=created_at,
    )


def _start_revision(
    update: ValidatedStartAnalysis,
    *,
    state: AnalysisSessionState,
    turn_id: str,
    revision_number: int,
    bootstrap: bool,
    created_at: datetime,
) -> SemanticRequestRevision:
    provenance = FieldProvenance.BOOTSTRAP if bootstrap else FieldProvenance.USER
    fields = {
        "analysis_kind": RequestField(
            value=update.candidate.analysis_kind.value,
            provenance=provenance,
            evidence=update.candidate.analysis_kind.evidence,
        )
    }
    for name, candidate in update.candidate.fields.items():
        fields[name] = RequestField(
            value=candidate.value,
            provenance=provenance,
            evidence=candidate.evidence,
        )
    return _revision(
        session_id=state.session_id,
        turn_id=turn_id,
        revision_number=revision_number,
        relationship=update.relationship.value,
        fields=fields,
        outputs=update.candidate.outputs,
        base_revision_id=update.related_revision_id,
        changed={*fields, "outputs"},
        created_at=created_at,
    )


def _revised_outputs(
    update: ValidatedReviseAnalysis,
    current: tuple[str, ...],
) -> tuple[str, ...]:
    patch = update.outputs
    if isinstance(patch, ValidatedInheritOutputs):
        return current
    if isinstance(patch, ValidatedAddOutputs):
        return tuple(dict.fromkeys((*current, *patch.outputs)))
    if isinstance(patch, ValidatedRemoveOutputs):
        removed = set(patch.outputs)
        return tuple(output for output in current if output not in removed)
    if isinstance(patch, ValidatedReplaceOutputs):
        return tuple(dict.fromkeys(patch.outputs))
    raise AnalysisError(AnalysisErrorCode.INVALID_CANDIDATE, "unknown output patch")


def _revise_revision(
    update: ValidatedReviseAnalysis,
    *,
    state: AnalysisSessionState,
    current: SemanticRequestRevision,
    turn_id: str,
    revision_number: int,
    created_at: datetime,
) -> SemanticRequestRevision:
    if update.base_revision_id != current.revision_id:
        raise AnalysisError(
            AnalysisErrorCode.STATE_PRECONDITION_FAILED,
            "revision update does not reference the active request revision",
        )
    fields = _inherited_fields(current)
    changed: set[str] = set()
    for name, patch in update.patches.items():
        if isinstance(patch, ValidatedUnchangedPatch):
            continue
        if isinstance(patch, ValidatedSetPatch):
            fields[name] = RequestField(
                value=patch.value,
                provenance=FieldProvenance.USER,
                evidence=patch.evidence,
            )
            changed.add(name)
            continue
        if isinstance(patch, ValidatedClearPatch):
            fields.pop(name, None)
            changed.add(name)
            continue
        raise AnalysisError(AnalysisErrorCode.INVALID_CANDIDATE, "unknown field patch")
    outputs = _revised_outputs(update, current.outputs)
    if outputs != current.outputs:
        changed.add("outputs")
    return _revision(
        session_id=state.session_id,
        turn_id=turn_id,
        revision_number=revision_number,
        relationship=update.relationship.value,
        fields=fields,
        outputs=outputs,
        base_revision_id=current.revision_id,
        changed=changed,
        created_at=created_at,
    )


def _clarification_revision(
    update: ValidatedAnswerClarification,
    *,
    state: AnalysisSessionState,
    current: SemanticRequestRevision,
    clarification: PendingClarification,
    turn_id: str,
    revision_number: int,
    created_at: datetime,
) -> SemanticRequestRevision:
    if (
        state.active_clarification_id != update.question_id
        or clarification.question_id != update.question_id
        or clarification.request_revision_id != current.revision_id
    ):
        raise AnalysisError(
            AnalysisErrorCode.STATE_PRECONDITION_FAILED,
            "clarification answer does not reference the active question",
        )
    fields = _inherited_fields(current)
    outputs = current.outputs
    if clarification.target_field == "outputs":
        outputs = tuple(update.answer)
    else:
        fields[clarification.target_field] = RequestField(
            value=update.answer,
            provenance=FieldProvenance.USER,
            evidence=update.evidence,
        )
    return _revision(
        session_id=state.session_id,
        turn_id=turn_id,
        revision_number=revision_number,
        relationship="clarification_answer",
        fields=fields,
        outputs=outputs,
        base_revision_id=current.revision_id,
        changed={clarification.target_field},
        created_at=created_at,
    )


class SemanticRequestReducer:
    """The only component that creates semantic request revisions."""

    @staticmethod
    def reduce(
        update: ValidatedTurnUpdate,
        *,
        state: AnalysisSessionState,
        current_revision: SemanticRequestRevision | None,
        active_clarification: PendingClarification | None,
        turn_id: str,
        bootstrap: bool = False,
        created_at: datetime | None = None,
    ) -> SemanticRequestRevision:
        revision_number = (
            current_revision.revision_number if current_revision else 0
        ) + 1
        revision_created_at = created_at or state.updated_at
        if isinstance(update, ValidatedStartAnalysis):
            return _start_revision(
                update,
                state=state,
                turn_id=turn_id,
                revision_number=revision_number,
                bootstrap=bootstrap,
                created_at=revision_created_at,
            )
        if isinstance(update, ValidatedReviseAnalysis):
            if current_revision is None:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "revision requires an active semantic request",
                )
            return _revise_revision(
                update,
                state=state,
                current=current_revision,
                turn_id=turn_id,
                revision_number=revision_number,
                created_at=revision_created_at,
            )
        if isinstance(update, ValidatedAnswerClarification):
            if current_revision is None or active_clarification is None:
                raise AnalysisError(
                    AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                    "clarification answer requires the active request and question",
                )
            return _clarification_revision(
                update,
                state=state,
                current=current_revision,
                clarification=active_clarification,
                turn_id=turn_id,
                revision_number=revision_number,
                created_at=revision_created_at,
            )
        raise AnalysisError(
            AnalysisErrorCode.INVALID_CANDIDATE,
            "non-semantic updates cannot be applied by SemanticRequestReducer",
        )


reduce_semantic_update = SemanticRequestReducer.reduce
