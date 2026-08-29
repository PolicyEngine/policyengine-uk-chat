from __future__ import annotations

import asyncio
from decimal import Decimal
import json
import os
from types import SimpleNamespace

import pytest
from sqlmodel import SQLModel, create_engine

import conversation_context.tools as context_tools

from capabilities.composition import compose_runtime
from capabilities.household import (
    AssembleHouseholdCandidateTool,
    HouseholdAnalysisCapability,
    HouseholdAnalysisDraft,
)
from capabilities.policy_reform import (
    PolicyReformCapability,
    PolicyReformInput,
    ResolveReformTool,
)
from capabilities.relevance import (
    AssessRelevanceTool,
    ConversationRelevanceCapability,
    RelevanceAssessment,
    RelevanceResult,
)
from capabilities.tracing import InvocationTracer
from chat.capability_service import ChatTurnService
from chat.events import TurnCompleted
from chat.model_port import (
    ConversationModelResponse,
    ModelCapabilityCall,
)
from chat.turn_input import ChatTurnInput
from conversation_context.engine_projection import HouseholdEngineFactProjector
from conversation_context.models import (
    BooleanFactValue,
    ClaimedMoneyValue,
    ContextEntityCandidate,
    ContextPatch,
    EntityKind,
    EnsureEntityOperation,
    FactClaim,
    FactClaimFieldUpdate,
    FactClaimRelationship,
    FactResolutionStatus,
    IntegerFactValue,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolutionResponse,
    PendingResolutionAction,
    PresentAssertion,
    SetFactOperation,
)
from conversation_context.reducer import ContextReducer
from conversation_context.change_pipeline import ContextChangeApplier, ContextChangeValidator
from conversation_context.registry import build_default_fact_registry
from conversation_context.tools import (
    AnthropicContextProposalReviewer,
    AnthropicContextInterpreter,
    ApplyContextChangeTool,
    ValidateContextChangeTool,
    ProposeContextChangeInput,
    ProposeContextChangeOutput,
    ProposeContextChangeTool,
    ReduceContextPatchTool,
)
from conversation_context.variable_resolution import (
    AnthropicVariableMapper,
    MappingConfidence,
    MappingStatus,
    ContextChangeResolver,
    ResolveContextChangeTool,
    VariableMappingResult,
    VariableMappingSelection,
)
from persistence.capability_repository import (
    PartialInputRegistry,
    RepositoryArtifactAccess,
    SQLConversationCapabilityRepository,
)
from persistence.context_repository import SQLConversationContextRepository
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.analysis_support import (
    ExtractResultFindingsTool,
    VerifyNumericalResponseTool,
)
from tools.typed_dispatch import build_dispatch_tools


class RelevantAssessor:
    async def assess(self, request):
        del request
        return RelevanceAssessment(
            result=RelevanceResult.RELEVANT,
            explanation="supported",
        )


class AgeInterpreter:
    def __init__(self) -> None:
        self.requests = []

    async def propose(self, request):
        self.requests.append(request)
        claims = ()
        if "£50,000" in request.current_message:
            claims = (
                FactClaim(
                    concept="income",
                    definition_key="person.employment_income",
                    subject_references=("person:self",),
                    scope_id="scope:primary-household",
                    value=ClaimedMoneyValue(amount=Decimal("50000")),
                    evidence="£50,000 of income",
                ),
            )
        elif request.current_message.strip() == "33":
            assert len(request.context.pending_questions) == 1
            requirement = request.context.pending_questions[0].requirements[0]
            assert requirement.fact_key == "person.age"
            claims = (
                FactClaim(
                    concept="age",
                    definition_key="person.age",
                    subject_references=(
                        requirement.subject_entity_id or "person:self",
                    ),
                    scope_id=requirement.scope_id,
                    value=IntegerFactValue(value=33),
                    evidence="33",
                ),
            )
        return ProposeContextChangeOutput(
            expected_revision=request.context.revision,
            changes=claims,
        )


class BaselineOnlyReformResolver:
    async def resolve(self, **kwargs):
        raise AssertionError(f"Current law should not require model resolution: {kwargs}")

    async def correct_representation(self, **kwargs):
        raise AssertionError(f"Current law should not require correction: {kwargs}")


class ScriptedConversationModel:
    def __init__(self) -> None:
        self.requests = []
        self.responses = [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="provider-call-first",
                        capability_id="household_analysis",
                        input={
                            "description": "A person with £50,000 of income",
                            "requested_outputs": ["tax"],
                        },
                    ),
                ),
                model="scripted-model",
            ),
            ConversationModelResponse(
                text="What age should I use for this calculation?",
                model="scripted-model",
                stop_reason="end_turn",
            ),
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="provider-call-second",
                        capability_id="household_analysis",
                        input={
                            "description": "The user answered that they are 33.",
                            "requested_outputs": ["tax"],
                        },
                    ),
                ),
                model="scripted-model",
            ),
            ConversationModelResponse(
                text=(
                    "On £50,000 of annual employment income, Income Tax is "
                    "£7,486.00 per year, National Insurance is £2,994.40 per year, "
                    "and household tax is £10,660.45 per year."
                ),
                model="scripted-model",
                stop_reason="end_turn",
            ),
        ]

    async def respond(self, request):
        self.requests.append(request)
        return self.responses.pop(0)

    async def redraft_numerical(self, **kwargs):
        raise AssertionError(f"The verified response should not need redrafting: {kwargs}")


class EmploymentIncomeMapper:
    def __init__(
        self,
        *,
        target_period: MoneyPeriod | None = MoneyPeriod.ANNUAL,
    ) -> None:
        self.calls = 0
        self.target_period = target_period

    async def select(self, **_kwargs):
        self.calls += 1
        return VariableMappingResult(
            selection=VariableMappingSelection(
                status=MappingStatus.MATCHED,
                variable_name="employment_income",
                confidence=MappingConfidence.HIGH,
                target_period=self.target_period,
            )
        )


COLLECTIVE_INCOME_MESSAGES = (
    "What if I were married and we collectively made 70k?",
    "What if I were married and we collectively made 70,000?",
    "What if I were married and we collectively made 70.000?",
    "What if I were married and we collectively made 70 thousand?",
)


