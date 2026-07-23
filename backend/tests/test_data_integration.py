"""Opt-in integration tests against the managed Enhanced FRS dataset."""

import math
import os

import pytest

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


def test_enhanced_frs_full_society_derivative_lifecycle():
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
    simulation_id = simulation["result_id"]

    budget = _execute(
        "compute_budgetary_impact",
        {"simulation_id": simulation_id},
        context,
    )
    assert math.isfinite(budget["net_budgetary_impact"])

    programs = _execute(
        "compute_program_breakdown",
        {"simulation_id": simulation_id, "programs": ["income_tax"]},
        context,
    )
    assert [row["program"] for row in programs["programs"]] == ["income_tax"]

    deciles = _execute(
        "compute_decile_impacts",
        {"simulation_id": simulation_id, "basis": "income"},
        context,
    )
    assert [row["decile"] for row in deciles["deciles"]] == list(range(1, 11))
    assert deciles["income_variable"] == "household_net_income"
    assert deciles["decile_variable"] is None
    assert deciles["grouping_variable"] == "household_net_income"
    assert deciles["entity"] == "household"

    winners_losers = _execute(
        "compute_winners_losers",
        {"simulation_id": simulation_id, "basis": "income"},
        context,
    )
    assert {row["decile"] for row in winners_losers["deciles"]} == set(range(11))

    poverty = _execute(
        "compute_poverty_metrics",
        {"simulation_id": simulation_id},
        context,
    )
    assert poverty["rates"]

    inequality = _execute(
        "compute_inequality_metrics",
        {"simulation_id": simulation_id},
        context,
    )
    assert {"gini", "top_10_share", "top_1_share", "bottom_50_share"}.issubset(
        inequality["metrics"]
    )

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


def test_cgt_basic_rate_reaches_default_income_deciles():
    """CGT changes reach household net income but not equivalised HBAI income."""

    context = new_tool_context("enhanced-frs-cgt-deciles")
    simulation = _execute(
        "run_society_simulation",
        {
            "year": 2026,
            "dataset": "enhanced_frs_2023_24",
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
    default_deciles = _execute(
        "compute_decile_impacts",
        {"simulation_id": simulation_id, "basis": "income"},
        context,
    )
    hbai_deciles = _execute(
        "compute_decile_impacts",
        {
            "simulation_id": simulation_id,
            "basis": "income",
            "income_concept": "equiv_hbai_household_net_income",
        },
        context,
    )

    assert budget["net_budgetary_impact"] > 0
    assert default_deciles["income_variable"] == "household_net_income"
    assert any(
        not math.isclose(row["absolute_change"], 0, abs_tol=1e-9)
        for row in default_deciles["deciles"]
    )
    assert hbai_deciles["income_variable"] == "equiv_hbai_household_net_income"
    assert all(
        math.isclose(row["absolute_change"], 0, abs_tol=1e-9)
        for row in hbai_deciles["deciles"]
    )
