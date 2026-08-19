from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.executor import (
    authorize_exploratory_call,
    execute_exploratory_plan,
    execute_standard_plan,
    validate_registered_arguments,
    validate_execution_authority,
)
from analysis.models import ResultEnvelope
from analysis_helpers import plan_and_records
from tools.context import new_tool_context
from tools.registry import tool_definitions, tool_specs


def _dispatch(operation, _arguments, _context):
    if operation == "run_society_simulation":
        return {
            "status": "success",
            "result_id": "society_1",
            "fiscal_year": "2026",
            "year": 2026,
            "dataset": {"name": "test"},
            "reform_applied": False,
        }
    if operation == "compute_budgetary_impact":
        return {
            "status": "success",
            "result_id": "budget_1",
            "tax_revenue": {"baseline": 10, "reform": 11, "change": 1},
            "benefit_spending": {"baseline": 5, "reform": 5, "change": 0},
            "net_budgetary_impact": 1,
        }
    if operation == "compute_poverty_metrics":
        return {
            "status": "success",
            "result_id": "poverty_1",
            "rates": [
                {
                    "poverty_type": "relative_bhc",
                    "group": "all",
                    "baseline_rate": 0.2,
                    "reform_rate": 0.19,
                    "rate_change": -0.01,
                    "relative_change": -0.05,
                    "baseline_headcount": 10,
                    "reform_headcount": 9,
                }
            ],
        }
    if operation == "generate_chart":
        return {
            "status": "success",
            "chart_markdown": "```chart\n{}\n```",
            "spec": {},
            "message": "Chart generated.",
        }
    raise AssertionError(operation)


def _verified(attempt):
    return lambda _execution_id, token: attempt if token == "token" else (_ for _ in ()).throw(
        AnalysisError(AnalysisErrorCode.EXECUTION_TOKEN_INVALID, "invalid token")
    )


def test_standard_execution_validates_outputs_and_typed_dependencies():
    semantic, bound, plan, _state, attempt = plan_and_records()
    outcome = execute_standard_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=_dispatch,
    )
    assert outcome.completion.status == "completed"
    assert [item.result_type for item in outcome.envelopes] == [
        "society_simulation",
        "budgetary_impact",
    ]
    assert outcome.record.fact_register.facts
    assert all("result_id" not in item["summary"] for item in outcome.record.operation_summaries)


def test_standard_execution_resolves_branching_dependencies_from_one_simulation():
    semantic, bound, plan, _state, attempt = plan_and_records(
        outputs=("budgetary_impact", "poverty_impact")
    )
    outcome = execute_standard_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=_dispatch,
    )

    assert outcome.completion.status == "completed"
    assert [item.result_type for item in outcome.envelopes] == [
        "society_simulation",
        "budgetary_impact",
        "poverty_metrics",
    ]
    dependent_steps = [
        step for step in plan.steps if step.operation != "run_society_simulation"
    ]
    assert len(dependent_steps) == 2
    assert all(
        step.depends_on == ("society_simulation",)
        for step in dependent_steps
    )


def test_malformed_success_object_fails_before_result_or_fact_creation():
    semantic, bound, plan, _state, attempt = plan_and_records()

    def malformed(operation, arguments, context):
        if operation == "run_society_simulation":
            return {"result_id": "missing_status"}
        return _dispatch(operation, arguments, context)

    outcome = execute_standard_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=malformed,
    )
    assert outcome.completion.status == "failed"
    assert outcome.completion.error_code == AnalysisErrorCode.RESULT_INVALID.value
    assert outcome.envelopes == ()


def test_unregistered_output_field_fails_before_result_or_fact_creation():
    semantic, bound, plan, _state, attempt = plan_and_records()

    def output_with_private_field(operation, arguments, context):
        result = _dispatch(operation, arguments, context)
        if operation == "run_society_simulation":
            return {**result, "internal_secret": "must not be persisted"}
        return result

    outcome = execute_standard_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=output_with_private_field,
    )

    assert outcome.completion.status == "failed"
    assert outcome.completion.error_code == AnalysisErrorCode.RESULT_INVALID.value
    assert outcome.envelopes == ()


@pytest.mark.parametrize(
    "output",
    [
        {
            "status": "success",
            "result_id": "household_baseline",
            "year": 2026,
            "reform_applied": False,
            "person": {"0": {"income_tax": 100}},
            "benunit": {"universal_credit": 50},
            "household": {"household_net_income": 1000},
        },
        {
            "status": "success",
            "result_id": "household_reform",
            "year": 2026,
            "reform_applied": True,
            "baseline": {
                "person": {"0": {"income_tax": 100}},
                "benunit": {"universal_credit": 50},
                "household": {"household_net_income": 1000},
            },
            "reform": {
                "person": {"0": {"income_tax": 90}},
                "benunit": {"universal_credit": 50},
                "household": {"household_net_income": 1010},
            },
        },
    ],
)
def test_household_output_contract_accepts_only_explicit_result_shapes(output):
    spec = next(
        item for item in tool_specs() if item.name == "run_household_simulation"
    )

    validated = spec.output_adapter.validate_python(output).model_dump(mode="json")

    assert validated["reform_applied"] is output["reform_applied"]
    assert "values" not in validated