class TenTurnInterpreter:
    """Deterministic provider substitute at the typed proposal boundary."""

    def __init__(self) -> None:
        self.requests = []

    async def propose(self, request):
        self.requests.append(request)
        current = request.current_message
        candidates = []
        claims = []
        spouse = next(
            (
                entity
                for entity in request.context.entities
                if entity.relationship_to_user == "spouse"
            ),
            None,
        )
        spouse_reference = spouse.entity_id if spouse is not None else "new:spouse"

        if current == "How much tax would I pay on £50,000 of income?":
            claims.append(
                FactClaim(
                    concept="employment income",
                    definition_key="person.employment_income",
                    subject_references=("person:self",),
                    scope_id="scope:primary-household",
                    value=ClaimedMoneyValue(amount=Decimal("50000")),
                    evidence="£50,000 of income",
                )
            )
        elif current in {"26", "27"}:
            claims.append(self._age("person:self", int(current), evidence=current))
        elif current in COLLECTIVE_INCOME_MESSAGES:
            candidates.append(
                ContextEntityCandidate(
                    reference="new:spouse",
                    kind=EntityKind.PERSON,
                    aliases=("my spouse", "spouse"),
                    relationship_to_user="spouse",
                )
            )
            claims.append(
                FactClaim(
                    concept="married",
                    definition_key="household.is_married",
                    subject_references=("household:primary",),
                    scope_id="scope:primary-household",
                    value=BooleanFactValue(value=True),
                    evidence=current,
                )
            )
            claims.append(
                FactClaim(
                    claim_id="collective-70000",
                    concept="employment income",
                    value=ClaimedMoneyValue(amount=Decimal("70000")),
                    subject_references=("person:self", spouse_reference),
                    relationship=FactClaimRelationship.SUM,
                    evidence=current,
                )
            )
        elif current == "50k for me, 20k for them":
            claims.extend(
                (
                    self._income("person:self", "50000", evidence=current),
                    self._income(spouse_reference, "20000", evidence=current),
                )
            )
        elif current in {"Yes", "Yes, annual"}:
            pending = request.context.pending_fact_resolutions[0]
            claims.append(
                PendingFactResolutionResponse(
                    proposal_id=pending.proposal_id,
                    action=PendingResolutionAction.ACCEPT,
                    evidence=current,
                )
            )
        elif current == "29":
            claims.append(self._age(spouse_reference, 29, evidence=current))
        elif current == "Actually, my spouse is 30.":
            claims.append(
                self._age(spouse_reference, 30, evidence=current, correction=True)
            )
        elif current == "What if our collective employment income were £80,000?":
            claims.append(
                FactClaim(
                    claim_id="collective-80000",
                    concept="employment income",
                    value=ClaimedMoneyValue(amount=Decimal("80000")),
                    subject_references=("person:self", spouse_reference),
                    relationship=FactClaimRelationship.SUM,
                    correction=True,
                    evidence=current,
                )
            )
        elif current == "Use £50,000 for me and £30,000 for my spouse.":
            claims.extend(
                (
                    self._income("person:self", "50000", evidence=current),
                    self._income(
                        spouse_reference,
                        "30000",
                        evidence=current,
                        correction=True,
                    ),
                )
            )
        elif current == "Now use £45,000 for me, keeping my spouse at £30,000.":
            claims.extend(
                (
                    self._income(
                        "person:self",
                        "45000",
                        evidence=current,
                        correction=True,
                    ),
                    self._income(spouse_reference, "30000", evidence=current),
                )
            )

        return ProposeContextChangeOutput(
            expected_revision=request.context.revision,
            candidate_entities=tuple(candidates),
            changes=tuple(claims),
        )

    @staticmethod
    def _income(subject_reference, amount, *, evidence, correction=False):
        return FactClaim(
            concept="employment income",
            definition_key="person.employment_income",
            subject_references=(subject_reference,),
            scope_id="scope:primary-household",
            value=ClaimedMoneyValue(
                amount=Decimal(amount),
            ),
            correction=correction,
            evidence=evidence,
        )

    @staticmethod
    def _age(subject_reference, age, *, evidence, correction=False):
        return FactClaim(
            concept="age",
            definition_key="person.age",
            subject_references=(subject_reference,),
            scope_id="scope:primary-household",
            value=IntegerFactValue(value=age),
            correction=correction,
            evidence=evidence,
        )


