from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from analysis.binding import ReformTargetSelectionRequest
from analysis.candidate_validation import validate_candidate
from analysis.catalogue import CatalogueCandidate
from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.interpreter import (
    InterpreterContext,
    candidate_tool_definition,
    interpret_turn,
    reform_target_selection_tool_definition,
    select_reform_targets,
)
from analysis.models import (
    AnalysisSessionState,
    AnswerClarification,
    AskAboutExecution,
    CandidateAnalysis,
    CandidateField,
    EvidenceClaim,
    ExecutionAttemptStatus,
    PendingClarification,
    SetExactReform,
    StartAnalysis,
)
from analysis_helpers import NOW, plan_and_records, revision


def _field(value, quote):
    return CandidateField(value=value, evidence=EvidenceClaim(quote=quote))


def _catalogue_candidate():
    return CatalogueCandidate(
        kind="reform_target",
        query="one",
        identifier="p.one",
        label="One",
        match_type="exact_label",
        score=1,
    )


def _start(*, fields=None, outputs=()):
    return StartAnalysis(
        candidate=CandidateAnalysis(
            analysis_kind=_field("society", "society"),
            fields=fields or {},
            outputs=outputs,
            output_evidence=(
                EvidenceClaim(quote="budgetary impact") if outputs else None
            ),
        )
    )


def _state():
    return AnalysisSessionState(session_id="session_test", updated_at=NOW)


def test_candidate_schema_is_generated_without_execution_authority():
    tool = candidate_tool_definition()
    schema_text = str(tool)
    for forbidden in (
        "permitted_operations",
        "operation_constraints",
        "max_model_iterations",
        "max_operation_calls",
        "dataset_identifier",
    ):
        assert forbidden not in schema_text
    assert "reform_instruction" in schema_text
    assert "Retrieve a current policy rate" in schema_text
    assert "Calculate an aggregate UK population result" in schema_text
    assert "does not need to name an internal category" in schema_text
    assert "exclude count, population, and entity wording" in schema_text
    assert "do not add role labels" in schema_text
    assert "server apply its UK default" in schema_text
    assert "oneOf" in schema_text
    assert "strict" not in tool


@pytest.mark.parametrize(
    ("value", "quote", "expected"),
    [(False, "set it false", False), (True, "set it true", True), (0, "set it to 0", 0)],
)
def test_exact_reform_scalars_are_preserved(value, quote, expected):
    update = _start(
        fields={
            "reform_intent": _field("a policy toggle", quote),
            "reform_instruction": _field(SetExactReform(value=value), quote),
        },
        outputs=("budgetary_impact",),
    )
    validated = validate_candidate(
        update,
        state=_state(),
        current_revision=None,
        active_clarification=None,
        executions={},
        user_message=f"society budgetary impact {quote}",
    )
    actual = validated.candidate.fields["reform_instruction"].value.value
    assert type(actual) is type(expected)
    assert actual == expected


@pytest.mark.parametrize("value", ["false", "true", 0, 1])
def test_candidate_validation_rejects_non_boolean_toggle_values(value):
    update = _start(
        fields={
            "reform_intent": _field("a policy toggle", str(value)),
            "reform_instruction": _field(
                {"kind": "set_toggle", "value": value}, str(value)
            ),
        },
        outputs=("budgetary_impact",),
    )

    with pytest.raises(AnalysisError) as raised:
        validate_candidate(
            update,
            state=_state(),
            current_revision=None,
            active_clarification=None,
            executions={},
            user_message=f"society budgetary impact set the toggle to {value}",
        )

    assert raised.value.code == AnalysisErrorCode.INVALID_CANDIDATE_TYPE


