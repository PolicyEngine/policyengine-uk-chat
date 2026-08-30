"""Provider boundary for capability-oriented conversational model calls."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_async_client


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class ModelCapabilityCall(StrictModel):
    call_id: str
    capability_id: str
    input: dict[str, object] = Field(default_factory=dict)


class ConversationModelRequest(StrictModel):
    messages: tuple[dict[str, object], ...]
    system: str
    capabilities: tuple[dict[str, object], ...]


class ConversationModelResponse(StrictModel):
    text: str = ""
    capability_calls: tuple[ModelCapabilityCall, ...] = ()
    stop_reason: str | None = None
    model: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ConversationModel(Protocol):
    async def respond(
        self,
        request: ConversationModelRequest,
    ) -> ConversationModelResponse: ...

    async def redraft_numerical(
        self,
        *,
        draft: str,
        unsupported_claims: tuple[str, ...],
        fact_summary: str,
    ) -> ConversationModelResponse: ...


class AnthropicConversationModel:
    def __init__(self, model: str = DEFAULT_FAST_MODEL) -> None:
        self._model = model

    async def respond(
        self,
        request: ConversationModelRequest,
    ) -> ConversationModelResponse:
        client = get_async_client()  # type: ignore[no-untyped-call]
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 16_000,
            "temperature": DEFAULT_TEMPERATURE,
            "system": request.system,
            "messages": list(request.messages),
        }
        if request.capabilities:
            kwargs["tools"] = [
                {
                    "name": item["identifier"],
                    "description": (
                        f"{item['description']} Required-use rule: {item['required_use']}"
                    ),
                    "input_schema": item["input_schema"],
                }
                for item in request.capabilities
            ]
        response = await client.messages.create(**kwargs)
        text = "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
        calls = tuple(
            ModelCapabilityCall(
                call_id=block.id,
                capability_id=block.name,
                input=dict(block.input) if isinstance(block.input, dict) else {},
            )
            for block in response.content
            if getattr(block, "type", None) == "tool_use"
        )
        return ConversationModelResponse(
            text=text,
            capability_calls=calls,
            stop_reason=getattr(response, "stop_reason", None),
            model=self._model,
            usage=self._usage(response),
        )

    async def redraft_numerical(
        self,
        *,
        draft: str,
        unsupported_claims: tuple[str, ...],
        fact_summary: str,
    ) -> ConversationModelResponse:
        client = get_async_client()  # type: ignore[no-untyped-call]
        response = await client.messages.create(
            model=self._model,
            max_tokens=4_000,
            temperature=DEFAULT_TEMPERATURE,
            system=(
                "Correct only the unsupported numerical claims in the draft while "
                "preserving its natural wording and Markdown structure. Every number "
                "in the corrected answer must repeat a verified fact, allowing only "
                "equivalent units, scale, sign, or rounding. Do not calculate a new "
                "total, difference, rate, or other derived value. Remove an unsupported "
                "claim when no verified fact can replace it, and do not repeat any "
                "expression listed as unsupported. Return only the corrected answer."
            ),
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Draft:\n{draft}\n\nUnsupported expressions: "
                        f"{', '.join(unsupported_claims)}\n\nVerified facts:\n{fact_summary}"
                    ),
                }
            ],
        )
        text = "".join(
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        )
        return ConversationModelResponse(
            text=text,
            stop_reason=getattr(response, "stop_reason", None),
            model=self._model,
            usage=self._usage(response),
        )

    @staticmethod
    def _usage(response: Any) -> ModelUsage:
        usage = getattr(response, "usage", None)
        return ModelUsage(
            input_tokens=getattr(usage, "input_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cache_creation_input_tokens=getattr(
                usage,
                "cache_creation_input_tokens",
                0,
            ),
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0),
        )
