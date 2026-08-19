from __future__ import annotations

import inspect

import pytest

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.models import (
    AnalysisSessionState,
    EvidenceReference,
    RevisionRelationship,
    ValidatedAddOutputs,
    ValidatedClearPatch,
    ValidatedReviseAnalysis,
    ValidatedSetPatch,
)
from analysis.reducer import SemanticRequestReducer, reduce_semantic_update
from analysis_helpers import NOW, revision


def _evidence(quote="evidence"):
    return EvidenceReference(quote=quote, message_sha256="hash")


def _state(current):
    return AnalysisSessionState(
        session_id=current.session_id,
        state_version=1,
        phase="completed",
        active_revision_id=current.revision_id,
        updated_at=NOW,
    )


def test_revise_inherits_unchanged_fields_and_clears_explicit_field():
    current = revision(
        fields={"year": 2025, "comparison_basis": "income"},
        outputs=("budgetary_impact",),
    )
    result = reduce_semantic_update(
        ValidatedReviseAnalysis(
            base_revision_id=current.revision_id,
            patches={
                "year": ValidatedSetPatch(value=2026, evidence=_evidence("2026")),
                "comparison_basis": ValidatedClearPatch(evidence=_evidence("clear")),
            },
            outputs=ValidatedAddOutputs(
                outputs=("poverty_impact",), evidence=_evidence("poverty")
            ),
            relationship=RevisionRelationship.ADDITIONAL_OUTPUT,
        ),
        state=_state(current),
        current_revision=current,
        active_clarification=None,
        turn_id="turn_2",
        created_at=NOW,
    )
    assert result.fields["year"].value == 2026
    assert result.fields["year"].provenance.value == "user"
    assert "comparison_basis" not in result.fields
    assert result.outputs == ("budgetary_impact", "poverty_impact")
    assert {item.field for item in result.invalidations} >= {"readiness", "plan"}


def test_revision_with_stale_base_is_rejected():
    current = revision()
    update = ValidatedReviseAnalysis(
        base_revision_id="rev_stale",
        relationship=RevisionRelationship.CORRECTION,
    )
    with pytest.raises(AnalysisError) as raised:
        reduce_semantic_update(
            update,
            state=_state(current),
            current_revision=current,
            active_clarification=None,
            turn_id="turn_2",
        )
    assert raised.value.code == AnalysisErrorCode.STATE_PRECONDITION_FAILED


def test_semantic_reducer_has_no_binding_compilation_or_persistence_dependency():
    source = inspect.getsource(SemanticRequestReducer)
    for forbidden in (
        "bind_request",
        "compile_plan",
        "commit_transition",
        "catalogue_version",
        "dataset_identifier",
    ):
        assert forbidden not in source


def test_semantic_reducer_rejects_nonsemantic_update_family():
    from analysis.models import CancelAnalysis

    current = revision()
    with pytest.raises(AnalysisError) as raised:
        reduce_semantic_update(
            CancelAnalysis(),
            state=_state(current),
            current_revision=current,
            active_clarification=None,
            turn_id="turn_2",
        )
    assert raised.value.code == AnalysisErrorCode.INVALID_CANDIDATE
