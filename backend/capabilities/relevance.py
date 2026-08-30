"""Every-turn UK policy relevance assessment with bounded outcomes."""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from capabilities.contracts import Capability, CapabilitySpec, Completed
from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_async_client
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from conversation_context.projection import ContextProjection


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationExcerpt(StrictModel):
    role: str
    content: str


class RelevanceResult(str, Enum):
    RELEVANT = "relevant"
    UNCERTAIN = "uncertain"
    CLEARLY_OUT_OF_SCOPE = "clearly_out_of_scope"


class RelevanceUsage(StrictModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class AssessRelevanceInput(StrictModel):
    current_message: str
    conversation: tuple[ConversationExcerpt, ...]
    context: ContextProjection | None = None


class RelevanceAssessment(StrictModel):
    result: RelevanceResult
    explanation: str
    usage: RelevanceUsage = RelevanceUsage()


class RelevanceAssessor(Protocol):
    async def assess(self, request: AssessRelevanceInput) -> RelevanceAssessment: ...


class AnthropicRelevanceAssessor:
    """One forced structured model call whose output cannot select capabilities."""

    async def assess(self, request: AssessRelevanceInput) -> RelevanceAssessment:
        client = get_async_client()
        tool = {
            "name": "submit_relevance_assessment",
            "description": "Return only the bounded UK Chat relevance result.",
            "input_schema": RelevanceAssessment.model_json_schema(),
        }
        response = await client.messages.create(
            model=DEFAULT_FAST_MODEL,
            max_tokens=500,
            temperature=DEFAULT_TEMPERATURE,
            system=(
                "Assess whether the latest turn concerns supported UK tax, benefit, "
                "government-policy, household-impact, or population-impact discussion. "
                "Return relevant for supported UK content, uncertain when context could "
                "make it relevant, and clearly_out_of_scope only for an explicitly "
                "unsupported jurisdiction or unrelated request. Do not select another "
                "capability, infer calculation inputs, or propose policy values."
            ),
            messages=[
                {
                    "role": "user",
                    "content": request.model_dump_json(),
                }
            ],
            tools=[tool],
            tool_choice={"type": "tool", "name": "submit_relevance_assessment"},
        )
        block = next(
            (
                item
                for item in response.content
                if getattr(item, "type", None) == "tool_use"
                and getattr(item, "name", None) == "submit_relevance_assessment"
            ),
            None,
        )
        if block is None:
            raise RuntimeError("Relevance assessor did not return structured output.")
        usage = getattr(response, "usage", None)
        payload = dict(block.input)
        payload["usage"] = {
            "input_tokens": getattr(usage, "input_tokens", 0),
            "output_tokens": getattr(usage, "output_tokens", 0),
            "cache_creation_input_tokens": getattr(
                usage,
                "cache_creation_input_tokens",
                0,
            ),
            "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0),
        }
        return RelevanceAssessment.model_validate(payload)


class AssessRelevanceTool(Tool[AssessRelevanceInput, RelevanceAssessment]):
    spec = ToolSpec(
        identifier="assess_relevance",
        version="1",
        description="Assess only whether the current turn is within UK Chat scope.",
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.CAPABILITY}),
        input_model=AssessRelevanceInput,
        output_model=RelevanceAssessment,
    )

    def __init__(self, assessor: RelevanceAssessor) -> None:
        self._assessor = assessor

    async def run(
        self,
        tool_input: AssessRelevanceInput,
        context: ToolCallContext,
    ) -> RelevanceAssessment:
        result = await self._assessor.assess(tool_input)
        context.record_model_usage(**result.usage.model_dump())
        return result


class ConversationRelevanceCapability(
    Capability[AssessRelevanceInput, RelevanceAssessment]
):
    spec = CapabilitySpec(
        identifier="conversation_relevance",
        version="1",
        description="Assess the scope of each user turn before normal conversation.",
        required_use="Run once for every user turn.",
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=AssessRelevanceInput,
        output_model=RelevanceAssessment,
        tool_dependencies=("assess_relevance",),
    )

    async def run(self, capability_input: AssessRelevanceInput, context):
        result = await context.invoke_tool("assess_relevance", capability_input)
        if not isinstance(result, RelevanceAssessment):
            raise TypeError("Relevance tool returned an incompatible output.")
        return Completed(value=result)
