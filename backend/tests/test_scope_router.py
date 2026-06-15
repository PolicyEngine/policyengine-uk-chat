"""Tests for the optional scope router in /chat/message.

The router is a cheap pre-check that decides which *background* a turn needs:
"compute" (full system prompt + reference doc + tools) or "lightweight" (a lean
system prompt, no reference doc, no tools). It never decides the wording — the
model still answers the user's real message in both cases.

These tests stub the classifier, so they never call Anthropic and run offline.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from routes import chatbot


def _stub_client_returning(text: str):
    """Return a stand-in Anthropic client whose .messages.create() yields `text`."""
    response = SimpleNamespace(content=[SimpleNamespace(text=text)])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
    return client


class TestRouteScope:
    """Parser calibration: map the model's one-word reply to a route, biased
    to fail safe toward "compute"."""

    @pytest.mark.parametrize(
        "model_reply,expected",
        [
            ("light", "lightweight"),
            ("lightweight", "lightweight"),
            ("Light", "lightweight"),
            ("LIGHT — off topic", "lightweight"),
            ("compute", "compute"),
            ("Compute.", "compute"),
            ("comp", "compute"),
            ("", "compute"),  # empty reply → fail safe
            ("maybe", "compute"),  # unrecognised → fail safe
            ("I think light", "compute"),  # doesn't start with "light" → fail safe
        ],
    )
    def test_parses_model_reply(self, model_reply, expected):
        with patch.object(chatbot, "_get_sync_anthropic_client", lambda: _stub_client_returning(model_reply)):
            assert chatbot._route_scope("any question") == expected

    def test_empty_input_routes_to_compute(self):
        # No need to call the model when the message is empty.
        with patch.object(chatbot, "_get_sync_anthropic_client", side_effect=AssertionError("should not be called")):
            assert chatbot._route_scope("") == "compute"
            assert chatbot._route_scope("   ") == "compute"

    def test_error_routes_to_compute(self):
        def boom():
            raise RuntimeError("anthropic down")

        with patch.object(chatbot, "_get_sync_anthropic_client", boom):
            assert chatbot._route_scope("What is the capital of France?") == "compute"


class TestLastUserText:
    def test_plain_string_content(self):
        convo = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "latest"},
        ]
        assert chatbot._last_user_text(convo) == "latest"

    def test_flattens_image_text_blocks(self):
        convo = [
            {"role": "user", "content": [
                {"type": "image", "source": {}},
                {"type": "text", "text": "describe this"},
            ]},
        ]
        assert chatbot._last_user_text(convo) == "describe this"

    def test_no_user_message(self):
        assert chatbot._last_user_text([{"role": "assistant", "content": "hi"}]) == ""


class TestLightweightSystemBlocks:
    """The lightweight branch must NOT load the reference doc or a chart
    directive, and must carry the lean prompt."""

    def test_excludes_reference_doc_and_carries_lean_prompt(self):
        blocks = chatbot._build_lightweight_system_blocks()
        assert len(blocks) == 1  # just the lean prompt — no reference doc, no charts
        text = blocks[0]["text"]
        assert "no tools" in text.lower()
        # The full reference doc (if any) must not be present.
        assert chatbot.REFERENCE_DOC == "" or chatbot.REFERENCE_DOC not in text


class TestIsFollowup:
    """The router only runs on the opening user turn; once an assistant turn is
    present the request is a follow-up and takes the full background."""

    def test_opening_turn_is_not_followup(self):
        assert chatbot._is_followup([{"role": "user", "content": "hi"}]) is False

    def test_after_assistant_reply_is_followup(self):
        convo = [
            {"role": "user", "content": "raise the PA to 15000"},
            {"role": "assistant", "content": "Here is the impact..."},
            {"role": "user", "content": "what if they were married?"},
        ]
        assert chatbot._is_followup(convo) is True

    def test_empty_conversation_is_not_followup(self):
        assert chatbot._is_followup([]) is False


class TestRouterConfig:
    def test_router_off_by_default(self):
        assert chatbot.SCOPE_ROUTER_ENABLED is False
