"""Illustrative household results must not carry population-ranked variables.

An illustrative household simulation contains exactly one household, so any
variable defined by that household's position within the simulated population
ranks it against itself. `household_income_decile` and `household_wealth_decile`
always return 10, and `in_relative_poverty_bhc`/`in_relative_poverty_ahc` always
return 0, whatever the household earns. Reporting those values as national
positions is the failure these tests pin down.
"""

import pytest

from conftest import requires_policyengine_py
from engine import households as household_engine
from engine.constants import POPULATION_RANKED_HOUSEHOLD_VARIABLES
from tools.context import new_tool_context
from tools.dispatch import execute_tool


YEAR = 2026


def _simulate(income: int) -> dict:
    return execute_tool(
        "run_household_simulation",
        {
            "people": [{"age": 40, "employment_income": income}],
            "year": YEAR,
        },
        new_tool_context(turn_id="test"),
    )


def test_population_ranked_names_cover_deciles_and_relative_poverty():
    assert set(POPULATION_RANKED_HOUSEHOLD_VARIABLES) == {
        "household_income_decile",
        "household_wealth_decile",
        "in_relative_poverty_bhc",
        "in_relative_poverty_ahc",
    }
    for reason in POPULATION_RANKED_HOUSEHOLD_VARIABLES.values():
        assert "simulated population" in reason


def test_drop_removes_names_from_nested_entity_blocks():
    payload = {
        "person": [{"age": 40.0, "household_income_decile": 10.0}],
        "household": {
            "household_net_income": 7618.75,
            "household_income_decile": 10.0,
            "household_wealth_decile": 10.0,
            "in_relative_poverty_bhc": 0.0,
            "in_relative_poverty_ahc": 0.0,
            "in_poverty_bhc": 1.0,
        },
    }

    dropped = household_engine._drop_population_ranked_variables(payload)

    assert dropped == set(POPULATION_RANKED_HOUSEHOLD_VARIABLES)
    assert payload["household"] == {
        "household_net_income": 7618.75,
        "in_poverty_bhc": 1.0,
    }
    assert payload["person"] == [{"age": 40.0}]


def test_drop_reports_nothing_when_no_ranked_variables_present():
    payload = {"household": {"household_net_income": 7618.75}}

    assert household_engine._drop_population_ranked_variables(payload) == set()
    assert payload == {"household": {"household_net_income": 7618.75}}


@requires_policyengine_py
@pytest.mark.parametrize("income", [6000, 200000])
def test_household_simulation_omits_population_ranked_variables(income):
    result = _simulate(income)

    assert "error" not in result, result
    household = result["household"]
    for name in POPULATION_RANKED_HOUSEHOLD_VARIABLES:
        assert name not in household
    omitted = result["omitted_population_ranked_variables"]
    assert set(omitted) == set(POPULATION_RANKED_HOUSEHOLD_VARIABLES)
    assert "compute_decile_impacts" in omitted["household_income_decile"]
    assert "compute_poverty_metrics" in omitted["in_relative_poverty_bhc"]
    # Absolute poverty is a fixed-threshold measure and stays available.
    assert "in_poverty_bhc" in household


@requires_policyengine_py
def test_engine_ranks_a_lone_household_against_itself():
    """Documents why the values are removed rather than merely caveated.

    Read from the engine directly, before removal, to show that the values are
    not merely implausible for one income: they are the same for every income.
    """

    from engine.py_runtime import calculate_household_py

    observed = []
    for income in (3000, 6000, 25000, 200000):
        household = calculate_household_py(
            people=[{"age": 40, "employment_income": income}],
            benunit=None,
            household=None,
            year=YEAR,
        )["household"]
        observed.append(
            {name: household[name] for name in POPULATION_RANKED_HOUSEHOLD_VARIABLES}
        )

    assert observed[0] == {
        "household_income_decile": 10,
        "household_wealth_decile": 10,
        "in_relative_poverty_bhc": 0,
        "in_relative_poverty_ahc": 0,
    }
    assert all(entry == observed[0] for entry in observed)
