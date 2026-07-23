"""Tests for UK Chat's temporary person-weighted decile grouping."""

from types import SimpleNamespace

import pandas as pd
import pytest
from microdf import MicroDataFrame

from engine.decile_grouping import (
    TEMPORARY_DECILE_VARIABLE,
    person_weighted_income_decile,
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

    with person_weighted_income_decile(
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

    with person_weighted_income_decile(
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
        with person_weighted_income_decile(
            _simulation(household),
            income_variable="household_net_income",
            entity="household",
        ):
            raise RuntimeError("output failed")

    assert household[TEMPORARY_DECILE_VARIABLE].tolist() == [91, 92]


def test_person_weighted_deciles_reject_non_household_entities():
    with pytest.raises(ValueError, match="require the household entity"):
        with person_weighted_income_decile(
            SimpleNamespace(),
            income_variable="person_net_income",
            entity="person",
        ):
            pass
