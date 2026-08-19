from __future__ import annotations

from types import SimpleNamespace

import pytest

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.models import Fact, FactRegister
from analysis.narration import (
    ApprovedNumberSegment,
    FactReferenceSegment,
    NarrationDraft,
    TextSegment,
    narrate_execution_result,
    narration_tool_definition,
    render_narration,
    validate_narration,
)
from analysis_helpers import plan_and_records


def _facts():
    return FactRegister(
        facts=(
            Fact(
                fact_id="fact_one",
                raw_value=1_000_000,
                unit="GBP",
                display_value="£1.00 million",
                label="Budget effect",
                source_step_id="budget",
            ),
        )
    )


def test_narrator_has_only_structured_prose_tool():
    definition = narration_tool_definition()
    assert definition["name"] == "emit_narration"
    assert "operation" not in str(definition).casefold()


def test_fact_and_approved_number_references_render():
    draft = NarrationDraft(
        segments=(
            TextSegment(text="The effect is "),
            FactReferenceSegment(fact_id="fact_one"),
            TextSegment(text=" in "),
            ApprovedNumberSegment(value_id="year"),
            TextSegment(text="."),
        )
    )
    assert render_narration(
        draft,
        facts=_facts(),
        approved_values={"year": "2026"},
    ) == "The effect is £1.00 million in 2026."


@pytest.mark.parametrize(
    "draft",
    [
        NarrationDraft(segments=(TextSegment(text="It costs £12."),)),
        NarrationDraft(segments=(FactReferenceSegment(fact_id="missing"),)),
        NarrationDraft(segments=(ApprovedNumberSegment(value_id="unknown"),)),
    ],
)
def test_unregistered_numbers_and_references_are_rejected(draft):
    with pytest.raises(AnalysisError) as raised:
        validate_narration(draft, facts=_facts(), approved_values={"year": "2026"})
    assert raised.value.code == AnalysisErrorCode.NARRATION_INVALID


class _Messages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            content=[],
            usage=SimpleNamespace(input_tokens=2, output_tokens=1),
        )


def test_invalid_narration_retries_then_uses_deterministic_fact_summary():
    semantic, _bound, plan, _state, _attempt = plan_and_records()
    messages = _Messages()
    result = narrate_execution_result(
        revision=semantic,
        plan=plan,
        summaries=(),
        facts=_facts(),
        client=SimpleNamespace(messages=messages),
    )
    assert "£1.00 million" in result.content
    assert len(result.call_usages) == 2
    assert result.usage["input_tokens"] == 4
    assert all(
        [tool["name"] for tool in call["tools"]] == ["emit_narration"]
        for call in messages.calls
    )
