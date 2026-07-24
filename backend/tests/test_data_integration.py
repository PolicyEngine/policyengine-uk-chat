"""Opt-in integration tests against the managed Enhanced FRS dataset."""

import math
import os

import pytest

from engine.constants import DATASET_LABELS, DEFAULT_UK_DATASET
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
    assert simulation["year"] == 2026
    assert simulation["fiscal_year"] == "2026"
    assert simulation["dataset"]["name"] == DEFAULT_UK_DATASET
    assert simulation["dataset"]["label"] == DATASET_LABELS[DEFAULT_UK_DATASET]
    assert simulation["dataset"]["uri"] != "unavailable"

    budget = _execute(
        "compute_budgetary_impact",
        {"simulation_id": simulation_id},
        context,
    )
    net_budgetary_impact = budget["net_budgetary_impact"]
    assert math.isfinite(net_budgetary_impact)
    assert -50_000_000_000 < net_budgetary_impact < -1_000_000_000

    programs = _execute(
        "compute_program_breakdown",
        {"simulation_id": simulation_id, "programs": ["income_tax"]},
        context,
    )
    assert [row["program"] for row in programs["programs"]] == ["income_tax"]
    assert -50_000_000_000 < programs["programs"][0]["change"] < 0

    deciles = _execute(
        "compute_decile_impacts",
        {"simulation_id": simulation_id, "basis": "income"},
        context,
    )
    assert [row["decile"] for row in deciles["deciles"]] == list(range(1, 11))
    assert all(row["absolute_change"] >= -0.01 for row in deciles["deciles"])

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
    assert all(
        0 <= row[key] <= 1
        for row in poverty["rates"]
        for key in ("baseline_rate", "reform_rate")
    )

    inequality = _execute(
        "compute_inequality_metrics",
        {"simulation_id": simulation_id},
        context,
    )
    assert {"gini", "top_10_share", "top_1_share", "bottom_50_share"}.issubset(
        inequality["metrics"]
    )
    assert all(
        0 <= values[target] <= 1
        for values in inequality["metrics"].values()
        for target in ("baseline", "reform")
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
