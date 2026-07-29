"""Explicit analytical configurations for decile-impact outputs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class DecileConcept(str, Enum):
    """The three decile concepts exposed by UK Chat."""

    HOUSEHOLD_NET_INCOME = "household_net_income"
    EQUIVALISED_HBAI_NET_INCOME = "equivalised_hbai_net_income"
    WEALTH = "wealth"


@dataclass(frozen=True)
class DecileConfiguration:
    """Concrete policyengine.py arguments for one public decile concept."""

    basis: Literal["income", "wealth"]
    income_variable: str
    decile_variable: str | None
    entity: str
    quantiles: int
    measure_label: str
    grouping_label: str


DEFAULT_DECILE_CONCEPT = DecileConcept.HOUSEHOLD_NET_INCOME
DECILE_CONCEPT_VALUES = tuple(concept.value for concept in DecileConcept)

_DECILE_CONFIGURATIONS = {
    DecileConcept.HOUSEHOLD_NET_INCOME: DecileConfiguration(
        basis="income",
        income_variable="household_net_income",
        decile_variable=None,
        entity="household",
        quantiles=10,
        measure_label="household net income",
        grouping_label="Household net income decile",
    ),
    DecileConcept.EQUIVALISED_HBAI_NET_INCOME: DecileConfiguration(
        basis="income",
        income_variable="equiv_hbai_household_net_income",
        decile_variable=None,
        entity="household",
        quantiles=10,
        measure_label="equivalised HBAI net income",
        grouping_label="Equivalised HBAI net income decile",
    ),
    DecileConcept.WEALTH: DecileConfiguration(
        basis="wealth",
        income_variable="household_net_income",
        decile_variable="household_wealth_decile",
        entity="household",
        quantiles=10,
        measure_label="household net income",
        grouping_label="Wealth decile",
    ),
}


def resolve_decile_concept(
    concept: DecileConcept | str,
) -> tuple[DecileConcept, DecileConfiguration]:
    """Validate and resolve a public concept to policyengine.py arguments."""

    try:
        resolved = DecileConcept(concept)
    except ValueError as exc:
        expected = ", ".join(DECILE_CONCEPT_VALUES)
        raise ValueError(
            f"Unknown decile_concept {concept!r}; expected one of: {expected}."
        ) from exc
    return resolved, _DECILE_CONFIGURATIONS[resolved]