class PendingSupplementTenTurnInterpreter:
    """Typed interpreter for period-only and amount-plus-period continuations."""

    def __init__(self, *, replace_total: bool) -> None:
        self.replace_total = replace_total
        self.requests = []

    async def propose(self, request):
        self.requests.append(request)
        current = request.current_message
        candidates = []
        changes = []
        spouse = next(
            (
                entity
                for entity in request.context.entities
                if entity.relationship_to_user == "spouse"
            ),
            None,
        )
        spouse_reference = spouse.entity_id if spouse is not None else "new:spouse"

        if current == "How much tax would I pay on £50,000 of income?":
            changes.append(
                FactClaim(
                    concept="income",
                    definition_key="person.employment_income",
                    subject_references=("person:self",),
                    scope_id="scope:primary-household",
                    value=ClaimedMoneyValue(
                        amount=Decimal("50000"),
                        period=MoneyPeriod.ANNUAL,
                    ),
                    evidence="£50,000 of income",
                )
            )
        elif current == "27":
            changes.append(TenTurnInterpreter._age("person:self", 27, evidence=current))
        elif current == (
            "What if I were married and my spouse and I collectively made £70,000?"
        ):
            candidates.append(
                ContextEntityCandidate(
                    reference="new:spouse",
                    kind=EntityKind.PERSON,
                    aliases=("my spouse", "spouse"),
                    relationship_to_user="spouse",
                )
            )
            changes.extend(
                (
                    FactClaim(
                        concept="married",
                        definition_key="household.is_married",
                        subject_references=("household:primary",),
                        scope_id="scope:primary-household",
                        value=BooleanFactValue(value=True),
                        evidence=current,
                    ),
                    FactClaim(
                        claim_id="supplemented-collective-income",
                        concept="employment income",
                        definition_key="person.employment_income",
                        value=ClaimedMoneyValue(amount=Decimal("70000")),
                        subject_references=("person:self", spouse_reference),
                        relationship=FactClaimRelationship.SUM,
                        evidence="collectively made £70,000",
                    ),
                )
            )
        elif current == "Yes, annual":
            pending = request.context.pending_fact_resolutions[0]
            changes.append(
                PendingFactResolutionResponse(
                    proposal_id=pending.proposal_id,
                    action=PendingResolutionAction.SUPPLY,
                    updates=(
                        FactClaimFieldUpdate(
                            path=("value", "period"),
                            value="annual",
                            evidence="annual",
                        ),
                    ),
                    evidence=current,
                )
            )
        elif current == "Actually, make that £75,000 annually.":
            pending = request.context.pending_fact_resolutions[0]
            changes.append(
                PendingFactResolutionResponse(
                    proposal_id=pending.proposal_id,
                    action=PendingResolutionAction.SUPPLY,
                    updates=(
                        FactClaimFieldUpdate(
                            path=("value", "amount"),
                            value=75000,
                            evidence="£75,000",
                        ),
                        FactClaimFieldUpdate(
                            path=("value", "period"),
                            value="annual",
                            evidence="annually",
                        ),
                    ),
                    evidence=current,
                )
            )
        elif current == "Yes, that breakdown is correct.":
            pending = request.context.pending_fact_resolutions[0]
            changes.append(
                PendingFactResolutionResponse(
                    proposal_id=pending.proposal_id,
                    action=PendingResolutionAction.ACCEPT,
                    evidence=current,
                )
            )
        elif current == "My spouse is 30.":
            changes.append(
                TenTurnInterpreter._age(spouse_reference, 30, evidence=current)
            )
        elif current == "Actually, I earn £55,000 annually.":
            changes.append(
                FactClaim(
                    concept="employment income",
                    definition_key="person.employment_income",
                    subject_references=("person:self",),
                    scope_id="scope:primary-household",
                    value=ClaimedMoneyValue(
                        amount=Decimal("55000"),
                        period=MoneyPeriod.ANNUAL,
                    ),
                    correction=True,
                    evidence=current,
                )
            )
        elif current in {
            "Keep my spouse at £20,000 annually.",
            "Keep my spouse at £25,000 annually.",
        }:
            amount = "25000" if "£25,000" in current else "20000"
            changes.append(
                FactClaim(
                    concept="employment income",
                    definition_key="person.employment_income",
                    subject_references=(spouse_reference,),
                    scope_id="scope:primary-household",
                    value=ClaimedMoneyValue(
                        amount=Decimal(amount),
                        period=MoneyPeriod.ANNUAL,
                    ),
                    evidence=current,
                )
            )

        return ProposeContextChangeOutput(
            expected_revision=request.context.revision,
            candidate_entities=tuple(candidates),
            changes=tuple(changes),
        )


class TenTurnAnthropicMessages:
    """Provider substitute that exercises generic claim-schema repair."""

    def __init__(self, scenario: TenTurnInterpreter) -> None:
        self._scenario = scenario
        self.calls = []

    async def create(self, **kwargs):
        content = kwargs["messages"][0]["content"]
        request_payload, _ = json.JSONDecoder().raw_decode(content)
        request = ProposeContextChangeInput.model_validate(request_payload)
        scenario_output = await self._scenario.propose(request)
        changes = [item.model_dump(mode="json") for item in scenario_output.changes]
        prior_attempts = sum(
            item["current_message"] == request.current_message for item in self.calls
        )
        if (
            request.current_message in COLLECTIVE_INCOME_MESSAGES
            and prior_attempts == 0
        ):
            changes = [
                item for item in changes if item.get("relationship") != "sum"
            ]
        self.calls.append(
            {
                "current_message": request.current_message,
                "provider_content": content,
            }
        )
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="tool_use",
                    name="submit_context_change",
                    input={
                        "expected_revision": scenario_output.expected_revision,
                        "candidate_entities": [
                            item.model_dump(mode="json")
                            for item in scenario_output.candidate_entities
                        ],
                        "changes": changes,
                    },
                )
            ],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=1,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
        )


class TenTurnConversationModel:
    """Invoke household analysis once per user turn, then narrate its typed result."""

    def __init__(self) -> None:
        self.call_count = 0
        self.requests = []

    async def respond(self, request):
        self.requests.append(request)
        last = request.messages[-1]
        if isinstance(last.get("content"), str):
            self.call_count += 1
            return ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id=f"household-call-{self.call_count}",
                        capability_id="household_analysis",
                        input={
                            "description": last["content"],
                            "requested_outputs": ["tax"],
                        },
                    ),
                ),
                model="ten-turn-model",
            )

        result_block = last["content"][0]
        result = json.loads(result_block["content"])
        if result["status"] == "needs_input":
            text = result["prompt"]
        else:
            outputs = result["value"]["result"]["outputs"]
            text = "\n".join(
                f"- {item['label']}: £{item['value']:,.2f} per year"
                for item in outputs
            )
        return ConversationModelResponse(
            text=text,
            model="ten-turn-model",
            stop_reason="end_turn",
        )

    async def redraft_numerical(self, **kwargs):
        raise AssertionError(f"The generated values should be verified: {kwargs}")


async def not_cancelled() -> bool:
    return False


async def collect(service, turn, context):
    return [
        event
        async for event in service.run(
            turn,
            is_cancelled=not_cancelled,
            context=context,
        )
    ]


