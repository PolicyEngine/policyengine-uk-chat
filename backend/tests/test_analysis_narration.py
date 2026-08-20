from __future__ import annotations

import json
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
    assert "oneOf" in str(definition["input_schema"])
    assert "strict" not in definition


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


class _NarrationBlock:
    type = "tool_use"
    name = "emit_narration"

    def __init__(self, value):
        self.input = value


def test_json_encoded_narration_draft_is_validated_and_rendered():
    semantic, _bound, plan, _state, _attempt = plan_and_records()
    response = SimpleNamespace(
        content=[
            _NarrationBlock(
                json.dumps(
                    {
                        "segments": [
                            {"type": "text", "text": "The effect is "},
                            {"type": "fact", "fact_id": "fact_one"},
                            {"type": "text", "text": "."},
                        ]
                    }
                )
            )
        ],
        usage=SimpleNamespace(input_tokens=2, output_tokens=1),
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_: response)
    )

    result = narrate_execution_result(
        revision=semantic,
        plan=plan,
        summaries=(),
        facts=_facts(),
        client=client,
    )

    assert result.content == "The effect is £1.00 million."
    assert len(result.call_usages) == 1


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
    retry_payload = json.loads(messages.calls[1]["messages"][0]["content"])
    assert "retry_feedback" not in json.loads(
        messages.calls[0]["messages"][0]["content"]
    )
    assert "no numerical characters" in retry_payload["retry_feedback"][
        "instruction"
    ]
