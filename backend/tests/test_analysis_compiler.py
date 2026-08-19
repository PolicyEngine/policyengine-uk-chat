from __future__ import annotations

import inspect

import pytest
from pydantic import ValidationError

from analysis.common import AnalysisError, AnalysisErrorCode, RuntimeVersions
from analysis.compiler import ExecutionPlanCompiler, compile_plan, plan_is_stale, validate_plan
from analysis.models import ExecutionMode, ExecutionPlan, PlanStep
from analysis_helpers import VERSIONS, bound_request


@pytest.mark.parametrize(
    ("kind", "fields", "outputs", "operations"),
    [
        ("explanation", {}, (), ()),
        ("parameter_lookup", {"parameter_query": "income tax"}, ("parameter_lookup",), ("get_parameter",)),
        ("reform_validation", {"reform": {"p": 1}}, ("reform_validity",), ("validate_reform",)),
        ("household", {"people": [{"age": 40}]}, ("net_income",), ("validate_household", "run_household_simulation")),
        ("household", {"people": [{"age": 40}]}, ("benefit_entitlement",), ("validate_household", "run_household_simulation")),
        ("society", {}, ("budgetary_impact",), ("run_society_simulation", "compute_budgetary_impact")),
        ("society", {}, ("tax_revenue",), ("run_society_simulation", "compute_budgetary_impact")),
        ("society", {}, ("benefit_spending",), ("run_society_simulation", "compute_budgetary_impact")),
        ("society", {}, ("poverty_impact",), ("run_society_simulation", "compute_poverty_metrics")),
        ("society", {}, ("inequality_impact",), ("run_society_simulation", "compute_inequality_metrics")),
        ("society", {}, ("decile_impact",), ("run_society_simulation", "compute_decile_impacts")),
        ("society", {}, ("winners_losers",), ("run_society_simulation", "compute_winners_losers")),
        ("society", {}, ("program_breakdown",), ("run_society_simulation", "compute_program_breakdown")),
        (
            "society",
            {
                "variable_query": "net income",
                "aggregate_entity": "household",
                "aggregate_operation": "sum",
            },
            ("aggregate",),
            ("run_society_simulation", "aggregate_result"),
        ),
        (
            "society",
            {"variable_query": "net income", "aggregate_entity": "household"},
            ("caseload",),
            ("run_society_simulation", "aggregate_result"),
        ),
        (
            "society",
            {"variable_query": "net income", "aggregate_entity": "household"},
            ("marginal_rate",),
            ("run_society_simulation", "aggregate_result"),
        ),
        (
            "society",
            {"chart_kind": "budget_waterfall"},
            ("chart",),
            (
                "run_society_simulation",
                "compute_budgetary_impact",
                "generate_chart",
            ),
        ),
    ],
)
def test_standard_templates_compile_exact_operations(kind, fields, outputs, operations):
    request = bound_request(kind, fields=fields, outputs=outputs)
    plan = compile_plan(request)
    assert tuple(step.operation for step in plan.steps) == operations
    assert plan.mode == (
        ExecutionMode.EXPLANATION if kind == "explanation" else ExecutionMode.STANDARD
    )
    assert all(step.result_type for step in plan.steps)


def test_multi_output_plan_shares_simulation_and_compatible_derivative():
    plan = compile_plan(
        bound_request(
            outputs=("budgetary_impact", "tax_revenue", "poverty_impact")
        )
    )
    operations = [step.operation for step in plan.steps]
    assert operations.count("run_society_simulation") == 1
    assert operations.count("compute_budgetary_impact") == 1
    assert operations.count("compute_poverty_metrics") == 1


def test_chart_compiles_explicit_typed_dependency():
    plan = compile_plan(
        bound_request(
            fields={"chart_kind": "budget_waterfall"},
            outputs=("chart",),
        )
    )
    chart = plan.steps[-1]
    assert chart.operation == "generate_chart"
    assert chart.depends_on == ("derive_budgetary_impact",)
    assert chart.arguments["result_id"].expected_result_type == "budgetary_impact"


@pytest.mark.parametrize(
    ("outputs", "allowed"),
    [
        (("budgetary_impact",), ("compute_budgetary_impact",)),
        (("poverty_impact",), ("compute_poverty_metrics",)),
        (("budgetary_impact", "poverty_impact"), ("compute_budgetary_impact", "compute_poverty_metrics")),
    ],
)
def test_exploratory_plan_has_smallest_server_owned_operation_set(outputs, allowed):
    plan = compile_plan(
        bound_request(
            "exploratory",
            fields={"objective": "trace interactions"},
            outputs=outputs,
        )
    )
    assert plan.mode == ExecutionMode.EXPLORATORY
    assert plan.allowed_operations == allowed
    assert plan.max_model_iterations == 4
    assert plan.max_operation_calls == 6
    assert plan.steps[0].operation == "run_society_simulation"


def test_compilation_is_canonical_and_deterministic():
    request = bound_request(outputs=("budgetary_impact", "poverty_impact"))
    first = compile_plan(request)
    second = compile_plan(request)
    assert first.plan_id == second.plan_id
    assert first.plan_hash == second.plan_hash
    assert first.model_dump_json() == second.model_dump_json()


def test_runtime_versions_are_bound_inputs_and_staleness_is_bound_identity_based():
    request = bound_request()
    plan = compile_plan(request)
    assert not plan_is_stale(
        plan,
        current_bound_request_id=request.bound_request_id,
        versions=VERSIONS,
    )
    changed = RuntimeVersions(
        catalogue_version="new",
        engine_version=VERSIONS.engine_version,
        country_package_version=VERSIONS.country_package_version,
        dataset_identifier=VERSIONS.dataset_identifier,
    )
    assert plan_is_stale(
        plan,
        current_bound_request_id=request.bound_request_id,
        versions=changed,
    )


def test_plan_rejects_unknown_dependency():
    valid = compile_plan(bound_request())
    invalid_step = valid.steps[-1].model_copy(update={"depends_on": ("missing",)})
    invalid = valid.model_copy(update={"steps": (*valid.steps[:-1], invalid_step)})
    with pytest.raises(AnalysisError) as raised:
        validate_plan(invalid)
    assert raised.value.code == AnalysisErrorCode.PLAN_INVALID


def test_compiler_accepts_only_bound_request_registry_and_operation_catalogue():
    signature = inspect.signature(ExecutionPlanCompiler.compile)
    assert tuple(signature.parameters) == (
        "request",
        "registry",
        "operation_catalogue",
    )
    source = inspect.getsource(ExecutionPlanCompiler)
    for forbidden in (
        "CandidateTurnUpdate",
        "permitted_operations",
        "max_operation_calls = request",
        "tool_specs",
    ):
        assert forbidden not in source
