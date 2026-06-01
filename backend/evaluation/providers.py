"""Model-provider adapters for manual AI evaluations."""

import os
from typing import Any, Dict, List, Protocol

from evaluation.schemas import ModelToolCall, ModelTurn


class ModelClient(Protocol):
    def generate(
        self,
        *,
        case_id: str,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]] | None = None,
    ) -> ModelTurn:
        ...


class FakeModelClient:
    """Deterministic provider used by offline eval cases."""

    def __init__(self, turns: Dict[str, ModelTurn]):
        self._turns = turns

    def generate(
        self,
        *,
        case_id: str,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]] | None = None,
    ) -> ModelTurn:
        if case_id not in self._turns:
            raise ValueError(f"No offline response configured for {case_id}")
        return self._turns[case_id]


class AnthropicModelClient:
    """Anthropic adapter behind the provider-neutral eval interface."""

    def __init__(self, model: str | None = None, max_tokens: int = 4000):
        import anthropic

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required for live Anthropic evals")
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or os.environ.get("ANTHROPIC_EVAL_MODEL", "claude-sonnet-4-6")
        self.max_tokens = max_tokens

    def generate(
        self,
        *,
        case_id: str,
        messages: List[Dict[str, Any]],
        system: str,
        tools: List[Dict[str, Any]] | None = None,
    ) -> ModelTurn:
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "temperature": 0,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        response = self.client.messages.create(**kwargs)

        text_parts: List[str] = []
        tool_calls: List[ModelToolCall] = []
        for block in response.content:
            if getattr(block, "type", None) == "text":
                text_parts.append(block.text)
            elif getattr(block, "type", None) == "tool_use":
                tool_input = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ModelToolCall(id=block.id, name=block.name, input=tool_input))

        usage = {}
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", 0),
                "output_tokens": getattr(response.usage, "output_tokens", 0),
            }
        return ModelTurn(text="".join(text_parts), tool_calls=tool_calls, usage=usage)