def test_two_turn_tax_clarification_uses_typed_context_invocation_link(
    tmp_path,
    monkeypatch,
):
    from tools import typed_dispatch

    engine = create_engine(f"sqlite:///{tmp_path / 'chat.sqlite'}")
    SQLModel.metadata.create_all(engine)
    calls = []

    def execute(identifier, payload, context=None):
        calls.append((identifier, payload, context))
        if identifier == "get_variable":
            return {
                "status": "success",
                "variable": {
                    "name": payload["name"],
                    "label": payload["name"].replace("_", " ").title(),
                    "entity": "person",
                },
            }
        if identifier == "search_variables":
            return {
                "status": "success",
                "variables": [
                    {
                        "name": "employment_income",
                        "label": "Employment income",
                        "entity": "person",
                        "definition_period": "year",
                        "value_type": "float",
                    }
                ],
            }
        if identifier == "validate_household":
            return {"valid": True, "year": 2026}
        if identifier == "run_household_simulation":
            return {
                "status": "success",
                "year": 2026,
                "reform_applied": False,
                "income_tax": 7_486,
                "national_insurance": 2_994.40,
                "household_tax": 10_660.45,
            }
        raise AssertionError(f"Unexpected retained tool call: {identifier}")

    monkeypatch.setattr(typed_dispatch, "execute_tool", execute)

    registry = build_default_fact_registry()
    interpreter = AgeInterpreter()
    partial_inputs = PartialInputRegistry()
    partial_inputs.register(
        "household_analysis",
        schema_version="1",
        model=HouseholdAnalysisDraft,
    )
    partial_inputs.register(
        "policy_reform",
        schema_version="1",
        model=PolicyReformInput,
    )
    capability_repository = SQLConversationCapabilityRepository(
        engine=engine,
        partial_inputs=partial_inputs,
    )
    trace_repository = SQLInvocationTraceRepository(engine=engine)
    artifacts = RepositoryArtifactAccess(capability_repository)
    context_repository = SQLConversationContextRepository(engine=engine)
    composition = compose_runtime(
        tools=(
            *build_dispatch_tools(),
            AssessRelevanceTool(RelevantAssessor()),
            ProposeContextChangeTool(interpreter),
            ValidateContextChangeTool(
                ContextChangeValidator(ContextReducer(registry), registry)
            ),
            ApplyContextChangeTool(ContextChangeApplier(context_repository)),
            ReduceContextPatchTool(ContextReducer(registry)),
            ResolveContextChangeTool(
                ContextChangeResolver(
                    registry,
                    EmploymentIncomeMapper(target_period=None),
                )
            ),
            ResolveReformTool(BaselineOnlyReformResolver()),
            AssembleHouseholdCandidateTool(),
            ExtractResultFindingsTool(),
            VerifyNumericalResponseTool(),
        ),
        capabilities=(
            ConversationRelevanceCapability(),
            PolicyReformCapability(),
            HouseholdAnalysisCapability(HouseholdEngineFactProjector(registry)),
        ),
        tracer=InvocationTracer(sink=trace_repository),
    )
    model = ScriptedConversationModel()
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=model,
        context_repository=context_repository,
        fact_registry=registry,
    )

    first_context = composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=not_cancelled,
        artifacts=artifacts,
    )
    first_turn = ChatTurnInput(
        messages=[
            {
                "role": "user",
                "content": "How much tax would I pay on £50,000 of income?",
            }
        ],
        session_id="conversation-1",
        turn_id="turn-1",
        debug=True,
    )

    first_events = asyncio.run(collect(service, first_turn, first_context))

    assert isinstance(first_events[-1], TurnCompleted)
    assert first_events[-1].content == "What age should I use for this calculation?"
    first_persisted = context_repository.load("conversation-1")
    assert first_persisted.active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("50000"),
            period=MoneyPeriod.ANNUAL,
        )
    )
    assert len(first_persisted.pending_questions) == 1
    pending_question = first_persisted.pending_questions[0]
    assert pending_question.question_id != "provider-call-first"
    assert pending_question.capability_invocation is not None
    waiting = capability_repository.list_waiting(
        "conversation-1",
        capability_id="household_analysis",
    )
    assert len(waiting) == 1
    assert pending_question.capability_invocation.invocation_id == waiting[0].invocation_id
    assert waiting[0].reference() == pending_question.capability_invocation
    assert waiting[0].requirements == pending_question.requirements
    assert waiting[0].partial_input.fact_requirements == pending_question.requirements
    assert waiting[0].partial_input.context_scope_id == "scope:primary-household"
    assert waiting[0].partial_input.requested_outputs == (
        "income_tax",
        "national_insurance",
        "household_tax",
    )
    first_model_result = model.requests[1].messages[-1]["content"][0]["content"]
    assert "capability_invocation" not in first_model_result
    assert "resuming_invocation_id" not in first_model_result
    household_definition = next(
        item
        for item in model.requests[0].capabilities
        if item["identifier"] == "household_analysis"
    )
    assert "resuming_invocation_id" not in household_definition["input_schema"][
        "properties"
    ]
    assert "internal invocation identifier" in model.requests[0].system

    # A later malformed or unrelated NeedsInput outcome must not erase the valid
    # pending calculation represented by ConversationContext.
    unchanged = asyncio.run(
        service._sync_pending_context(
            capability_id="household_analysis",
            result={
                "status": "needs_input",
                "prompt": "Unlinked clarification",
                "fact_requirements": [],
            },
            context=first_context.with_conversation_context(first_persisted),
        )
    )
    assert unchanged is None
    assert context_repository.load("conversation-1").pending_questions == (
        pending_question,
    )

    missing_reference = pending_question.capability_invocation.model_copy(
        update={"invocation_id": "missing-waiting-invocation"}
    )
    with pytest.raises(
        RuntimeError,
        match="cannot reference a missing waiting invocation",
    ):
        asyncio.run(
            service._sync_pending_context(
                capability_id="household_analysis",
                result={
                    "status": "needs_input",
                    "prompt": pending_question.prompt,
                    "fact_requirements": [
                        item.model_dump(mode="json")
                        for item in pending_question.requirements
                    ],
                    "capability_invocation": missing_reference.model_dump(
                        mode="json"
                    ),
                },
                context=first_context.with_conversation_context(first_persisted),
            )
        )
    assert context_repository.load("conversation-1").pending_questions == (
        pending_question,
    )

    # Repair a context written by the earlier implementation, which retained the
    # question but omitted its typed link to the otherwise valid waiting record.
    legacy_question = pending_question.model_copy(
        update={"capability_invocation": None}
    )
    legacy_context = first_persisted.model_copy(
        update={"pending_questions": (legacy_question,)}
    )
    context_repository.save(
        legacy_context,
        expected_revision=first_persisted.revision,
    )
    repaired = asyncio.run(
        service._sync_pending_context(
            capability_id="household_analysis",
            result={
                "status": "needs_input",
                "prompt": pending_question.prompt,
                "fact_requirements": [
                    item.model_dump(mode="json")
                    for item in pending_question.requirements
                ],
                "capability_invocation": (
                    pending_question.capability_invocation.model_dump(mode="json")
                ),
            },
            context=first_context.with_conversation_context(legacy_context),
        )
    )
    assert repaired is not None
    assert len(repaired.pending_questions) == 1
    assert repaired.pending_questions[0].capability_invocation == (
        pending_question.capability_invocation
    )
    assert all(
        question.capability_invocation is not None
        for question in repaired.pending_questions
    )

    second_context = composition.executor.context(
        request_id="request-2",
        conversation_id="conversation-1",
        turn_id="turn-2",
        is_cancelled=not_cancelled,
        artifacts=artifacts,
    )
    second_turn = ChatTurnInput(
        messages=[
            {
                "role": "user",
                "content": "How much tax would I pay on £50,000 of income?",
            },
            {
                "role": "assistant",
                "content": "What age should I use for this calculation?",
            },
            {"role": "user", "content": "33"},
        ],
        session_id="conversation-1",
        turn_id="turn-2",
        debug=True,
    )

    second_events = asyncio.run(collect(service, second_turn, second_context))

    assert isinstance(second_events[-1], TurnCompleted)
    assert len(interpreter.requests[1].context.pending_questions) == 1
    projected_question = interpreter.requests[1].context.pending_questions[0]
    assert "capability_invocation" not in projected_question.model_dump(mode="json")
    assert "question_id" not in projected_question.model_dump(mode="json")
    assert "£7,486.00" in second_events[-1].content
    assert "£2,994.40" in second_events[-1].content
    assert "£10,660.45" in second_events[-1].content
    completed_context = context_repository.load("conversation-1")
    age = completed_context.active_fact(
        "person.age",
        "person:self",
        "scope:primary-household",
    )
    assert age is not None
    assert age.assertion == PresentAssertion(value=IntegerFactValue(value=33))
    assert completed_context.pending_questions == ()
    assert capability_repository.list_waiting(
        "conversation-1",
        capability_id="household_analysis",
    ) == ()
    validation_input = next(
        payload
        for identifier, payload, _context in reversed(calls)
        if identifier == "validate_household"
    )
    assert validation_input["people"][0]["age"] == 33
    assert validation_input["people"][0]["employment_income"] == 50_000
    assert validation_input["extra_variables"] == [
        "income_tax",
        "national_insurance",
        "household_tax",
    ]
    assert all(
        identifier not in {"rent", "council_tax"}
        for identifier in validation_input["household"]
        if validation_input["household"][identifier] is None
    )


