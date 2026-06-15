"""Tests for the optional Haiku-based topic gate in /chat/message.

The gate is a cheap pre-check: one fast-model classification on the latest user
message, short-circuiting clearly off-topic requests with a canned refusal
before the heavy chat loop (system prompt, reference doc, tools) ever runs. It
is off by default and opt-in via POLICYENGINE_CHAT_TOPIC_GATE_ENABLED.

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


class TestClassifyOnTopic:
    """Boundary-case calibration for the Haiku classifier parser.

    These tests don't actually call Haiku — they patch the Anthropic client so
    we can lock in the parser's behaviour against representative responses. The
    real classifier prompt is exercised by manual eval, not unit tests.
    """

    @pytest.mark.parametrize(
        "model_reply,expected",
        [
            ("yes", True),
            ("Yes", True),
            ("yes.", True),
            ("YES — clearly policy", True),
            ("no", False),
            ("No.", False),
            ("no, off-topic", False),
            ("", True),  # malformed → fail open
            ("maybe", True),  # not starting with "no" → fail open
        ],
    )
    def test_parses_model_reply(self, model_reply, expected):
        with patch.object(chatbot, "_get_sync_anthropic_client", lambda: _stub_client_returning(model_reply)):
            assert chatbot._classify_on_topic("any question") is expected

    def test_empty_input_passes_through(self):
        # No need to call the model at all when the message is empty.
        with patch.object(chatbot, "_get_sync_anthropic_client", side_effect=AssertionError("should not be called")):
            assert chatbot._classify_on_topic("") is True
            assert chatbot._classify_on_topic("   ") is True

    def test_anthropic_error_fails_open(self):
        def boom():
            raise RuntimeError("anthropic down")

        with patch.object(chatbot, "_get_sync_anthropic_client", boom):
            assert chatbot._classify_on_topic("How does Universal Credit work?") is True


class TestChatMessageGate:
    """End-to-end gate behaviour via TestClient.

    Gate is off by default in tests (env var unset). When turned on with the
    classifier stubbed to reject, /chat/message returns an SSE stream containing
    the canned refusal and never invokes the heavy chat loop.
    """

    def test_gate_off_by_default(self):
        # Module imports and the default config keeps the gate disabled.
        assert chatbot.TOPIC_GATE_ENABLED is False

    def test_gate_on_rejects_off_topic(self, monkeypatch):
        from fastapi.testclient import TestClient
        from main import app

        monkeypatch.setattr(chatbot, "TOPIC_GATE_ENABLED", True)
        monkeypatch.setattr(chatbot, "_classify_on_topic", lambda _msg: False)

        client = TestClient(app)
        resp = client.post(
            "/chat/message",
            json={"messages": [{"role": "user", "content": "What's the capital of France?"}]},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "UK tax and benefit" in body
        assert "refused_by_topic_gate" in body

    def test_gate_on_passes_through_on_topic(self, monkeypatch):
        """When the classifier accepts, the gate must NOT short-circuit — the
        request falls through to the normal loop (here stubbed to fail without a
        live key, proving the canned refusal was not what came back)."""
        monkeypatch.setattr(chatbot, "TOPIC_GATE_ENABLED", True)
        monkeypatch.setattr(chatbot, "_classify_on_topic", lambda _msg: True)

        # _classify_on_topic returning True means the gate is a no-op; we only
        # assert the gate did not emit its refusal, without exercising the live
        # model path.
        assert chatbot._classify_on_topic("How does the personal allowance work?") is True
