import asyncio
from types import SimpleNamespace

from chat import suggestions


class FakeMessages:
    def __init__(self, content=None, error=None):
        self.content = content
        self.error = error
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(content=self.content)


def run_suggestions(monkeypatch, raw, *, question="Question", answer="Answer"):
    messages = FakeMessages(
        content=[
            SimpleNamespace(type="ignored", text="ignore"),
            SimpleNamespace(type="text", text=raw),
        ]
    )
    monkeypatch.setattr(
        suggestions,
        "get_async_client",
        lambda: SimpleNamespace(messages=messages),
    )
    result = asyncio.run(
        suggestions.generate_followup_suggestions(question, answer)
    )
    return result, messages


def test_suggestions_skip_empty_answers_without_calling_provider(monkeypatch):
    monkeypatch.setattr(
        suggestions,
        "get_async_client",
        lambda: (_ for _ in ()).throw(AssertionError("provider should not run")),
    )
    assert asyncio.run(suggestions.generate_followup_suggestions("Question", "  ")) == []


def test_suggestions_parse_fenced_dict_and_clean_results(monkeypatch):
    long_question = "x" * 130
    raw = (
        "```json\n"
        + '{"suggestions":[" First? ","First?",7,"","'
        + long_question
        + '","Third?","Fourth?"]}'
        + "\n```"
    )

    result, messages = run_suggestions(
        monkeypatch,
        raw,
        question=" q " * 1000,
        answer=" a " * 3000,
    )

    assert result == ["First?", "x" * 117 + "...", "Third?"]
    call = messages.calls[0]
    assert call["model"] == suggestions.SUGGESTION_MODEL
    assert call["temperature"] == suggestions.SUGGESTION_TEMPERATURE
    assert len(call["messages"][0]["content"]) <= 5550


def test_suggestions_accept_bare_lists_and_questions_key(monkeypatch):
    result, _ = run_suggestions(monkeypatch, '["One?", "Two?"]')
    assert result == ["One?", "Two?"]

    result, _ = run_suggestions(
        monkeypatch,
        '{"suggestions": [], "questions": ["Fallback?"]}',
    )
    assert result == ["Fallback?"]


def test_suggestions_return_empty_for_empty_scalar_or_failed_responses(monkeypatch):
    for raw in ("", '"not-a-list"', "not-json"):
        result, _ = run_suggestions(monkeypatch, raw)
        assert result == []

    messages = FakeMessages(error=RuntimeError("provider unavailable"))
    monkeypatch.setattr(
        suggestions,
        "get_async_client",
        lambda: SimpleNamespace(messages=messages),
    )
    assert asyncio.run(
        suggestions.generate_followup_suggestions("Question", "Answer")
    ) == []
