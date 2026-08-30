"""Concrete application composition for the capability-oriented chat runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any
from uuid import uuid4

from capabilities.chart import SocietyChartCapability
from capabilities.composition import RuntimeComposition, compose_runtime
from capabilities.follow_up import AnalysisFollowUpCapability
from capabilities.household import (
    AssembleHouseholdCandidateTool,
    HouseholdAnalysisCapability,
    HouseholdAnalysisDraft,
)
from capabilities.policy_information import PolicyInformationCapability
from capabilities.policy_reform import (
    AnthropicReformCandidateResolver,
    PolicyReformCapability,
    PolicyReformInput,
    ResolveReformTool,
)
from capabilities.relevance import (
    AnthropicRelevanceAssessor,
    AssessRelevanceTool,
    ConversationRelevanceCapability,
)
from capabilities.society import SocietyAnalysisCapability, SocietyAnalysisInput
from capabilities.tracing import InvocationTracer
from chat.artifact_context import RepositoryArtifactSummarySource
from chat.capability_service import ChatTurnService
from chat.events import CancellationProbe, ChatEvent
from chat.model_port import AnthropicConversationModel
from chat.turn_input import ChatTurnInput
from persistence.capability_repository import (
    PartialInputRegistry,
    RepositoryArtifactAccess,
    SQLConversationCapabilityRepository,
)
from persistence.context_repository import SQLConversationContextRepository
from persistence.idempotency import SQLIdempotencyRepository
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.analysis_support import build_analysis_support_tools
from tools.context import TurnResultStore
from tools.typed_dispatch import build_dispatch_tools
from conversation_context.reducer import ContextReducer
from conversation_context.change_pipeline import ContextChangeApplier, ContextChangeValidator
from conversation_context.engine_projection import HouseholdEngineFactProjector
from conversation_context.registry import build_default_fact_registry
from conversation_context.tools import (
    AnthropicContextProposalReviewer,
    AnthropicContextInterpreter,
    ApplyContextChangeTool,
    ProposeContextChangeTool,
    ReduceContextPatchTool,
    ValidateContextChangeTool,
)
from conversation_context.variable_resolution import (
    AnthropicVariableMapper,
    ContextChangeResolver,
    ResolveContextChangeTool,
)


@dataclass(frozen=True, slots=True)
class CapabilityChatApplication:
    """Long-lived composition plus request-scoped execution construction."""

    composition: RuntimeComposition
    service: ChatTurnService
    artifacts: RepositoryArtifactAccess

    async def run(
        self,
        turn: ChatTurnInput,
        *,
        is_cancelled: CancellationProbe,
    ) -> AsyncIterator[ChatEvent]:
        effective_turn = (
            turn if turn.turn_id else replace(turn, turn_id=uuid4().hex)
        )
        context = self.composition.executor.context(
            request_id=uuid4().hex,
            conversation_id=effective_turn.session_id,
            turn_id=effective_turn.turn_id,
            is_cancelled=is_cancelled,
            artifacts=self.artifacts,
            result_store=TurnResultStore(),
        )
        async for event in self.service.run(
            effective_turn,
            is_cancelled=is_cancelled,
            context=context,
        ):
            yield event


def build_capability_chat_application(*, engine: Any | None = None) -> CapabilityChatApplication:
    """Assemble every concrete object and validate the dependency graph once."""

    partial_inputs = PartialInputRegistry()
    partial_inputs.register(
        "policy_reform",
        schema_version="1",
        model=PolicyReformInput,
    )
    partial_inputs.register(
        "household_analysis",
        schema_version="1",
        model=HouseholdAnalysisDraft,
    )
    partial_inputs.register(
        "society_analysis",
        schema_version="1",
        model=SocietyAnalysisInput,
    )
    artifact_repository = SQLConversationCapabilityRepository(
        engine=engine,
        partial_inputs=partial_inputs,
    )
    trace_repository = SQLInvocationTraceRepository(engine=engine)
    tracer = InvocationTracer(sink=trace_repository)
    fact_registry = build_default_fact_registry()
    context_repository = SQLConversationContextRepository(engine=engine)
    context_reducer = ContextReducer(fact_registry)

    tools = (
        *build_dispatch_tools(),
        *build_analysis_support_tools(),
        AssessRelevanceTool(AnthropicRelevanceAssessor()),
        ProposeContextChangeTool(AnthropicContextInterpreter()),
        ValidateContextChangeTool(
            ContextChangeValidator(context_reducer, fact_registry),
            AnthropicContextProposalReviewer(),
        ),
        ApplyContextChangeTool(ContextChangeApplier(context_repository)),
        ReduceContextPatchTool(context_reducer),
        ResolveContextChangeTool(
            ContextChangeResolver(
                fact_registry,
                AnthropicVariableMapper(),
            )
        ),
        ResolveReformTool(AnthropicReformCandidateResolver()),
        AssembleHouseholdCandidateTool(),
    )
    capabilities = (
        ConversationRelevanceCapability(),
        PolicyInformationCapability(),
        PolicyReformCapability(),
        HouseholdAnalysisCapability(HouseholdEngineFactProjector(fact_registry)),
        SocietyAnalysisCapability(),
        AnalysisFollowUpCapability(),
        SocietyChartCapability(),
    )
    composition = compose_runtime(
        tools=tools,
        capabilities=capabilities,
        tracer=tracer,
    )
    idempotency = SQLIdempotencyRepository(engine=engine)
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=AnthropicConversationModel(),
        idempotency=idempotency,
        artifact_summaries=RepositoryArtifactSummarySource(artifact_repository),
        context_repository=context_repository,
        fact_registry=fact_registry,
    )
    return CapabilityChatApplication(
        composition=composition,
        service=service,
        artifacts=RepositoryArtifactAccess(artifact_repository),
    )


@lru_cache(maxsize=1)
def get_capability_chat_application() -> CapabilityChatApplication:
    return build_capability_chat_application()
