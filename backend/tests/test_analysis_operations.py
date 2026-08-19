from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType

import pytest

from analysis.capabilities import CAPABILITY_REGISTRY
from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.operations import (
    ChartRecipe,
    OperationCatalogue,
    build_operation_catalogue,
    default_operation_catalogue,
)
from tools.registry import tool_specs


def _assert_catalogue_invalid(action, message: str) -> None:
    with pytest.raises(AnalysisError) as raised:
        action()
    assert raised.value.code == AnalysisErrorCode.CAPABILITY_INVALID
    assert message in str(raised.value)


def test_default_catalogue_validates_all_registered_contracts():
    catalogue = default_operation_catalogue()

    catalogue.validate(CAPABILITY_REGISTRY)
    assert set(catalogue.operations) == {spec.name for spec in tool_specs()}
    assert set(catalogue.producers) == set(CAPABILITY_REGISTRY.producers)
    assert catalogue.chart_recipe("budget_waterfall").source_output == (
        "budgetary_impact"
    )


def test_catalogue_rejects_duplicate_operation_identifiers():
    specifications = tool_specs()

    _assert_catalogue_invalid(
        lambda: build_operation_catalogue(
            specifications=(*specifications, specifications[0])
        ),
        "duplicate identifiers",
    )


def test_catalogue_rejects_partially_registered_operation():
    specifications = tool_specs()
    incomplete = replace(specifications[0], fact_extractor=None)

    _assert_catalogue_invalid(
        lambda: build_operation_catalogue(
            specifications=(incomplete, *specifications[1:])
        ),
        "missing its fact extractor",
    )


def test_catalogue_rejects_chart_recipe_result_type_drift():
    catalogue = default_operation_catalogue()
    recipe = catalogue.chart_recipe("budget_waterfall")
    invalid = OperationCatalogue(
        capability_version=catalogue.capability_version,
        operations=catalogue.operations,
        producers=catalogue.producers,
        chart_recipes=MappingProxyType(
            {
                **catalogue.chart_recipes,
                recipe.chart_kind: replace(
                    recipe,
                    source_result_type="wrong_result_type",
                ),
            }
        ),
    )

    _assert_catalogue_invalid(
        lambda: invalid.validate(CAPABILITY_REGISTRY),
        "source result type differs from its producer",
    )


def test_catalogue_rejects_chart_recipe_with_unknown_operation():
    catalogue = default_operation_catalogue()
    recipe = ChartRecipe(
        chart_kind="unknown_chart",
        source_output="budgetary_impact",
        source_operation="missing_operation",
        source_result_type="budgetary_impact",
    )
    invalid = replace(
        catalogue,
        chart_recipes=MappingProxyType({recipe.chart_kind: recipe}),
    )

    _assert_catalogue_invalid(
        lambda: invalid.validate(CAPABILITY_REGISTRY),
        "names missing source operation",
    )


def test_catalogue_mappings_and_model_definitions_do_not_expose_mutation():
    catalogue = default_operation_catalogue()

    with pytest.raises(TypeError):
        catalogue.operations["invented"] = next(iter(catalogue.operations.values()))

    definitions = catalogue.tool_definitions()
    definitions[0]["input_schema"]["invented"] = True

    assert "invented" not in catalogue.operation(
        str(definitions[0]["name"])
    ).input_schema
