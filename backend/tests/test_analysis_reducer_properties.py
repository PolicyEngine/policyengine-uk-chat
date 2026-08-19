from __future__ import annotations

from hypothesis import given, strategies as st

from analysis.models import (
    AnalysisSessionState,
    EvidenceReference,
    RevisionRelationship,
    ValidatedClearPatch,
    ValidatedReviseAnalysis,
    ValidatedSetPatch,
)
from analysis.reducer import reduce_semantic_update
from analysis_helpers import NOW, revision


@given(
    original=st.integers(min_value=2000, max_value=2100),
    replacement=st.integers(min_value=2000, max_value=2100),
    clear_basis=st.booleans(),
)
def test_revision_properties_preserve_parent_and_exact_types(
    original,
    replacement,
    clear_basis,
):
    current = revision(fields={"year": original, "comparison_basis": "income"})
    evidence = EvidenceReference(quote=str(replacement), message_sha256="hash")
    patches = {"year": ValidatedSetPatch(value=replacement, evidence=evidence)}
    if clear_basis:
        patches["comparison_basis"] = ValidatedClearPatch(evidence=evidence)
    result = reduce_semantic_update(
        ValidatedReviseAnalysis(
            base_revision_id=current.revision_id,
            patches=patches,
            relationship=RevisionRelationship.CORRECTION,
        ),
        state=AnalysisSessionState(
            session_id=current.session_id,
            active_revision_id=current.revision_id,
            updated_at=NOW,
        ),
        current_revision=current,
        active_clarification=None,
        turn_id="turn_property",
        created_at=NOW,
    )
    assert result.base_revision_id == current.revision_id
    assert result.revision_number == current.revision_number + 1
    assert type(result.fields["year"].value) is int
    assert result.fields["year"].value == replacement
    assert ("comparison_basis" not in result.fields) is clear_basis