@pytest.mark.parametrize(
    (
        "user_age",
        "interpretation_mode",
        "allocation_answer",
        "collective_message",
    ),
    (
        (26, "scripted", "50k for me, 20k for them", COLLECTIVE_INCOME_MESSAGES[0]),
        (27, "scripted", "Yes, annual", COLLECTIVE_INCOME_MESSAGES[1]),
        (27, "scripted", "Yes", COLLECTIVE_INCOME_MESSAGES[0]),
        (27, "scripted", "Yes", COLLECTIVE_INCOME_MESSAGES[2]),
        (27, "scripted", "Yes", COLLECTIVE_INCOME_MESSAGES[3]),
        pytest.param(
            27,
            "live",
            "50k for me, 20k for them",
            COLLECTIVE_INCOME_MESSAGES[0],
            marks=pytest.mark.skipif(
                os.environ.get("RUN_LIVE_ANTHROPIC_TESTS") != "1"
                or not os.environ.get("ANTHROPIC_API_KEY"),
                reason=(
                    "set RUN_LIVE_ANTHROPIC_TESTS=1 and ANTHROPIC_API_KEY "
                    "to run the live ten-turn context regression"
                ),
            ),
        ),
    ),
)
def test_ten_user_turns_preserve_people_facts_and_collective_income_resolution(
    tmp_path,
    monkeypatch,
    user_age,
    interpretation_mode,
    allocation_answer,
    collective_message,
):
    from tools import typed_dispatch

    engine = create_engine(f"sqlite:///{tmp_path / 'ten-turn-chat.sqlite'}")
    SQLModel.metadata.create_all(engine)
    validation_inputs = []
    simulation_inputs = []
    catalogue_searches = []
    real_execute_tool = typed_dispatch.execute_tool

    def execute(identifier, payload, context=None):
        if identifier in {"get_variable", "search_variables"}:
            result = real_execute_tool(identifier, payload, context=context)
            if identifier == "search_variables":
                catalogue_searches.append(result)
            return result
        if identifier == "validate_household":
            validation_inputs.append(payload)
            return {"valid": True, "year": 2026}
        if identifier == "run_household_simulation":
            simulation_inputs.append(payload)
            employment_income = sum(
                person.get("employment_income", 0) for person in payload["people"]
            )
            return {
                "status": "success",
                "year": 2026,
                "reform_applied": False,
                "income_tax": employment_income * 0.2,
                "national_insurance": employment_income * 0.08,
                "household_tax": employment_income * 0.28,
            }
        raise AssertionError(f"Unexpected retained tool call: {identifier}")

    monkeypatch.setattr(typed_dispatch, "execute_tool", execute)

    registry = build_default_fact_registry()
    interpretation_scenario = TenTurnInterpreter()
    interpretation_provider = None
    if interpretation_mode == "scripted":
        interpretation_provider = TenTurnAnthropicMessages(interpretation_scenario)
        monkeypatch.setattr(
            context_tools,
            "get_async_client",
            lambda: SimpleNamespace(messages=interpretation_provider),
        )
    interpreter = AnthropicContextInterpreter()
    partial_inputs = PartialInputRegistry()
    partial_inputs.register(
        "household_analysis",
        schema_version="1",
        model=HouseholdAnalysisDraft,
    )
    partial_inputs.register(
        "policy_reform",
        schema_version="1",
        model=PolicyReformInput,
    )
    capability_repository = SQLConversationCapabilityRepository(
        engine=engine,
        partial_inputs=partial_inputs,
    )
    trace_repository = SQLInvocationTraceRepository(engine=engine)
    artifacts = RepositoryArtifactAccess(capability_repository)
    context_repository = SQLConversationContextRepository(engine=engine)
    mapper = (
        EmploymentIncomeMapper(target_period=None)
        if interpretation_mode == "scripted"
        else AnthropicVariableMapper()
    )
    composition = compose_runtime(
        tools=(
            *build_dispatch_tools(),
            AssessRelevanceTool(RelevantAssessor()),
            ProposeContextChangeTool(interpreter),
            ValidateContextChangeTool(
                ContextChangeValidator(ContextReducer(registry), registry),
                (
                    AnthropicContextProposalReviewer()
                    if interpretation_mode == "live"
                    else None
                ),
            ),
            ApplyContextChangeTool(ContextChangeApplier(context_repository)),
            ReduceContextPatchTool(ContextReducer(registry)),
            ResolveContextChangeTool(
                ContextChangeResolver(
                    registry,
                    mapper,
                )
            ),
            ResolveReformTool(BaselineOnlyReformResolver()),
            AssembleHouseholdCandidateTool(),
            ExtractResultFindingsTool(),
            VerifyNumericalResponseTool(),
        ),
        capabilities=(
            ConversationRelevanceCapability(),
            PolicyReformCapability(),
            HouseholdAnalysisCapability(HouseholdEngineFactProjector(registry)),
        ),
        tracer=InvocationTracer(sink=trace_repository),
    )
    model = TenTurnConversationModel()
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=model,
        context_repository=context_repository,
        fact_registry=registry,
    )
    user_turns = (
        "How much tax would I pay on £50,000 of income?",
        str(user_age),
        collective_message,
        allocation_answer,
        "29",
        "Actually, my spouse is 30.",
        "What if our collective employment income were £80,000?",
        "Use £50,000 for me and £30,000 for my spouse.",
        "Now use £45,000 for me, keeping my spouse at £30,000.",
        "Recalculate the same tax outputs.",
    )
    async def run_path():
        transcript = []
        contexts = []
        completed_turns = []
        for index, user_message in enumerate(user_turns, start=1):
            transcript.append({"role": "user", "content": user_message})
            capability_context = composition.executor.context(
                request_id=f"request-{index}",
                conversation_id="conversation-ten-turn",
                turn_id=f"turn-{index}",
                is_cancelled=not_cancelled,
                artifacts=artifacts,
            )
            events = await collect(
                service,
                ChatTurnInput(
                    messages=list(transcript),
                    session_id="conversation-ten-turn",
                    turn_id=f"turn-{index}",
                    debug=True,
                ),
                capability_context,
            )
            assert isinstance(events[-1], TurnCompleted)
            completed_turns.append(events[-1])
            transcript.append({"role": "assistant", "content": events[-1].content})
            contexts.append(context_repository.load("conversation-ten-turn"))
        return contexts, completed_turns

    contexts, completed_turns = asyncio.run(run_path())

    assert len(user_turns) == 10
    if interpretation_mode == "scripted":
        assert len(interpretation_scenario.requests) == 11, [
            (
                request.current_message,
                [issue.code for issue in request.repair_issues],
            )
            for request in interpretation_scenario.requests
        ]
        assert mapper.calls == 2
    if interpretation_provider is not None:
        collective_calls = [
            item
            for item in interpretation_provider.calls
            if item["current_message"] == collective_message
        ]
        assert len(collective_calls) == 2
        repair_content = collective_calls[1]["provider_content"]
        assert '"code": "missing_monetary_fact_claim"' in repair_content
        assert '"evidence":' in repair_content
    assert model.call_count == 10
    assert "age" in completed_turns[0].content.casefold()
    assert all(
        "what period should i use" not in turn.content.casefold()
        for turn in completed_turns
    )
    assert all(
        housing_text not in completed_turns[index].content.casefold()
        for index in (0, 3)
        for housing_text in ("rent", "council tax")
    )
    assert all(
        internal_text not in turn.content.casefold()
        for turn in completed_turns
        for internal_text in (
            "person 1",
            "person 2",
            "<function_calls>",
            "employment_income",
            "policyengine",
        )
    )
    employment_income_results = [
        variable
        for result in catalogue_searches
        for variable in result.get("variables", [])
        if variable.get("name") == "employment_income"
    ]
    assert employment_income_results
    assert all(
        variable.get("definition_period") == "year"
        for variable in employment_income_results
    )
    self_age = contexts[1].active_fact(
        "person.age",
        "person:self",
        "scope:primary-household",
    )
    assert self_age is not None
    assert self_age.assertion == PresentAssertion(
        value=IntegerFactValue(value=user_age)
    )
    assert contexts[0].active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("50000"),
            period=MoneyPeriod.ANNUAL,
        )
    )

    proposed = contexts[2].pending_fact_resolutions
    assert len(proposed) == 1
    assert proposed[0].status is FactResolutionStatus.AWAITING_CONFIRMATION
    assignment = proposed[0].assignments[0]
    assert isinstance(assignment.assertion.value, MoneyFactValue)
    assert assignment.assertion.value.amount == Decimal("20000")
    assert "£20,000" in completed_turns[2].content
    assert "correct breakdown" in completed_turns[2].content.casefold()
    assert "PolicyEngine" not in completed_turns[2].content
    assert "employment_income" not in completed_turns[2].content
    spouse_id = assignment.subject_entity_id
    assert len(simulation_inputs) == 6

    allocation_context = contexts[3]
    assert allocation_context.pending_fact_resolutions == ()
    assert len(allocation_context.pending_questions) == 1
    assert "age" in allocation_context.pending_questions[0].prompt.casefold()
    assert allocation_context.active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("50000"),
            period=MoneyPeriod.ANNUAL,
        )
    )
    assert allocation_context.active_fact(
        "person.employment_income",
        spouse_id,
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("20000"),
            period=MoneyPeriod.ANNUAL,
        )
    )

    married_completed = contexts[4]
    assert married_completed.pending_questions == ()
    assert capability_repository.list_waiting(
        "conversation-ten-turn",
        capability_id="household_analysis",
    ) == ()
    spouse_age = married_completed.active_fact(
        "person.age",
        spouse_id,
        "scope:primary-household",
    )
    assert spouse_age is not None
    assert spouse_age.assertion == PresentAssertion(value=IntegerFactValue(value=29))

    corrected_age = contexts[5].active_fact(
        "person.age",
        spouse_id,
        "scope:primary-household",
    )
    assert corrected_age is not None
    assert corrected_age.assertion == PresentAssertion(value=IntegerFactValue(value=30))
    assert corrected_age.supersedes_fact_id == spouse_age.fact_id

    second_collective = contexts[6].pending_fact_resolutions
    assert len(second_collective) == 1
    assert second_collective[0].status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert "divided" in second_collective[0].prompt
    assert contexts[7].pending_fact_resolutions == ()

    final_context = contexts[-1]
    assert tuple(
        entity.entity_id
        for entity in final_context.entities
        if entity.kind is EntityKind.PERSON
    ) == ("person:self", spouse_id)
    assert final_context.pending_questions == ()
    assert final_context.pending_fact_resolutions == ()
    assert final_context.active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("45000"),
            period=MoneyPeriod.ANNUAL,
        )
    )
    assert final_context.active_fact(
        "person.employment_income",
        spouse_id,
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("30000"),
            period=MoneyPeriod.ANNUAL,
        )
    )
    assert [
        tuple(person["employment_income"] for person in item["people"])
        for item in simulation_inputs
    ] == [
        (50000.0,),
        (50000.0, 20000.0),
        (50000.0, 20000.0),
        (50000.0, 30000.0),
        (45000.0, 30000.0),
        (45000.0, 30000.0),
    ]
    assert validation_inputs == simulation_inputs

    trace_records = trace_repository.list_for_conversation(
        "conversation-ten-turn",
        include_private=True,
    )

    def context_change_records(turn_id):
        return [
            record
            for record in trace_records
            if record.turn_id == turn_id
            and record.status.value == "completed"
            and record.identifier
            in {
                "propose_context_change",
                "validate_context_change",
                "resolve_context_change",
                "apply_context_change",
            }
        ]

    first_collective_records = context_change_records("turn-3")
    assert [record.identifier for record in first_collective_records][-5:] == [
        "propose_context_change",
        "validate_context_change",
        "resolve_context_change",
        "validate_context_change",
        "apply_context_change",
    ]
    if interpretation_mode == "scripted":
        assert [record.identifier for record in first_collective_records] == [
            "propose_context_change",
            "validate_context_change",
            "propose_context_change",
            "validate_context_change",
            "resolve_context_change",
            "validate_context_change",
            "apply_context_change",
        ]
    first_resolution = first_collective_records[-3]
    assert first_resolution.debug_input is not None
    resolved_claim = first_resolution.debug_input["claims"][0]
    proposal_claims = first_resolution.debug_input["proposal"]["changes"]
    assert resolved_claim in proposal_claims
    assert resolved_claim["relationship"] == "sum"
    assert first_resolution.debug_input["validation_issues"][0]["code"] == (
        "authoritative_resolution_required"
    )
    assert first_collective_records[-2].debug_input["claims_resolved"] is True
    assert first_collective_records[-1].debug_input["outcome"]["status"] == (
        "ready_to_commit"
    )

    second_collective_records = context_change_records("turn-7")
    assert [record.identifier for record in second_collective_records][-5:] == [
        "propose_context_change",
        "validate_context_change",
        "resolve_context_change",
        "validate_context_change",
        "apply_context_change",
    ]