def test_household_output_contract_rejects_unknown_top_level_fields():
    spec = next(
        item for item in tool_specs() if item.name == "run_household_simulation"
    )
    output = {
        "status": "success",
        "result_id": "household_baseline",
        "year": 2026,
        "reform_applied": False,
        "person": {"0": {"income_tax": 100}},
        "benunit": {"universal_credit": 50},
        "household": {"household_net_income": 1000},
        "secret_internal_field": "must not be exposed",
    }

    with pytest.raises(ValidationError):
        spec.output_adapter.validate_python(output)


@pytest.mark.parametrize("value", [0, 1, "false", "true"])
def test_household_output_contract_rejects_non_boolean_reform_flags(value):
    spec = next(
        item for item in tool_specs() if item.name == "run_household_simulation"
    )
    output = {
        "status": "success",
        "result_id": "household_baseline",
        "year": 2026,
        "reform_applied": value,
        "person": {"0": {"income_tax": 100}},
        "benunit": {"universal_credit": 50},
        "household": {"household_net_income": 1000},
    }

    with pytest.raises(ValidationError):
        spec.output_adapter.validate_python(output)


def test_registered_input_adapters_enforce_complete_json_schema_constraints():
    specs = {spec.name: spec for spec in tool_specs()}

    assert validate_registered_arguments(
        "search_variables", {"query": "income", "limit": 100}, specs
    ) == {"query": "income", "limit": 100}
    with pytest.raises(AnalysisError):
        validate_registered_arguments(
            "search_variables", {"query": "income", "limit": 101}, specs
        )
    with pytest.raises(AnalysisError):
        validate_registered_arguments(
            "get_parameter", {"path": "gov.test", "year": True}, specs
        )
    with pytest.raises(AnalysisError):
        validate_registered_arguments(
            "run_society_simulation",
            {"extra_variables": {"unknown_entity": ["net_income"]}},
            specs,
        )


def test_cancellation_is_polled_before_each_operation():
    semantic, bound, plan, _state, attempt = plan_and_records()
    calls = []
    outcome = execute_standard_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=lambda *args: calls.append(args),
        is_cancelled=lambda: True,
    )
    assert outcome.completion.status == "cancelled"
    assert calls == []


def test_cancellation_between_operations_preserves_only_completed_results():
    semantic, bound, plan, _state, attempt = plan_and_records()
    calls = []
    cancelled = False

    def dispatch(operation, arguments, context):
        nonlocal cancelled
        calls.append(operation)
        result = _dispatch(operation, arguments, context)
        if operation == "run_society_simulation":
            cancelled = True
        return result

    outcome = execute_standard_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=dispatch,
        is_cancelled=lambda: cancelled,
    )

    assert outcome.completion.status == "cancelled"
    assert calls == ["run_society_simulation"]
    assert [item.result_type for item in outcome.envelopes] == [
        "society_simulation"
    ]


def test_unrelated_conversation_version_is_not_execution_authority():
    _semantic, bound, plan, _state, attempt = plan_and_records()
    assert validate_execution_authority(
        plan=plan,
        attempt=attempt,
        token="token",
        bound_request=bound,
        verify_attempt=_verified(attempt),
    ) == attempt


def test_cross_execution_dependency_is_rejected():
    _semantic, _bound, plan, _state, _attempt = plan_and_records(
        "exploratory",
        fields={"objective": "trace effects"},
    )
    envelope = ResultEnvelope(
        execution_id="other_execution",
        source_step_id="society_simulation",
        result_id="result_other",
        result_type="society_simulation",
        value={"status": "success"},
    )
    with pytest.raises(AnalysisError) as raised:
        authorize_exploratory_call(
            plan=plan,
            execution_id="execution_test",
            operation="compute_budgetary_impact",
            arguments={"simulation_id": {"source_step_id": "society_simulation"}},
            envelopes={"society_simulation": envelope},
            definitions={item["name"]: item for item in tool_definitions()},
        )
    assert raised.value.code == AnalysisErrorCode.RESULT_INVALID


class _ToolBlock:
    type = "tool_use"
    name = "compute_budgetary_impact"
    id = "call_1"
    input = {"simulation_id": {"source_step_id": "society_simulation"}}


class _Messages:
    def __init__(self):
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        content = [_ToolBlock()] if self.calls == 1 else []
        return SimpleNamespace(
            content=content,
            usage=SimpleNamespace(input_tokens=2, output_tokens=1),
        )


def test_exploratory_execution_uses_restricted_tools_and_actual_results():
    semantic, bound, plan, _state, attempt = plan_and_records(
        "exploratory",
        fields={"objective": "trace effects"},
    )
    messages = _Messages()
    outcome = execute_exploratory_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=_dispatch,
        client=SimpleNamespace(messages=messages),
    )
    assert outcome.completion.status == "completed"
    assert {item.result_type for item in outcome.envelopes} >= {
        "society_simulation",
        "budgetary_impact",
    }
    assert len(outcome.usage_entries) == 2


