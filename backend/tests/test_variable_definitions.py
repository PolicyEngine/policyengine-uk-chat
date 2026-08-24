"""Contract tests for the authoritative variable-definition lookup.

The tool has four outcomes and they must be deterministic: the same query
against the same model version always resolves the same way. Tests that need
the compiled model are gated; the ranking rules are tested against a stub so
they stay pinned even where policyengine.py is unavailable.
"""

from dataclasses import dataclass

import pytest

from conftest import requires_policyengine_py
from engine import definitions
from engine.definitions import (
    MATCH_ALL_TOKENS,
    MATCH_EXACT_LABEL,
    MATCH_EXACT_NAME,
    MATCH_PHRASE,
    get_variable_definition,
    rank_matches,
)
from tools.context import new_tool_context
from tools.definitions import TOOL_DEFINITIONS
from tools.dispatch import execute_tool


@dataclass
class StubVariable:
    name: str
    label: str | None = None
    description: str | None = None
    entity: str = "household"
    adds: list[str] | None = None
    subtracts: list[str] | None = None
    definition_period: str = "year"
    value_type: type = float
    default_value: float = 0.0
    possible_values: list[str] | None = None


class StubModel:
    id = "policyengine-uk@0.0.0-test"
    package_name = "policyengine-uk"
    country_code = "uk"

    def __init__(self, variables):
        self.variables_by_name = {variable.name: variable for variable in variables}
        self.entity_variables = {"household": ["household_net_income"]}


def _stub(monkeypatch, *variables):
    model = StubModel(variables)
    monkeypatch.setattr(definitions, "uk_model_version", lambda: model)
    return model


# --- ranking rules ----------------------------------------------------------


def test_exact_name_beats_every_other_tier(monkeypatch):
    model = _stub(
        monkeypatch,
        StubVariable("council_tax", label="Council tax"),
        StubVariable("council_tax_band", label="Council tax band"),
    )

    matches = rank_matches("council_tax", model)

    assert matches[0].name == "council_tax"
    assert matches[0].tier == MATCH_EXACT_NAME


def test_name_matching_ignores_separators_and_case(monkeypatch):
    model = _stub(monkeypatch, StubVariable("council_tax", label="Council tax"))

    assert rank_matches("Council Tax", model)[0].tier == MATCH_EXACT_NAME


def test_label_match_ranks_above_phrase_and_token_matches(monkeypatch):
    model = _stub(
        monkeypatch,
        StubVariable("cb_amount", label="Child benefit"),
        StubVariable("cb_entitlement", label="Child benefit entitlement amount"),
    )

    matches = rank_matches("child benefit", model)

    assert (matches[0].name, matches[0].tier) == ("cb_amount", MATCH_EXACT_LABEL)
    assert matches[1].tier == MATCH_PHRASE


def test_all_tokens_tier_tolerates_word_order(monkeypatch):
    model = _stub(monkeypatch, StubVariable("tax_income_gross", label="Gross income tax"))

    assert rank_matches("tax gross", model)[0].tier == MATCH_ALL_TOKENS


def test_ranking_is_stable_regardless_of_registry_order(monkeypatch):
    forward = _stub(
        monkeypatch,
        StubVariable("a_net_income", label="A net income"),
        StubVariable("b_net_income", label="B net income"),
    )
    reversed_model = _stub(
        monkeypatch,
        StubVariable("b_net_income", label="B net income"),
        StubVariable("a_net_income", label="A net income"),
    )

    assert [match.name for match in rank_matches("net income", forward)] == [
        match.name for match in rank_matches("net income", reversed_model)
    ]


# --- outcomes ---------------------------------------------------------------


def test_single_match_returns_a_definition(monkeypatch):
    _stub(
        monkeypatch,
        StubVariable(
            "household_net_income",
            label="Household net income",
            adds=["household_market_income"],
            subtracts=["household_tax"],
        ),
    )

    result = get_variable_definition("household_net_income")

    assert result["status"] == "success"
    assert result["matched_on"] == MATCH_EXACT_NAME
    variable = result["variable"]
    assert variable["name"] == "household_net_income"
    assert variable["entity"] == "household"
    assert variable["is_default_society_output"] is True
    assert result["source"]["model"] == "policyengine-uk@0.0.0-test"


