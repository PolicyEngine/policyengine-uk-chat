"""Regression tests for model-facing prompt contracts."""

import pytest

from agent_tools import TOOL_DEFINITIONS
from prompts import (
    SYSTEM_PROMPT,
    SUGGESTION_SYSTEM,
    TITLE_SYSTEM,
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
    assert "cannot access or disclose real households" in SYSTEM_PROMPT
    assert "illustrative synthetic households" in SYSTEM_PROMPT
    assert "Simulation.single_person()" in SYSTEM_PROMPT
    assert "analyse_microdata` must not be used with FRS" in SYSTEM_PROMPT


def test_main_prompt_prefers_typed_tools_before_python():
    assert "calculate_household" in SYSTEM_PROMPT
    assert "run_economy_simulation" in SYSTEM_PROMPT
    assert "analyse_microdata" in SYSTEM_PROMPT
    assert "validate_reform" in SYSTEM_PROMPT
    assert "routine" in SYSTEM_PROMPT
    assert "preflight" in SYSTEM_PROMPT
    assert "Use `run_python` as the fallback" in SYSTEM_PROMPT


def test_validate_reform_tool_is_not_routine_preflight():
    description = _tool("validate_reform")["description"]
    assert "without running a simulation" in description
    assert "routine preflight" in description


def test_run_python_tool_repeats_microdata_contract():
    description = _tool("run_python")["description"]
    assert "row-level survey microdata" in description
    assert "illustrative synthetic households" in description
    assert "Simulation.single_person()" in description
    assert "rather than real households" in description
    assert "fallback" in description.lower()


def test_analyse_microdata_tool_excludes_frs():
    tool = _tool("analyse_microdata")
    description = tool["description"]
    dataset_schema = tool["input_schema"]["properties"]["dataset"]
    assert "does not support FRS" in description
    assert dataset_schema["default"] == "efrs"
    assert "frs" not in dataset_schema["enum"]


def test_generate_chart_tool_requires_neutral_titles():
    chart_tool = _tool("generate_chart")
    description = chart_tool["description"]
    title_description = chart_tool["input_schema"]["properties"]["title"]["description"]
    assert "factually neutral" in description
    assert "typed calculation tool or `run_python`" in description
    assert "factually neutral" in title_description.lower()


def test_secondary_model_prompts_use_neutral_wording():
    for prompt in (SUGGESTION_SYSTEM, TITLE_SYSTEM):
        assert "neutral, descriptive wording" in prompt
        assert "regressive" in prompt
        assert "punitive" in prompt


def test_system_blocks_preserve_cache_breakpoints_after_prompt_refactor():
    pytest.importorskip("pydantic_ai")
    pytest.importorskip("anthropic")

    from routes.chatbot import _build_system_blocks

    on = _build_system_blocks(charts_mode=True)
    off = _build_system_blocks(charts_mode=False)
    assert on[0] == off[0]
    assert on[0]["text"] == SYSTEM_PROMPT
    assert on[0]["cache_control"] == {"type": "ephemeral"}
    assert "chart mode" in on[-1]["text"]