def test_exploratory_execution_checks_cancellation_between_tool_blocks():
    semantic, bound, plan, _state, attempt = plan_and_records(
        "exploratory",
        fields={"objective": "trace effects"},
    )
    cancelled = False
    calls: list[str] = []

    class Block:
        type = "tool_use"
        name = "compute_budgetary_impact"

        def __init__(self, call_id):
            self.id = call_id
            self.input = {
                "simulation_id": {"source_step_id": "society_simulation"}
            }

    def dispatch(operation, arguments, context):
        nonlocal cancelled
        result = _dispatch(operation, arguments, context)
        if operation == "compute_budgetary_impact":
            calls.append(operation)
            cancelled = True
        return result

    response = SimpleNamespace(
        content=[Block("call_1"), Block("call_2")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    outcome = execute_exploratory_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=dispatch,
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response)),
        is_cancelled=lambda: cancelled,
    )

    assert outcome.completion.status == "cancelled"
    assert calls == ["compute_budgetary_impact"]


def test_exploratory_chart_consumes_program_breakdown_from_same_execution():
    semantic, bound, plan, _state, attempt = plan_and_records(
        "exploratory",
        fields={
            "objective": "show program impacts",
            "chart_kind": "program_budget_waterfall",
        },
        outputs=("program_breakdown", "chart"),
    )
    generated_chart_arguments = []

    class Block:
        type = "tool_use"

        def __init__(self, name, call_id, arguments):
            self.name = name
            self.id = call_id
            self.input = arguments

    responses = iter(
        (
            [
                Block(
                    "compute_program_breakdown",
                    "call_programs",
                    {"simulation_id": {"source_step_id": "society_simulation"}},
                )
            ],
            [
                Block(
                    "generate_chart",
                    "call_chart",
                    {
                        "chart_kind": "program_budget_waterfall",
                        "result_id": {"source_step_id": "explore_1"},
                    },
                )
            ],
            [],
        )
    )

    def dispatch(operation, arguments, context):
        if operation == "compute_program_breakdown":
            return {
                "status": "success",
                "result_id": "programs_1",
                "programs": [],
                "net_budgetary_impact": 0,
            }
        if operation == "generate_chart":
            generated_chart_arguments.append(arguments)
        return _dispatch(operation, arguments, context)

    messages = SimpleNamespace(
        create=lambda **_kwargs: SimpleNamespace(
            content=next(responses),
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        )
    )
    outcome = execute_exploratory_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=dispatch,
        client=SimpleNamespace(messages=messages),
    )

    assert outcome.completion.status == "completed"
    assert generated_chart_arguments == [
        {
            "chart_kind": "program_budget_waterfall",
            "result_id": "programs_1",
        }
    ]
    chart_start = next(
        event
        for event in outcome.events
        if event.kind == "start" and event.operation == "generate_chart"
    )
    assert chart_start.arguments == {
        "chart_kind": "program_budget_waterfall",
        "result_id": {"source_step_id": "explore_1"},
    }
    assert "programs_1" not in str(chart_start.arguments)
    assert {item.result_type for item in outcome.envelopes} >= {
        "program_breakdown",
        "chart",
    }
    assert len(outcome.record.response_artifacts) == 1
    assert outcome.record.response_artifacts[0].kind == "chart"
    assert "```chart" in outcome.record.response_artifacts[0].content


def test_exploratory_model_cannot_call_operation_outside_plan():
    semantic, bound, plan, _state, attempt = plan_and_records(
        "exploratory",
        fields={"objective": "trace effects"},
    )

    class BadBlock(_ToolBlock):
        name = "compute_poverty_metrics"

    response = SimpleNamespace(
        content=[BadBlock()],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    outcome = execute_exploratory_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=_dispatch,
        client=SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response)),
    )
    assert outcome.completion.status == "failed"
    assert outcome.completion.error_code == AnalysisErrorCode.OPERATION_NOT_PERMITTED.value


def test_declared_plan_result_type_cannot_override_registered_actual_type():
    semantic, bound, plan, _state, attempt = plan_and_records()
    changed_specs = tuple(
        replace(spec, result_type="wrong_type")
        if spec.name == "compute_budgetary_impact"
        else spec
        for spec in tool_specs()
    )
    outcome = execute_standard_plan(
        plan=plan,
        attempt=attempt,
        token="token",
        revision=semantic,
        bound_request=bound,
        verify_attempt=_verified(attempt),
        dispatch=_dispatch,
        specs=changed_specs,
    )
    assert outcome.completion.status == "failed"
    assert outcome.completion.error_code == AnalysisErrorCode.RESULT_INVALID.value


def test_result_store_rejects_cross_execution_handle():
    shared = new_tool_context("turn", execution_id="execution_one")
    result_id = shared.result_store.put(
        "society_simulation",
        {},
        {},
        execution_id="execution_one",
        source_step_id="step",
    )
    with pytest.raises(KeyError):
        shared.result_store.get(result_id, execution_id="execution_two")
