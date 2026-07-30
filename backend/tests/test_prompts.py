"""Regression tests for model-facing prompt contracts."""

import pytest

from tools.definitions import DEFAULT_SIMULATION_YEAR, TOOL_DEFINITIONS
from engine.constants import HOUSEHOLD_COUNTRY_IDS, UK_CHAT_DATASET
from prompts import (
    SYSTEM_PROMPT,
    SUGGESTION_SYSTEM,
    TITLE_SYSTEM,
    gateway_system,
)


def _tool(name: str) -> dict:
    return next(tool for tool in TOOL_DEFINITIONS if tool["name"] == name)


def test_main_prompt_contains_factual_neutrality_rules():
    assert "Be factually neutral." in SYSTEM_PROMPT
    assert "Stick to mechanics and quantified effects." in SYSTEM_PROMPT
    for term in (
        "good",
        "bad",
        "fair",
        "unfair",
        "regressive",
        "progressive",
        "generous",
        "punitive",
    ):
        assert term in SYSTEM_PROMPT


def test_main_prompt_contains_microdata_privacy_rules():
    assert "row-level survey microdata" in SYSTEM_PROMPT
    assert "real households" in SYSTEM_PROMPT
    assert "cannot access or disclose real household records" in SYSTEM_PROMPT
    assert "illustrative synthetic households" in SYSTEM_PROMPT
    assert "exactly one household containing one benefit unit" in SYSTEM_PROMPT
    assert "Do not combine unrelated adults" in SYSTEM_PROMPT


def test_main_prompt_describes_household_country_ids():
    assert HOUSEHOLD_COUNTRY_IDS == (
        "ENGLAND",
        "NORTHERN_IRELAND",
        "SCOTLAND",
        "WALES",
    )
    for country_id in HOUSEHOLD_COUNTRY_IDS:
        assert f"`{country_id}`" in SYSTEM_PROMPT
    assert "do not use ONS codes such as `E92000001`" in SYSTEM_PROMPT


def test_main_prompt_describes_py_lifecycle_tools():
    assert f"default simulation year is {DEFAULT_SIMULATION_YEAR}" in SYSTEM_PROMPT
    assert UK_CHAT_DATASET.name in SYSTEM_PROMPT
    for name in (
        "list_entities",
        "search_variables",
        "list_society_output_variables",
        "search_parameters",
        "list_reform_targets",
        "validate_reform",
        "validate_household",
        "run_household_simulation",
        "run_society_simulation",
        "compute_budgetary_impact",
        "compute_decile_impacts",
        "generate_chart",
    ):
        assert f"`{name}`" in SYSTEM_PROMPT
    assert "Do not run broad Python code for normal analysis" in SYSTEM_PROMPT
    assert "It does not define new" in SYSTEM_PROMPT
    assert "wait for the\n  result before running the simulation" in SYSTEM_PROMPT


def test_main_prompt_distinguishes_three_decile_concepts():
    assert "measure household net income and rank households" in SYSTEM_PROMPT
    assert '`decile_concept="household_net_income"`' in SYSTEM_PROMPT
    assert '`decile_concept="equivalised_hbai_net_income"`' in SYSTEM_PROMPT
    assert '`decile_concept="wealth"`' in SYSTEM_PROMPT
    assert "person-weighted ranks" in SYSTEM_PROMPT
    assert "negative or non-finite values" in SYSTEM_PROMPT
    assert "excluded from" in SYSTEM_PROMPT
    assert "group households by wealth" in SYSTEM_PROMPT
    assert "not describe wealth deciles as income deciles" in SYSTEM_PROMPT
    assert "empty decile has null income impacts" in SYSTEM_PROMPT
    assert "missing results, not zero impacts" in SYSTEM_PROMPT


def test_public_tools_exclude_removed_public_tools():
    names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert "run_python" not in names
    assert "calculate_household" not in names
    assert "run_economy_simulation" not in names
    assert "analyse_microdata" not in names
    assert "lookup_parameter" not in names


def test_validate_reform_tool_is_not_routine_preflight():
    description = _tool("validate_reform")["description"]
    assert "without running a simulation" in description
    assert "routine preflight" in description


def test_society_tool_and_prompt_fix_the_dataset():
    schema = _tool("run_society_simulation")["input_schema"]
    assert "dataset" not in schema["properties"]
    assert f"`{UK_CHAT_DATASET.name}` dataset" in SYSTEM_PROMPT
    assert "cannot select another dataset" in SYSTEM_PROMPT


def test_generate_chart_tool_describes_deterministic_presets():
    chart_tool = _tool("generate_chart")
    description = chart_tool["description"]
    enum = chart_tool["input_schema"]["properties"]["chart_kind"]["enum"]
    assert "deterministic app-v2-style choices" in description
    assert "budget_waterfall" in enum
    assert "decile_relative_bar" in enum
    assert "winners_losers_stacked_bar" in enum


def test_gateway_prompt_renders_caller_supplied_default_year():
    rendered = gateway_system(
        "scope text",
        "- tool - purpose. Required: none.",
        "label_a, label_b",
        DEFAULT_SIMULATION_YEAR,
    )
    assert f"year {DEFAULT_SIMULATION_YEAR}" in rendered
    assert "{default_year}" not in rendered
    assert "not an exhaustive list" in rendered
    assert "catalogue_queries" in rendered


def test_secondary_model_prompts_use_neutral_wording():
    for prompt in (SUGGESTION_SYSTEM, TITLE_SYSTEM):
        assert "neutral, descriptive wording" in prompt
        assert "regressive" in prompt
        assert "punitive" in prompt


def test_system_blocks_preserve_cache_breakpoints_after_prompt_refactor():
    pytest.importorskip("anthropic")

    from chat.system_blocks import build_system_blocks

    on = build_system_blocks(charts_mode=True)
    off = build_system_blocks(charts_mode=False)
    assert on[0] == off[0]
    assert on[0]["text"] == SYSTEM_PROMPT
    assert on[0]["cache_control"] == {"type": "ephemeral"}
    assert "chart mode" in on[-1]["text"]
