"""UK Chat-owned grouping for person-weighted income deciles."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import numpy as np
import pandas as pd
from microdf import MicroSeries


TEMPORARY_DECILE_VARIABLE = "__uk_chat_person_weighted_income_decile"


@contextmanager
def income_decile_grouping(
    baseline_simulation: Any,
    *,
    income_variable: str,
    entity: str,
) -> Iterator[str]:
    """Temporarily attach person-weighted income-decile assignments.

    The caller remains responsible for passing the returned column name to an
    official policyengine.py output class. The temporary column is restored or
    removed even when that output raises.
    """

    if entity != "household":
        raise ValueError(
            "Person-weighted income deciles currently require the household entity."
        )

    baseline_data = getattr(
        baseline_simulation.output_dataset.data,
        entity,
    )
    income = baseline_data[income_variable]
    person_weights = (
        baseline_data["household_weight"] * baseline_data["household_count_people"]
    )
    weighted_income = MicroSeries(
        income,
        weights=np.asarray(person_weights),
    )
    deciles = pd.Series(
        np.asarray(weighted_income.decile_rank()),
        index=baseline_data.index,
        dtype=int,
    )

    had_previous_value = TEMPORARY_DECILE_VARIABLE in baseline_data.columns
    previous_value = (
        baseline_data[TEMPORARY_DECILE_VARIABLE].copy() if had_previous_value else None
    )
    baseline_data[TEMPORARY_DECILE_VARIABLE] = deciles
    try:
        yield TEMPORARY_DECILE_VARIABLE
    finally:
        if had_previous_value:
            baseline_data[TEMPORARY_DECILE_VARIABLE] = previous_value
        else:
            del baseline_data[TEMPORARY_DECILE_VARIABLE]


def decile_grouping_metadata(
    *,
    income_variable: str,
    decile_variable: str | None,
) -> dict[str, Any]:
    """Describe the durable grouping semantics without exposing a temp column."""

    if decile_variable is not None:
        return {
            "decile_variable": decile_variable,
            "grouping_variable": decile_variable,
            "grouping_method": "precomputed_variable",
            "grouping_weight_variables": [],
        }
    return {
        "decile_variable": None,
        "grouping_variable": income_variable,
        "grouping_method": "person_weighted_rank",
        "grouping_weight_variables": [
            "household_weight",
            "household_count_people",
        ],
    }