@pytest.mark.parametrize(
    ("field_name", "value", "message"),
    [
        ("year", "2026", "society in 2026"),
        (
            "reform",
            {"gov.hmrc.income_tax.rates.uk[0].rate": 0.21},
            "society set the income tax rate to 0.21",
        ),
        ("permitted_operations", ["run_society_simulation"], "society"),
        ("dataset_identifier", "secret", "society secret"),
    ],
)
def test_invalid_or_forbidden_candidate_fields_are_rejected(field_name, value, message):
    update = _start(fields={field_name: _field(value, str(value))})
    with pytest.raises(AnalysisError) as raised:
        validate_candidate(
            update,
            state=_state(),
            current_revision=None,
            active_clarification=None,
            executions={},
            user_message=message,
        )
    assert raised.value.code in {
        AnalysisErrorCode.INVALID_CANDIDATE,
        AnalysisErrorCode.INVALID_CANDIDATE_TYPE,
    }


def test_execution_question_cannot_disguise_calculation_work():
    semantic, _bound, _plan, state, attempt = plan_and_records()
    state = state.model_copy(
        update={
            "phase": "completed",
            "active_execution_id": None,
            "latest_execution_id": attempt.execution_id,
        }
    )
    update = AskAboutExecution(
        execution_id=attempt.execution_id,
        question="What was the numerical impact?",
        evidence=EvidenceClaim(quote="numerical impact"),
    )
    with pytest.raises(AnalysisError) as raised:
        validate_candidate(
            update,
            state=state,
            current_revision=semantic,
            active_clarification=None,
            executions={str(attempt.execution_id): attempt},
            user_message="What was the numerical impact?",
        )
    assert raised.value.code == AnalysisErrorCode.INVALID_CANDIDATE


def test_clarification_answer_is_validated_against_target_evidence():
    current = revision(fields={"year": 2026})
    clarification = PendingClarification(
        question_id="question_year",
        session_id=current.session_id,
        request_revision_id=current.revision_id,
        target_field="year",
        target_contract="simulation_year",
        reason_code="missing_year",
        prompt="Which year?",
        created_at=NOW,
    )
    state = AnalysisSessionState(
        session_id=current.session_id,
        phase="awaiting_clarification",
        active_revision_id=current.revision_id,
        active_clarification_id=clarification.question_id,
        updated_at=NOW,
    )
    update = AnswerClarification(
        question_id=clarification.question_id,
        answer=2027,
        evidence=EvidenceClaim(quote="2026"),
    )

    with pytest.raises(AnalysisError) as raised:
        validate_candidate(
            update,
            state=state,
            current_revision=current,
            active_clarification=clarification,
            executions={},
            user_message="Use 2026.",
        )
    assert raised.value.code == AnalysisErrorCode.INVALID_EVIDENCE


def test_closed_output_clarification_accepts_only_cited_permitted_choices():
    current = revision(outputs=())
    clarification = PendingClarification(
        question_id="question_output",
        session_id=current.session_id,
        request_revision_id=current.revision_id,
        target_field="outputs",
        target_contract="requested_outputs",
        choice_mode="closed",
        reason_code="missing_output",
        prompt="Which output?",
        permitted_choices=("budgetary_impact", "poverty_impact"),
        created_at=NOW,
    )
    state = AnalysisSessionState(
        session_id=current.session_id,
        phase="awaiting_clarification",
        active_revision_id=current.revision_id,
        active_clarification_id=clarification.question_id,
        updated_at=NOW,
    )
    validated = validate_candidate(
        AnswerClarification(
            question_id=clarification.question_id,
            answer=["budgetary_impact", "poverty_impact"],
            evidence=EvidenceClaim(
                quote="budgetary impact and poverty impact"
            ),
        ),
        state=state,
        current_revision=current,
        active_clarification=clarification,
        executions={},
        user_message="Show budgetary impact and poverty impact.",
    )
    assert validated.answer == ("budgetary_impact", "poverty_impact")


def test_reform_target_schema_contains_only_bounded_identifiers_and_evidence():
    candidate = _catalogue_candidate()
    schema = reform_target_selection_tool_definition((candidate,))
    text = str(schema)
    assert "p.one" in text
    assert "evidence" in text
    assert "value" not in text
    assert "magnitude" not in text


class _Block:
    type = "tool_use"
    name = "emit_turn_update"

    def __init__(self, update):
        self.input = {"update": update}


class _Response:
    def __init__(self, update):
        self.content = [_Block(update)]
        self.usage = SimpleNamespace(
            input_tokens=2,
            output_tokens=3,
            cache_creation_input_tokens=1,
            cache_read_input_tokens=1,
        )


