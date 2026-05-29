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


def test_run_python_tool_repeats_microdata_contract():
    description = _tool("run_python")["description"]
    assert "row-level survey microdata" in description
    assert "illustrative synthetic households" in description
    assert "Simulation.single_person()" in description
    assert "rather than real households" in description


def test_generate_chart_tool_requires_neutral_titles():
    chart_tool = _tool("generate_chart")
    description = chart_tool["description"]
    title_description = chart_tool["input_schema"]["properties"]["title"]["description"]
    assert "factually neutral" in description
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

    on = _build_system_blocks(plan_mode=True, charts_mode=True)
    off = _build_system_blocks(plan_mode=False, charts_mode=False)
    assert on[0] == off[0]
    assert on[0]["text"] == SYSTEM_PROMPT
    assert on[0]["cache_control"] == {"type": "ephemeral"}
    assert "PLAN MODE IS ACTIVE" in on[-2]["text"]
    assert "chart mode" in on[-1]["text"]