def _run_pending_supplement_ten_turn_path(
    tmp_path,
    monkeypatch,
    *,
    replace_total: bool,
):
    from tools import typed_dispatch

    name = "amount-and-period" if replace_total else "period-only"
    engine = create_engine(f"sqlite:///{tmp_path / f'{name}.sqlite'}")
    SQLModel.metadata.create_all(engine)
    validation_inputs = []
    simulation_inputs = []

    def execute(identifier, payload, context=None):
        del context
        if identifier == "get_variable":
            return {
                "status": "success",
                "variable": {
                    "name": payload["name"],
                    "label": payload["name"].replace("_", " ").title(),
                    "entity": "person",
                    "value_type": "float",
                },
            }
        if identifier == "search_variables":
            return {
                "status": "success",
                "variables": [
                    {
                        "name": "employment_income",
                        "label": "Employment income",
                        "entity": "person",
                        "description": "Income from employment.",
                        "definition_period": None,
                        "value_type": "float",
                    }
                ],
            }
        if identifier == "validate_household":
            validation_inputs.append(payload)
            return {"valid": True, "year": 2026}
        if identifier == "run_household_simulation":
            simulation_inputs.append(payload)
            employment_income = sum(
                person.get("employment_income", 0) for person in payload["people"]
            )
            return {
                "status": "success",
                "year": 2026,
                "reform_applied": False,
                "income_tax": employment_income * 0.2,
                "national_insurance": employment_income * 0.08,
                "household_tax": employment_income * 0.28,
            }
        raise AssertionError(f"Unexpected retained tool call: {identifier}")

    monkeypatch.setattr(typed_dispatch, "execute_tool", execute)
    registry = build_default_fact_registry()
    interpreter = PendingSupplementTenTurnInterpreter(replace_total=replace_total)
    partial_inputs = PartialInputRegistry()
    partial_inputs.register(
        "household_analysis",
        schema_version="1",
        model=HouseholdAnalysisDraft,
    )
    partial_inputs.register(
        "policy_reform",
        schema_version="1",
        model=PolicyReformInput,
    )
    capability_repository = SQLConversationCapabilityRepository(
        engine=engine,
        partial_inputs=partial_inputs,
    )
    artifacts = RepositoryArtifactAccess(capability_repository)
    context_repository = SQLConversationContextRepository(engine=engine)
    trace_repository = SQLInvocationTraceRepository(engine=engine)
    composition = compose_runtime(
        tools=(
            *build_dispatch_tools(),
            AssessRelevanceTool(RelevantAssessor()),
            ProposeContextChangeTool(interpreter),
            ValidateContextChangeTool(
                ContextChangeValidator(ContextReducer(registry), registry)
            ),
            ApplyContextChangeTool(ContextChangeApplier(context_repository)),
            ReduceContextPatchTool(ContextReducer(registry)),
            ResolveContextChangeTool(
                ContextChangeResolver(registry, EmploymentIncomeMapper())
            ),
            ResolveReformTool(BaselineOnlyReformResolver()),
            AssembleHouseholdCandidateTool(),
            ExtractResultFindingsTool(),
            VerifyNumericalResponseTool(),
        ),
        capabilities=(
            ConversationRelevanceCapability(),
            PolicyReformCapability(),
            HouseholdAnalysisCapability(HouseholdEngineFactProjector(registry)),
        ),
        tracer=InvocationTracer(sink=trace_repository),
    )
    model = TenTurnConversationModel()
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=model,
        context_repository=context_repository,
        fact_registry=registry,
    )
    clarification_answer = (
        "Actually, make that £75,000 annually."
        if replace_total
        else "Yes, annual"
    )
    spouse_amount = "£25,000" if replace_total else "£20,000"
    user_turns = (
        "How much tax would I pay on £50,000 of income?",
        "27",
        "What if I were married and my spouse and I collectively made £70,000?",
        clarification_answer,
        "Yes, that breakdown is correct.",
        "My spouse is 30.",
        "What is our combined tax now?",
        "Actually, I earn £55,000 annually.",
        f"Keep my spouse at {spouse_amount} annually.",
        "Recalculate the same tax outputs.",
    )
    conversation_id = f"conversation-{name}"

    async def run_path():
        transcript = []
        contexts = []
        completed_turns = []
        for index, user_message in enumerate(user_turns, start=1):
            transcript.append({"role": "user", "content": user_message})
            capability_context = composition.executor.context(
                request_id=f"{name}-request-{index}",
                conversation_id=conversation_id,
                turn_id=f"turn-{index}",
                is_cancelled=not_cancelled,
                artifacts=artifacts,
            )
            events = await collect(
                service,
                ChatTurnInput(
                    messages=list(transcript),
                    session_id=conversation_id,
                    turn_id=f"turn-{index}",
                    debug=True,
                ),
                capability_context,
            )
            assert isinstance(events[-1], TurnCompleted)
            completed_turns.append(events[-1])
            transcript.append({"role": "assistant", "content": events[-1].content})
            contexts.append(context_repository.load(conversation_id))
        return contexts, completed_turns

    contexts, completed_turns = asyncio.run(run_path())
    return SimpleNamespace(
        contexts=contexts,
        completed_turns=completed_turns,
        interpreter=interpreter,
        model=model,
        simulation_inputs=simulation_inputs,
        validation_inputs=validation_inputs,
        trace_records=trace_repository.list_for_conversation(
            conversation_id,
            include_private=True,
        ),
        user_turns=user_turns,
    )


