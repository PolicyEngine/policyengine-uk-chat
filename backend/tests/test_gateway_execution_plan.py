from types import SimpleNamespace

import pytest

from engine.decile_concepts import DEFAULT_DECILE_CONCEPT
from gateway.execution import (
    GatewayExecutionPlan,
    analysis_tool_for_output,
    build_execution_plan,
)
from gateway.intent import ReformIntent
from gateway.policy import SlotFact
from gateway.runtime import GatewayVerdict, serialise_plan_for_system
from tools.definitions import DEFAULT_SIMULATION_YEAR


@pytest.mark.parametrize(
    ("output", "tool"),
    [
        ("budgetary_impact", "compute_budgetary_impact"),
        ("tax_revenue", "compute_budgetary_impact"),
        ("benefit_spending", "compute_budgetary_impact"),
        ("poverty_impact", "compute_poverty_metrics"),
        ("inequality_impact", "compute_inequality_metrics"),
        ("decile_impact", "compute_decile_impacts"),
        ("winners_losers", "compute_winners_losers"),
    ],
)
def test_analysis_tool_for_output(output, tool):
    assert analysis_tool_for_output("run_society_simulation", output) == tool


def test_output_mapping_does_not_override_non_society_tool():
    assert (
        analysis_tool_for_output("run_household_simulation", "budgetary_impact")
        == "run_household_simulation"
    )
    assert analysis_tool_for_output("get_parameter", "tax_revenue") == "get_parameter"


def _intent():
    return ReformIntent(
        policy_phrase="personal allowance",
        action="increase",
        amount="£500",
        scope="unspecified",
        evidence="increasing the personal allowance by £500",
    )


def _assessment():
    return SimpleNamespace(
        reform={"gov.hmrc.income_tax.allowances.personal_allowance.amount": 13_070},
        confidence=93,
        parameter_bindings=(
            SimpleNamespace(
                parameter_path="gov.hmrc.income_tax.allowances.personal_allowance.amount",
                label="Personal allowance",
                catalogue_evidence="personal allowance",
            ),
        ),
    )


def test_society_execution_plan_has_dependency_default_and_conventions():
    plan = build_execution_plan(
        "run_society_simulation",
        [
            SlotFact("year", "default", value=str(DEFAULT_SIMULATION_YEAR)),
            SlotFact("output", "prompt", kind="output", value="decile_impact"),
        ],
        _intent(),
        "How would increasing the personal allowance by £500 affect incomes by decile?",
        _assessment(),
    )

    assert plan.target_tool == "compute_decile_impacts"
    assert plan.prerequisites == ("run_society_simulation",)
    assert plan.approved_reform == _assessment().reform
    assert next(item for item in plan.inputs if item.name == "year").value == str(
        DEFAULT_SIMULATION_YEAR
    )
    conventions = {item.name: item.value for item in plan.conventions}
    assert conventions["comparator"] == "current law"
    assert conventions["population"] == "full modelled population"
    assert conventions["jurisdictions"] == "applicable modelled UK jurisdictions"
    assert conventions["method"] == "direct static microsimulation"
    assert conventions["dataset"]
    assert conventions["decile_concept"] == DEFAULT_DECILE_CONCEPT.value


def test_explicit_year_wins_over_default():
    plan = build_execution_plan(
        "run_society_simulation",
        [
            SlotFact("year", "prompt", value="2025"),
            SlotFact("output", "prompt", kind="output", value="budgetary_impact"),
        ],
        _intent(),
        "Cost in 2025 of increasing the personal allowance by £500",
        _assessment(),
    )

    assert next(item for item in plan.inputs if item.name == "year").value == "2025"


def test_output_kind_selects_derivative_when_classifier_uses_label_as_name():
    plan = build_execution_plan(
        "run_society_simulation",
        [
            SlotFact(
                "budgetary_impact",
                "prompt",
                kind="output",
                value="budgetary_impact",
            )
        ],
        _intent(),
        "What is the annual cost of increasing the personal allowance by £500?",
        _assessment(),
    )

    assert plan.target_tool == "compute_budgetary_impact"
    assert plan.prerequisites == ("run_society_simulation",)


def test_serialized_plan_orders_simulation_before_derivative_and_preserves_evidence():
    plan = build_execution_plan(
        "run_society_simulation",
        [SlotFact("output", "prompt", kind="output", value="budgetary_impact")],
        _intent(),
        "What is the cost of increasing the personal allowance by £500?",
        _assessment(),
    )
    verdict = GatewayVerdict(
        outcome="ready",
        route="compute",
        tool="run_society_simulation",
        reform_intent=_intent(),
        reform_assessment=_assessment(),
        execution_plan=plan,
    )

    serialized = serialise_plan_for_system(verdict)

    assert serialized.index("run_society_simulation") < serialized.index(
        "compute_budgetary_impact"
    )
    assert _intent().evidence in serialized
    assert "runtime handoff" in serialized.lower()
    assert "Personal allowance" in serialized
    assert "13070" in serialized


def test_reform_plan_without_assessment_is_not_executable():
    with pytest.raises(ValueError, match="assessment"):
        build_execution_plan(
            "run_society_simulation",
            [SlotFact("output", "prompt", kind="output", value="budgetary_impact")],
            _intent(),
            "Increase the personal allowance by £500",
            None,
        )


def test_execution_plan_type_is_immutable():
    assert GatewayExecutionPlan.__dataclass_params__.frozen is True
