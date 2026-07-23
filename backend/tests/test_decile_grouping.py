"""Tests for UK Chat's temporary person-weighted decile grouping."""

from types import SimpleNamespace

import pandas as pd
import pytest
from microdf import MicroDataFrame

from engine.decile_grouping import (
    TEMPORARY_DECILE_VARIABLE,
    decile_grouping_metadata,
    income_decile_grouping,
)


def _simulation(household: MicroDataFrame) -> SimpleNamespace:
    return SimpleNamespace(
        output_dataset=SimpleNamespace(
            data=SimpleNamespace(household=household),
        )
    )


def _household_frame(
    incomes,
    household_weights,
    people_counts,
    *,
    index=None,
) -> MicroDataFrame:
    return MicroDataFrame(
        pd.DataFrame(
            {
                "household_net_income": incomes,
                "household_weight": household_weights,
                "household_count_people": people_counts,
            },
            index=index,
        ),
        weights="household_weight",
    )


def test_income_deciles_multiply_survey_weights_by_household_size():
    household = _household_frame(
        [10, 20, 30, 40],
        [2, 1, 1, 1],
        [2, 1, 1, 1],
        index=[100, 200, 300, 400],
    )

    with income_decile_grouping(
        _simulation(household),
        income_variable="household_net_income",
        entity="household",
    ) as decile_variable:
        assert decile_variable == TEMPORARY_DECILE_VARIABLE
        assert household[decile_variable].tolist() == [6, 8, 9, 10]

    assert TEMPORARY_DECILE_VARIABLE not in household.columns


def test_income_deciles_keep_tied_incomes_in_the_same_group():
    household = _household_frame(
        [10, 10, 20],
        [1, 1, 1],
        [1, 2, 1],
    )

    with income_decile_grouping(
        _simulation(household),
        income_variable="household_net_income",
        entity="household",
    ) as decile_variable:
        assert household[decile_variable].tolist() == [8, 8, 10]


def test_income_decile_grouping_restores_existing_column_after_failure():
    household = _household_frame(
        [10, 20],
        [1, 1],
        [1, 1],
    )
    household[TEMPORARY_DECILE_VARIABLE] = [91, 92]

    with pytest.raises(RuntimeError, match="output failed"):
        with income_decile_grouping(
            _simulation(household),
            income_variable="household_net_income",
            entity="household",
        ):
            raise RuntimeError("output failed")

    assert household[TEMPORARY_DECILE_VARIABLE].tolist() == [91, 92]


def test_person_weighted_deciles_reject_non_household_entities():
    with pytest.raises(ValueError, match="require the household entity"):
        with income_decile_grouping(
            SimpleNamespace(),
            income_variable="person_net_income",
            entity="person",
        ):
            pass


def test_grouping_metadata_hides_temporary_column_name():
    income_metadata = decile_grouping_metadata(
        income_variable="household_net_income",
        decile_variable=None,
    )
    wealth_metadata = decile_grouping_metadata(
        income_variable="household_net_income",
        decile_variable="household_wealth_decile",
    )

    assert income_metadata["decile_variable"] is None
    assert income_metadata["grouping_variable"] == "household_net_income"
    assert TEMPORARY_DECILE_VARIABLE not in income_metadata.values()
    assert wealth_metadata == {
        "decile_variable": "household_wealth_decile",
        "grouping_variable": "household_wealth_decile",
        "grouping_method": "precomputed_variable",
        "grouping_weight_variables": [],
    }
