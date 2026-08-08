from pathlib import Path

import pytest

from eval.loaders import load_case_file
from gateway.intent import (
    OutputIntent,
    output_from_prompt,
    reform_intent_from_prompt,
    upsert_output_slot,
)
from gateway.policy import SlotFact


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("What is the cost of increasing Child Benefit by £5?", "budgetary_impact"),
        ("What is the budgetary cost of increasing Child Benefit by £5?", "budgetary_impact"),
        ("What annual revenue comes from raising the basic rate by 1pp?", "tax_revenue"),
        ("Show the tax revenue from raising the basic rate by 1pp.", "tax_revenue"),
        ("How would increasing UC by 5% affect poverty?", "poverty_impact"),
        ("What is the child poverty effect of increasing UC by 5%?", "poverty_impact"),
        ("Show the impact by decile of raising the allowance by £500.", "decile_impact"),
        ("Give the distributional impact by decile.", "decile_impact"),
        ("How many people would gain or lose from the reform?", "winners_losers"),
        ("How many households gain from increasing UC by £500?", "winners_losers"),
        ("Show affected-household counts after increasing UC by £500.", "winners_losers"),
    ],
)
def test_output_phrases(prompt, expected):
    intent = output_from_prompt(prompt)

    assert intent is not None
    assert intent.value == expected
    assert intent.evidence in prompt


def test_specific_distributional_outputs_precede_generic_fiscal_words():
    assert output_from_prompt("What is the cost by decile of increasing UC by 5%?").value == "decile_impact"
    assert output_from_prompt("What is the cost and who would gain or lose?").value == "winners_losers"


@pytest.mark.parametrize(
    "prompt",
    [
        "Discuss the cost of living crisis.",
        "Why is the cost of living rising?",
        "Compare living costs across the UK.",
    ],
)
def test_generic_cost_of_living_is_not_executable_output(prompt):
    assert output_from_prompt(prompt) is None


@pytest.mark.parametrize(
    ("prompt", "action", "policy", "amount", "scope"),
    [
        (
            "Increase all employee National Insurance rates by one percentage point.",
            "increase",
            "employee National Insurance rates",
            "one percentage point",
            "all",
        ),
        (
            "Reduce the Universal Credit taper rate by two percentage points.",
            "decrease",
            "Universal Credit taper rate",
            "two percentage points",
            "unspecified",
        ),
        ("Set the personal allowance to £15,000.", "set", "personal allowance", "£15,000", "unspecified"),
        ("Abolish the two-child limit.", "abolish", "two-child limit", None, "unspecified"),
        ("Freeze every income tax threshold.", "freeze", "income tax threshold", None, "every"),
        ("Uprate both disability premiums by 4%.", "uprate", "disability premiums", "4%", "both"),
        ("Replace the UC taper with a 50% rate.", "replace", "UC taper", "a 50% rate", "unspecified"),
        ("Double Universal Credit.", "multiply", "Universal Credit", "2x", "unspecified"),
        (
            "Raise the lowest capital gains tax rate from 18% to 20%.",
            "set",
            "lowest capital gains tax rate",
            "20%",
            "unspecified",
        ),
    ],
)
def test_reform_extraction(prompt, action, policy, amount, scope):
    intent = reform_intent_from_prompt(prompt)

    assert intent is not None
    assert intent.action == action
    assert intent.policy_phrase == policy
    assert intent.amount == amount
    assert intent.scope == scope
    assert intent.evidence in prompt


@pytest.mark.parametrize(
    "prompt",
    [
        "Model a wealth tax.",
        "Compare the two reforms.",
        "Increase it by 5%.",
        "Reduce this by £500.",
    ],
)
def test_incomplete_or_pronoun_only_reforms_are_rejected(prompt):
    assert reform_intent_from_prompt(prompt) is None


def test_upsert_output_replaces_assumed_but_not_explicit_prompt_output():
    inferred = OutputIntent("poverty_impact", "poverty")
    assumed = [SlotFact("output", "assumed", kind="output")]
    explicit = [SlotFact("output", "prompt", kind="output", value="decile_impact")]

    assert upsert_output_slot(assumed, inferred) == [
        SlotFact("output", "prompt", kind="output", value="poverty_impact")
    ]
    assert upsert_output_slot(explicit, inferred) == explicit


def test_prompt_year_overrides_a_classifier_default():
    from gateway.intent import upsert_prompt_year

    slots = [SlotFact("year", "default", value="2026")]

    assert upsert_prompt_year("get_parameter", slots, "Show the value in 2025") == [
        SlotFact("year", "prompt", value="2025")
    ]


def test_all_population_prompts_have_output_and_reform_intent():
    path = Path(__file__).parents[2] / "evals/cases/tool_loop/uk_population_live.yaml"
    cases = load_case_file(path)

    assert len(cases) == 20
    for case in cases:
        assert output_from_prompt(case.prompt) is not None, case.id
        assert reform_intent_from_prompt(case.prompt) is not None, case.id


def test_population_parameter_families_state_variant_scope():
    path = Path(__file__).parents[2] / "evals/cases/tool_loop/uk_population_live.yaml"
    cases = {case.id: case for case in load_case_file(path)}

    for suffix in ("cost", "child_poverty"):
        prompt = cases[f"uk_population_uc_child_element_10pw_{suffix}"].prompt
        assert "standard child element only" in prompt
        assert "higher first-child amount" in prompt
    for suffix in ("cost", "households"):
        prompt = cases[f"uk_population_uc_work_allowance_500_{suffix}"].prompt
        assert "both Universal Credit work allowances" in prompt
        assert "with and without housing support" in prompt
