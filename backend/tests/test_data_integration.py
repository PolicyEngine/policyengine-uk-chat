"""Opt-in integration tests against the managed Enhanced FRS dataset."""

import math
import os

import pytest

from capabilities.society_outputs import validated_aggregate_values
from tools.context import new_tool_context
from tools.dispatch import execute_tool


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DATA_EVALS") != "1",
    reason="set RUN_DATA_EVALS=1 to run Enhanced FRS integration tests",
)


def _execute(name: str, tool_input: dict, context) -> dict:
    result = execute_tool(name, tool_input, context=context)
    assert "error" not in result, result.get("error")
    assert result["status"] == "success"
    return result


@pytest.fixture(scope="module")
def live_society_run():
    context = new_tool_context("enhanced-frs-integration")
    simulation = _execute(
        "run_society_simulation",
        {
            "year": 2026,
            "reform": {
                "gov.hmrc.income_tax.allowances.personal_allowance.amount": 15_000,
            },
        },
        context,
    )
    return context, simulation


def test_live_society_simulation_smoke(live_society_run):
    """Materialize and run one real managed-data baseline/reform pair."""

    _context, simulation = live_society_run

    assert simulation["status"] == "success"
    assert simulation["year"] == 2026
    assert simulation["result_id"].startswith("society_simulation_")


def test_enhanced_frs_full_society_derivative_lifecycle(live_society_run):
    context, simulation = live_society_run
    simulation_id = simulation["result_id"]

    budget = _execute(
        "compute_budgetary_impact",
        {"simulation_id": simulation_id},
        context,
    )
    assert math.isfinite(budget["net_budgetary_impact"])
    assert validated_aggregate_values("budgetary_impact", budget)

    programs = _execute(
        "compute_program_breakdown",
        {"simulation_id": simulation_id, "programs": ["income_tax"]},
        context,
    )
    assert [row["program"] for row in programs["programs"]] == ["income_tax"]
    assert validated_aggregate_values("program_statistics", programs)

    deciles = _execute(
        "compute_decile_impacts",
        {
            "simulation_id": simulation_id,
            "decile_concept": "household_net_income",
        },
        context,
    )
    assert [row["decile"] for row in deciles["deciles"]] == list(range(1, 11))
    assert deciles["income_variable"] == "household_net_income"
    assert deciles["decile_variable"] is None
    assert deciles["grouping_variable"] == "household_net_income"
    assert deciles["entity"] == "household"
    assert deciles["quantiles"] == 10
    assert deciles["measure_label"] == "household net income"
    assert deciles["grouping_label"] == "Household net income decile"
    assert validated_aggregate_values("decile_impacts", deciles)

    winners_losers = _execute(
        "compute_winners_losers",
        {
            "simulation_id": simulation_id,
            "decile_concept": "household_net_income",
        },
        context,
    )
    assert {row["decile"] for row in winners_losers["deciles"]} == set(range(11))
    assert winners_losers["income_variable"] == "household_net_income"
    assert winners_losers["decile_variable"] is None
    assert winners_losers["grouping_variable"] == "household_net_income"
    assert winners_losers["grouping_label"] == "Household net income decile"
    assert validated_aggregate_values("winners_losers", winners_losers)

    poverty = _execute(
        "compute_poverty_metrics",
        {"simulation_id": simulation_id},
        context,
    )
    assert poverty["rates"]
    assert validated_aggregate_values("poverty", poverty)

    inequality = _execute(
        "compute_inequality_metrics",
        {"simulation_id": simulation_id},
        context,
    )
    assert {"gini", "top_10_share", "top_1_share", "bottom_50_share"}.issubset(
        inequality["metrics"]
    )
    assert validated_aggregate_values("inequality", inequality)

    aggregate = _execute(
        "aggregate_result",
        {
            "simulation_id": simulation_id,
            "target": "change",
            "entity": "household",
            "variable": "household_tax",
            "operation": "sum",
        },
        context,
    )
    assert math.isfinite(aggregate["result"]["value"])


def test_cgt_basic_rate_income_deciles_reconcile_with_budgetary_impact():
    """CGT-driven net-income changes reconcile with the fiscal impact."""

    context = new_tool_context("enhanced-frs-cgt-deciles")
    simulation = _execute(
        "run_society_simulation",
        {
            "year": 2026,
            "reform": {"gov.hmrc.cgt.basic_rate": 0.20},
        },
        context,
    )
    simulation_id = simulation["result_id"]

    budget = _execute(
        "compute_budgetary_impact",
        {"simulation_id": simulation_id},
        context,
    )
    income_deciles = _execute(
        "compute_decile_impacts",
        {
            "simulation_id": simulation_id,
            "decile_concept": "household_net_income",
        },
        context,
    )

    assert budget["net_budgetary_impact"] > 0
    assert income_deciles["income_variable"] == "household_net_income"

    total_income_change = 0.0
    for row in income_deciles["deciles"]:
        analysis_weight = (
            row["count_better_off"]
            + row["count_worse_off"]
            + row["count_no_change"]
        )
        if row["absolute_change"] is None:
            assert analysis_weight == 0
            continue
        total_income_change += row["absolute_change"] * analysis_weight

    assert total_income_change < 0
    assert total_income_change == pytest.approx(
        -budget["net_budgetary_impact"],
        rel=0.10,
    )