def _assert_pending_supplement_path(result, *, expected_spouse_income: Decimal):
    assert len(result.user_turns) == 10
    repair_requests = [
        (
            request.current_message,
            [issue.code for issue in request.repair_issues],
        )
        for request in result.interpreter.requests
        if request.repair_issues
    ]
    assert len(result.interpreter.requests) == 10, repair_requests
    assert result.model.call_count == 10
    assert len(result.completed_turns) == 10
    original_pending = result.contexts[2].pending_fact_resolutions
    assert len(original_pending) == 1
    assert original_pending[0].status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert original_pending[0].source_claim is not None
    assert isinstance(original_pending[0].source_claim.value, ClaimedMoneyValue)
    assert original_pending[0].source_claim.value.amount == Decimal("70000")
    assert original_pending[0].source_claim.value.period is None
    assert original_pending[0].supplements == ()
    assert "period" in result.completed_turns[2].content.casefold()

    resolved = result.contexts[3].pending_fact_resolutions
    assert len(resolved) == 1
    assert resolved[0].status is FactResolutionStatus.AWAITING_CONFIRMATION
    assert resolved[0].source_turn_id == "turn-3"
    assert resolved[0].source_claim == original_pending[0].source_claim
    assert len(resolved[0].supplements) == 1
    assert resolved[0].supplements[0].turn_id == "turn-4"
    assert resolved[0].period is MoneyPeriod.ANNUAL
    assert len(resolved[0].assignments) == 1
    assert resolved[0].assignments[0].assertion.value.amount == expected_spouse_income
    assert "correct breakdown" in result.completed_turns[3].content.casefold()

    spouse_id = resolved[0].assignments[0].subject_entity_id
    assert result.contexts[4].pending_fact_resolutions == ()
    assert result.contexts[4].active_fact(
        "person.employment_income",
        spouse_id,
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=expected_spouse_income,
            period=MoneyPeriod.ANNUAL,
        )
    )
    final = result.contexts[-1]
    assert final.pending_fact_resolutions == ()
    assert final.pending_questions == ()
    assert final.active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("55000"),
            period=MoneyPeriod.ANNUAL,
        )
    )
    assert final.active_fact(
        "person.employment_income",
        spouse_id,
        "scope:primary-household",
    ).assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=expected_spouse_income,
            period=MoneyPeriod.ANNUAL,
        )
    )
    assert len(result.simulation_inputs) == 6
    assert result.validation_inputs == result.simulation_inputs