class _Messages:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0
        self.requests = []

    def create(self, **kwargs):
        self.calls += 1
        self.requests.append(kwargs)
        return next(self.responses)


def test_interpreter_retries_invalid_candidate_and_records_each_call():
    invalid = _start(fields={"year": _field("2026", "2026")}).model_dump(mode="json")
    valid = _start(fields={"year": _field(2026, "2026")}).model_dump(mode="json")
    messages = _Messages([_Response(invalid), _Response(valid)])
    result = interpret_turn(
        InterpreterContext(
            state=_state(),
            active_revision=None,
            active_clarification=None,
            executions={},
            latest_user_message="society in 2026",
        ),
        client=SimpleNamespace(messages=messages),
    )
    assert result.retry_count == 1
    assert len(result.call_usages) == 2
    assert result.usage.input_tokens == 4
    retry_context = json.loads(messages.requests[1]["messages"][0]["content"])
    feedback = retry_context["retry_feedback"]
    assert "non-empty outputs list" in feedback["instruction"]
    assert "year" in feedback["validation_error"]


def test_interpreter_validates_json_encoded_tool_update():
    update = _start(fields={"year": _field(2026, "2026")}).model_dump(mode="json")
    messages = _Messages([_Response(json.dumps(update))])

    result = interpret_turn(
        InterpreterContext(
            state=_state(),
            active_revision=None,
            active_clarification=None,
            executions={},
            latest_user_message="society in 2026",
        ),
        client=SimpleNamespace(messages=messages),
    )

    assert result.validated_update.candidate.fields["year"].value == 2026
    assert messages.calls == 1


def test_interpreter_accepts_starter_prompt_analysis_kind_classification():
    message = "What's the personal allowance?"
    update = StartAnalysis(
        candidate=CandidateAnalysis(
            analysis_kind=_field("parameter_lookup", message),
            fields={
                "parameter_query": _field("personal allowance", message),
            },
        )
    )
    messages = _Messages([_Response(json.dumps(update.model_dump(mode="json")))])

    result = interpret_turn(
        InterpreterContext(
            state=_state(),
            active_revision=None,
            active_clarification=None,
            executions={},
            latest_user_message=message,
        ),
        client=SimpleNamespace(messages=messages),
    )

    assert result.validated_update.candidate.analysis_kind.value == "parameter_lookup"
    assert (
        result.validated_update.candidate.fields["parameter_query"].value
        == "personal allowance"
    )
    assert result.retry_count == 0
    assert messages.calls == 1


def test_candidate_validation_maps_natural_aggregate_vocabulary():
    message = "How many people receive Universal Credit?"
    update = StartAnalysis(
        candidate=CandidateAnalysis(
            analysis_kind=_field("society", message),
            fields={
                "variable_query": _field("Universal Credit", "Universal Credit"),
                "aggregate_entity": _field("person", "people"),
                "aggregate_operation": _field("count", "How many"),
            },
            outputs=("caseload",),
            output_evidence=EvidenceClaim(quote=message),
        )
    )

    result = validate_candidate(
        update,
        state=_state(),
        current_revision=None,
        active_clarification=None,
        executions={},
        user_message=message,
    )

    assert result.candidate.fields["aggregate_entity"].value == "person"
    assert result.candidate.fields["aggregate_operation"].value == "count"


class _SelectionBlock:
    type = "tool_use"
    name = "emit_reform_targets"
    input = {
        "selections": [
            {"identifier": "p.one", "label": "One", "evidence": "one"}
        ]
    }


def test_reform_selector_can_select_only_supplied_target():
    response = SimpleNamespace(
        content=[_SelectionBlock()],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
    selected = select_reform_targets(
        ReformTargetSelectionRequest(
            intent="increase one",
            candidates=(_catalogue_candidate(),),
            year=2026,
            session_id="session_test",
            turn_id="turn_test",
        ),
        client=client,
    )
    assert [item.identifier for item in selected.bindings] == ["p.one"]
    assert selected.usage_entry.operation == "reform_target_selection"
