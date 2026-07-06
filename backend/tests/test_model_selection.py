"""Model routing tests for reform, distributional, and chart-heavy turns."""

from types import SimpleNamespace

import pytest

from chat.model_selection import _detect_reform_signal, select_chat_model
from config import DEFAULT_FAST_MODEL, DEFAULT_REASONING_MODEL


def _messages(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def _slot(name: str, *, source: str = "prompt", kind: str = "tool_input"):
    return SimpleNamespace(name=name, source=source, kind=kind, value=name)


def _verdict(tool: str, slots: list):
    return SimpleNamespace(outcome="ready", route="compute", tool=tool, slots=slots)


class TestSelectChatModel:
    def test_gateway_reform_slot_routes_to_reasoning_model(self):
        verdict = _verdict("run_economy_simulation", [_slot("reform")])

        assert (
            select_chat_model(_messages("Model the policy change"), gateway_verdict=verdict)
            == DEFAULT_REASONING_MODEL
        )

    def test_gateway_distributional_output_routes_to_reasoning_model(self):
        verdict = _verdict(
            "run_economy_simulation",
            [_slot("decile_impact", kind="output")],
        )

        assert (
            select_chat_model(_messages("Show the distribution"), gateway_verdict=verdict)
            == DEFAULT_REASONING_MODEL
        )

    def test_gateway_baseline_budgetary_query_does_not_force_reasoning_model(self):
        verdict = _verdict(
            "run_economy_simulation",
            [
                _slot("year", source="default"),
                _slot("dataset", source="default"),
                _slot("budgetary_impact", kind="output"),
            ],
        )

        assert (
            select_chat_model(_messages("How much does child benefit cost?"), gateway_verdict=verdict)
            == DEFAULT_FAST_MODEL
        )

    def test_decile_reform_text_routes_to_reasoning_model(self):
        prompt = "Show me the decile impact of a reform raising the personal allowance by 5%"

        assert select_chat_model(_messages(prompt)) == DEFAULT_REASONING_MODEL

    def test_plain_question_routes_to_fast_model(self):
        assert (
            select_chat_model(_messages("What is the personal allowance for 2025?"))
            == DEFAULT_FAST_MODEL
        )

    def test_charts_mode_upgrades_without_reform_signal(self):
        assert (
            select_chat_model(_messages("Plot the income tax schedule"), charts_mode=True)
            == DEFAULT_REASONING_MODEL
        )

    def test_policy_rate_change_pattern_routes_to_reasoning_model(self):
        prompt = "What happens if the basic rate goes from 20% to 25%?"

        assert select_chat_model(_messages(prompt)) == DEFAULT_REASONING_MODEL


@pytest.mark.parametrize(
    "prompt",
    [
        "Revenue increased by 5% last year; explain the chart.",
        "Can you change the wording of the answer?",
        "Please cut the explanation in half.",
        "Replace the table with bullets.",
        "GDP fell by 2% in this example.",
        "What if the interest rate goes from 2% to 3%?",
    ],
)
def test_generic_change_language_does_not_trigger_reform_signal(prompt):
    assert _detect_reform_signal(prompt) is None
    assert select_chat_model(_messages(prompt)) == DEFAULT_FAST_MODEL


def test_attached_image_does_not_inflate_token_estimate():
    # A large base64 image on a trivial question must not be counted as text
    # (which would push the estimate past the fast-model window and misroute
    # the turn to the expensive complex model).
    image_message = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": "A" * 500_000,
                },
            },
            {"type": "text", "text": "What is the personal allowance for 2025?"},
        ],
    }
    assert select_chat_model([image_message]) == DEFAULT_FAST_MODEL