def test_ten_user_turns_merge_a_period_only_answer_with_retained_claim_provenance(
    tmp_path,
    monkeypatch,
):
    result = _run_pending_supplement_ten_turn_path(
        tmp_path,
        monkeypatch,
        replace_total=False,
    )

    _assert_pending_supplement_path(
        result,
        expected_spouse_income=Decimal("20000"),
    )
    turn_four_proposal = next(
        record
        for record in result.trace_records
        if record.turn_id == "turn-4"
        and record.identifier == "propose_context_change"
        and record.status.value == "completed"
    )
    assert turn_four_proposal.debug_output is not None
    assert turn_four_proposal.debug_output["changes"][0]["action"] == "supply"
    assert turn_four_proposal.debug_output["changes"][0]["updates"] == [
        {
            "path": ["value", "period"],
            "value": "annual",
            "evidence": "annual",
        }
    ]
    assert "70000" not in json.dumps(turn_four_proposal.debug_output)


def test_ten_user_turns_validate_a_replacement_total_and_period_before_merging(
    tmp_path,
    monkeypatch,
):
    result = _run_pending_supplement_ten_turn_path(
        tmp_path,
        monkeypatch,
        replace_total=True,
    )

    _assert_pending_supplement_path(
        result,
        expected_spouse_income=Decimal("25000"),
    )
    resolved = result.contexts[3].pending_fact_resolutions[0]
    assert resolved.expected_total == Decimal("75000")
    assert [update.path for update in resolved.supplements[0].updates] == [
        ("value", "amount"),
        ("value", "period"),
    ]
    turn_four_validation = next(
        record
        for record in result.trace_records
        if record.turn_id == "turn-4"
        and record.identifier == "validate_context_change"
        and record.status.value == "completed"
        and record.debug_output is not None
        and record.debug_output["status"] == "resolution_required"
    )
    assert turn_four_validation.debug_output["issues"] == [
        {
            "code": "authoritative_resolution_required",
            "message": (
                "The model-proposed claim requires an authoritative semantic mapping "
                "before it can be validated as a context change."
            ),
            "path": ["proposal", "changes", "0"],
            "claim_index": 0,
            "operation_index": None,
            "evidence": "collectively made £70,000",
        }
    ]