def test_ambiguous_query_asks_instead_of_choosing(monkeypatch):
    _stub(
        monkeypatch,
        StubVariable("a_net_income", label="A net income"),
        StubVariable("b_net_income", label="B net income"),
        StubVariable("c_net_income", label="C net income"),
    )

    result = get_variable_definition("net income", limit=2)

    assert result["status"] == "needs_confirmation"
    assert "variable" not in result
    assert result["option_count"] == 3
    assert [option["name"] for option in result["options"]] == [
        "a_net_income",
        "b_net_income",
    ]
    assert all("matched_on" in option for option in result["options"])


def test_unknown_query_returns_an_error_with_suggestions(monkeypatch):
    _stub(monkeypatch, StubVariable("universal_credit", label="Universal credit"))

    result = get_variable_definition("universal crdit")

    assert result["status"] == "error"
    assert result["suggestions"] == ["universal_credit"]
    assert "variable" not in result


def test_query_with_no_word_characters_is_rejected(monkeypatch):
    _stub(monkeypatch, StubVariable("universal_credit"))

    result = get_variable_definition("   ")

    assert result["status"] == "error"
    assert "at least one letter or digit" in result["error"]


@pytest.mark.parametrize("limit,expected", [(0, 1), (99, 10)])
def test_limit_is_clamped_to_the_documented_bounds(monkeypatch, limit, expected):
    _stub(
        monkeypatch,
        *[StubVariable(f"v{index}_net_income", label=f"V{index} net income") for index in range(12)],
    )

    result = get_variable_definition("net income", limit=limit)

    assert result["status"] == "needs_confirmation"
    assert len(result["options"]) == expected
    assert result["option_count"] == 12


# --- formula reporting ------------------------------------------------------


def test_composition_formula_is_reported_as_available(monkeypatch):
    _stub(
        monkeypatch,
        StubVariable(
            "household_net_income",
            label="Household net income",
            adds=["household_market_income", "household_benefits"],
            subtracts=["household_tax"],
        ),
    )

    formula = get_variable_definition("household_net_income")["variable"]["formula"]

    assert formula["available"] is True
    assert formula["kind"] == "composition"
    assert formula["statement"] == (
        "household_net_income = household_market_income + household_benefits "
        "- household_tax"
    )


def test_missing_formula_is_reported_rather_than_reconstructed(monkeypatch):
    _stub(
        monkeypatch,
        StubVariable("universal_credit", label="Universal credit", description="UC award"),
    )

    formula = get_variable_definition("universal_credit")["variable"]["formula"]

    assert formula["available"] is False
    assert formula["kind"] is None
    assert formula["statement"] is None
    assert "no machine-readable formula" in formula["note"]
    assert "not a formula" in formula["note"]


# --- tool seam --------------------------------------------------------------


def test_tool_is_registered_with_a_strict_bounded_schema():
    tool = next(
        item for item in TOOL_DEFINITIONS if item["name"] == "get_variable_definition"
    )
    schema = tool["input_schema"]

    assert schema["additionalProperties"] is False
    assert schema["required"] == ["query"]
    assert set(schema["properties"]) == {"query", "limit"}
    assert schema["properties"]["limit"]["maximum"] == 10
    assert "formula.available" in tool["description"]


@requires_policyengine_py
def test_tool_resolves_a_real_variable_against_the_compiled_model():
    result = execute_tool(
        "get_variable_definition",
        {"query": "household_net_income"},
        new_tool_context(turn_id="test"),
    )

    assert result["status"] == "success"
    formula = result["variable"]["formula"]
    assert formula["available"] is True
    assert "household_market_income" in formula["adds"]
    # The definition must come from the model version that runs simulations.
    assert result["source"]["package"] == "policyengine-uk"
    assert result["source"]["model"].startswith("policyengine-uk@")


@requires_policyengine_py
def test_tool_reports_ambiguity_on_a_real_broad_query():
    result = execute_tool(
        "get_variable_definition",
        {"query": "net income"},
        new_tool_context(turn_id="test"),
    )

    assert result["status"] == "needs_confirmation"
    assert result["option_count"] > 1


@requires_policyengine_py
def test_real_suggestions_surface_the_variable_a_question_names():
    result = execute_tool(
        "get_variable_definition",
        {"query": "what does universal credit include"},
        new_tool_context(turn_id="test"),
    )

    assert result["status"] == "error"
    assert result["suggestions"][0] == "universal_credit"
