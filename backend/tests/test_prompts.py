"""Regression tests for active model-facing prompt contracts."""

from chat.capability_service import ChatTurnService
from prompts import SUGGESTION_SYSTEM, TITLE_SYSTEM


def test_conversation_prompt_keeps_capabilities_optional_for_general_questions():
    prompt = ChatTurnService._system_prompt(())

    assert "The conversation is primary" in prompt
    assert "Invoke zero or more public capabilities only when useful" in prompt
    assert "answer directly when no capability is needed" in prompt


def test_conversation_prompt_requires_deterministic_capabilities_for_policy_calculations():
    prompt = ChatTurnService._system_prompt(())

    assert "Government policy formulation, scope, formula" in prompt
    assert "must invoke household_analysis" in prompt
    assert "Population-wide reform or benefit impacts" in prompt


def test_conversation_prompt_explains_transferable_artifacts_and_clarification():
    prompt = ChatTurnService._system_prompt(
        ({"artifact_type": "validated_reform", "summary": "A validated reform"},)
    )

    assert "Compatible retained artifact summaries" in prompt
    assert "A validated reform" in prompt
    assert "which household details require clarification" in prompt


def test_secondary_model_prompts_use_neutral_wording():
    for prompt in (SUGGESTION_SYSTEM, TITLE_SYSTEM):
        assert "neutral, descriptive wording" in prompt
        assert "regressive" in prompt
        assert "punitive" in prompt


def test_title_prompt_formats_fixed_sterling_amounts():
    assert "fixed numeric amount in pounds sterling" in TITLE_SYSTEM
    assert "£{VALUE}" in TITLE_SYSTEM
    assert "'£5', not 'five pounds' or '5 pounds'" in TITLE_SYSTEM
    assert "only for a specified monetary value" in TITLE_SYSTEM
    assert "general use of the word 'pound'" in TITLE_SYSTEM
