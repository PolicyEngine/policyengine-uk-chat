from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from analysis.models import (
    CANDIDATE_TURN_UPDATE_ADAPTER,
    REFORM_INSTRUCTION_ADAPTER,
    TURN_OUTCOME_ADAPTER,
    VALIDATED_TURN_UPDATE_ADAPTER,
    CandidateAnalysis,
    CandidateField,
    CompletedTurnOutcome,
    EvidenceClaim,
    EvidenceReference,
    ExecutionStatusChange,
    AnalysisSessionState,
    PlanStatusChange,
    SetExactReform,
    SetReformToggle,
    StartAnalysis,
    ValidatedCandidateAnalysis,
    ValidatedCandidateField,
    ValidatedStartAnalysis,
    WorkflowTransition,
)
from analysis_helpers import bound_request, revision


@pytest.mark.parametrize("value", [False, True, 0, 1, 0.0, 1.25, "false"])
def test_exact_scalar_types_survive_reform_round_trip(value):
    instruction = SetExactReform(value=value)
    restored = REFORM_INSTRUCTION_ADAPTER.validate_json(
        REFORM_INSTRUCTION_ADAPTER.dump_json(instruction)
    )
    assert type(restored.value) is type(value)
    assert restored.value == value


@pytest.mark.parametrize("value", [False, True])
def test_toggle_booleans_survive_round_trip(value):
    restored = REFORM_INSTRUCTION_ADAPTER.validate_json(
        REFORM_INSTRUCTION_ADAPTER.dump_json(SetReformToggle(value=value))
    )
    assert restored.value is value


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_toggle_rejects_values_that_are_not_json_booleans(value):
    with pytest.raises(ValidationError):
        REFORM_INSTRUCTION_ADAPTER.validate_python(
            {"kind": "set_toggle", "value": value}
        )


def test_candidate_and_validated_unions_are_distinct_and_round_trip():
    raw = StartAnalysis(
        candidate=CandidateAnalysis(
            analysis_kind=CandidateField(
                value="explanation",
                evidence=EvidenceClaim(quote="explain"),
            )
        )
    )
    validated = ValidatedStartAnalysis(
        candidate=ValidatedCandidateAnalysis(
            analysis_kind=ValidatedCandidateField(
                value="explanation",
                evidence=EvidenceReference(
                    quote="explain", message_sha256="hash"
                ),
            )
        )
    )
    assert type(CANDIDATE_TURN_UPDATE_ADAPTER.validate_json(
        CANDIDATE_TURN_UPDATE_ADAPTER.dump_json(raw)
    )) is StartAnalysis
    assert type(VALIDATED_TURN_UPDATE_ADAPTER.validate_json(
        VALIDATED_TURN_UPDATE_ADAPTER.dump_json(validated)
    )) is ValidatedStartAnalysis


def test_turn_outcome_discriminator_round_trips():
    outcome = CompletedTurnOutcome(content="done", route="standard")
    restored = TURN_OUTCOME_ADAPTER.validate_json(
        TURN_OUTCOME_ADAPTER.dump_json(outcome)
    )
    assert restored == outcome


@pytest.mark.parametrize("model", [revision(), bound_request()])
def test_target_records_are_immutable(model):
    with pytest.raises(ValidationError):
        model.created_at = model.created_at


def test_semantic_revision_does_not_contain_runtime_or_binding_values():
    semantic = revision()
    encoded = semantic.model_dump(mode="json")
    assert "readiness" not in encoded
    assert "catalogue_version" not in encoded
    assert "dataset_identifier" not in encoded
    assert "output_producers" not in encoded


def test_bound_request_keeps_semantic_revision_unchanged():
    semantic = revision(outputs=("budgetary_impact",))
    before = semantic.model_dump_json()
    bound = bound_request(outputs=("budgetary_impact",))
    assert semantic.model_dump_json() == before
    assert bound.request_revision_id == semantic.revision_id
    assert bound.fields["year"].provenance.value == "default"


@pytest.mark.parametrize(
    "change",
    [
        {
            "kind": "plan_status",
            "plan_id": "plan_one",
            "next_status": "cancellation_requested",
        },
        {
            "kind": "execution_status",
            "execution_id": "execution_one",
            "next_status": "ready",
        },
        {
            "kind": "receipt_status",
            "record_id": "turn_one",
            "next_status": "completed",
        },
    ],
)
def test_transition_rejects_invalid_record_and_status_combinations(change):
    state = AnalysisSessionState(session_id="session_status")
    with pytest.raises(ValidationError):
        WorkflowTransition(
            expected_state_version=state.state_version,
            current_phase=state.phase,
            next_state=state,
            status_changes=(change,),
        )


def test_transition_status_change_variants_round_trip():
    state = AnalysisSessionState(session_id="session_status")
    transition = WorkflowTransition(
        expected_state_version=state.state_version,
        current_phase=state.phase,
        next_state=state,
        status_changes=(
            PlanStatusChange(
                plan_id="plan_one",
                expected_status="ready",
                next_status="executing",
            ),
            ExecutionStatusChange(
                execution_id="execution_one",
                expected_status="claimed",
                next_status="running",
            ),
        ),
    )

    restored = WorkflowTransition.model_validate_json(transition.model_dump_json())

    assert isinstance(restored.status_changes[0], PlanStatusChange)
    assert isinstance(restored.status_changes[1], ExecutionStatusChange)
