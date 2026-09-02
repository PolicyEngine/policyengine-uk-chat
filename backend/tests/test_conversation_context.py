from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlmodel import SQLModel, create_engine

from capabilities.composition import compose_runtime
from capabilities.relevance import (
    AssessRelevanceTool,
    ConversationRelevanceCapability,
    RelevanceAssessment,
    RelevanceResult,
)
from capabilities.tracing import InvocationTracer
from chat.capability_service import ChatTurnService
from chat.events import InvocationActivity, TurnCompleted
from chat.model_port import ConversationModelResponse, ModelCapabilityCall
from chat.turn_input import ChatTurnInput
from conversation_context.household_view import HouseholdContextView
from conversation_context.models import (
    CapabilityInvocationReference,
    ClaimedMoneyValue,
    ContextEntityCandidate,
    ContextPatch,
    EntityKind,
    EnsureEntityOperation,
    ExplicitAbsenceAssertion,
    FactDecisionStatus,
    FactClaim,
    FactClaimFieldUpdate,
    FactClaimRelationship,
    FactRequirement,
    IntegerFactValue,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolution,
    PendingFactResolutionResponse,
    PendingResolutionAction,
    FactResolutionStatus,
    PendingQuestion,
    PendingQuestionStatus,
    PresentAssertion,
    ReplacePendingQuestionsOperation,
    SetFactOperation,
    TextSetFactValue,
)
from conversation_context.reducer import ContextReducer
from conversation_context.change_pipeline import (
    ContextChangeApplier,
    ValidateContextChangeInput,
    ContextValidationOutcome,
    ContextValidationStatus,
    ContextChangeValidator,
    ContextChangeProposal,
    SemanticClaimReview,
)
from conversation_context.registry import (
    FactDefinition,
    FactDefinitionRegistry,
    FactUpdatePolicy,
    FactValueKind,
    build_default_fact_registry,
)
from conversation_context.tools import (
    ApplyContextChangeTool,
    ContextSemanticReviewOutput,
    ValidateContextChangeTool,
    ProposeContextChangeOutput,
    ProposeContextChangeTool,
    ReduceContextPatchTool,
)
from conversation_context.variable_resolution import (
    MappingConfidence,
    MappingStatus,
    ContextChangeResolver,
    ResolveContextChangeTool,
    VariableMappingResult,
    VariableMappingSelection,
)
from conversation_context.models import ConversationContext
from persistence.context_repository import SQLConversationContextRepository
from persistence.rows import ConversationContextRow
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from tools.typed_models import SafeToolOutput, SearchVariablesInput


class FakeRelevanceAssessor:
    async def assess(self, request):
        return RelevanceAssessment(
            result=RelevanceResult.RELEVANT,
            explanation="supported",
        )


class FakeContextInterpreter:
    async def propose(self, request):
        claims = ()
        if "42" in request.current_message:
            claims = (
                FactClaim(
                    concept="age",
                    definition_key="person.age",
                    subject_references=("person:self",),
                    scope_id="scope:primary-household",
                    value=IntegerFactValue(value=42),
                    evidence="I am 42",
                ),
            )
        return ProposeContextChangeOutput(
            expected_revision=request.context.revision,
            changes=claims,
        )


class AggregateIncomeInterpreter:
    async def propose(self, request):
        return ProposeContextChangeOutput(
            expected_revision=request.context.revision,
            candidate_entities=(
                ContextEntityCandidate(
                    reference="new:spouse",
                    kind=EntityKind.PERSON,
                    aliases=("my spouse",),
                    relationship_to_user="spouse",
                ),
            ),
            changes=(
                    FactClaim(
                        concept="employment income",
                        definition_key="person.employment_income",
                        subject_references=("person:self",),
                        scope_id="scope:primary-household",
                        value=ClaimedMoneyValue(
                            amount=Decimal("50000"),
                            period=MoneyPeriod.ANNUAL,
                        ),
                        evidence="I earn £50,000.",
                    ),
                FactClaim(
                    claim_id="combined-income",
                    concept="employment income",
                    value=ClaimedMoneyValue(
                        amount=Decimal("70000"),
                        period=MoneyPeriod.ANNUAL,
                    ),
                    subject_references=("person:self", "new:spouse"),
                    relationship=FactClaimRelationship.SUM,
                    evidence="Together we earn £70,000.",
                ),
            ),
        )


class EmploymentIncomeMapper:
    async def select(self, **_kwargs):
        return VariableMappingResult(
            selection=VariableMappingSelection(
                status=MappingStatus.MATCHED,
                variable_name="employment_income",
                confidence=MappingConfidence.HIGH,
                target_period=MoneyPeriod.ANNUAL,
            )
        )


class EmploymentIncomeSearchTool(Tool[SearchVariablesInput, SafeToolOutput]):
    spec = ToolSpec(
        identifier="search_variables",
        version="1",
        description="Return the employment-income catalogue entry.",
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.TOOL}),
        input_model=SearchVariablesInput,
        output_model=SafeToolOutput,
    )

    async def run(
        self,
        tool_input: SearchVariablesInput,
        context: ToolCallContext,
    ) -> SafeToolOutput:
        del tool_input, context
        return SafeToolOutput(
            {
                "variables": [
                    {
                        "name": "employment_income",
                        "label": "Employment income",
                        "entity": "person",
                        "description": "Income from employment.",
                        "definition_period": "year",
                        "value_type": "float",
                    }
                ]
            }
        )


class DirectModel:
    async def respond(self, request):
        assert '"person.age"' in request.system
        return ConversationModelResponse(text="Thanks.", stop_reason="end_turn")

    async def redraft_numerical(self, **kwargs):
        raise AssertionError(kwargs)

    async def review_assessment_language(self, **kwargs):
        raise AssertionError(kwargs)


class PlainDirectModel:
    async def respond(self, request):
        assert '"pending_fact_resolutions"' in request.system
        assert "not accepted calculation input" in request.system
        return ConversationModelResponse(text="Thanks.", stop_reason="end_turn")

    async def redraft_numerical(self, **kwargs):
        raise AssertionError(kwargs)

    async def review_assessment_language(self, **kwargs):
        raise AssertionError(kwargs)


class NaturalContextClarificationModel:
    async def respond(self, request):
        assert "unregistered_fact_claim" in request.system
        assert "ask one concise, natural clarification question" in request.system
        assert request.capabilities == ()
        return ConversationModelResponse(
            text="Could you clarify which household detail you meant?",
            capability_calls=(
                ModelCapabilityCall(
                    call_id="not-offered",
                    capability_id="household_analysis",
                    input={},
                ),
            ),
            stop_reason="end_turn",
        )

    async def redraft_numerical(self, **kwargs):
        raise AssertionError(kwargs)

    async def review_assessment_language(self, **kwargs):
        raise AssertionError(kwargs)


async def _not_cancelled():
    return False


def _age_patch(revision: int, age: int, *, correction: bool = False):
    return ContextPatch(
        expected_revision=revision,
        operations=(
            SetFactOperation(
                definition_key="person.age",
                subject_reference="person:self",
                scope_id="scope:primary-household",
                assertion=PresentAssertion(value=IntegerFactValue(value=age)),
                correction=correction,
            ),
        ),
    )


def test_context_validator_rejects_a_whole_proposal_without_partial_entity_creation():
    registry = build_default_fact_registry()
    validator = ContextChangeValidator(ContextReducer(registry), registry)
    prior = ConversationContext.initial("conversation")
    proposal = ContextChangeProposal(
        expected_revision=0,
        candidate_entities=(
            ContextEntityCandidate(
                reference="new:spouse",
                kind=EntityKind.PERSON,
                aliases=("my spouse",),
                relationship_to_user="spouse",
            ),
        ),
        changes=(
            FactClaim(
                concept="age",
                definition_key="person.not_registered",
                subject_references=("new:spouse",),
                scope_id="scope:primary-household",
                value=IntegerFactValue(value=30),
                evidence="I have a 30-year-old spouse.",
            ),
        ),
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=proposal,
            claims_resolved=True,
            turn_id="turn-1",
            evidence="I have a 30-year-old spouse.",
        )
    )

    assert outcome.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert outcome.context == prior
    assert outcome.context.revision == 0
    assert all(
        entity.relationship_to_user != "spouse" for entity in outcome.context.entities
    )
    assert outcome.issues[0].claim_index == 0
    assert outcome.issues[0].path == (
        "proposal",
        "changes",
        "0",
    )


def test_context_validator_processes_direct_and_relational_claims_from_one_proposal():
    registry = build_default_fact_registry()
    validator = ContextChangeValidator(ContextReducer(registry), registry)
    prior = ConversationContext.initial("conversation")
    proposal = ContextChangeProposal(
        expected_revision=0,
        candidate_entities=(
            ContextEntityCandidate(
                reference="new:spouse",
                kind=EntityKind.PERSON,
                aliases=("my spouse",),
                relationship_to_user="spouse",
            ),
        ),
        changes=(
            FactClaim(
                concept="age",
                definition_key="person.age",
                subject_references=("person:self",),
                scope_id="scope:primary-household",
                value=IntegerFactValue(value=26),
                evidence="I am 26.",
            ),
            FactClaim(
                concept="employment income",
                subject_references=("person:self", "new:spouse"),
                relationship=FactClaimRelationship.SUM,
                value=ClaimedMoneyValue(
                    amount=Decimal("70000"),
                    period=MoneyPeriod.ANNUAL,
                ),
                evidence="Together we earn £70,000 a year.",
            ),
        ),
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=proposal,
            turn_id="turn-1",
            evidence="I am 26 and together we earn £70,000 a year.",
        )
    )

    assert outcome.status is ContextValidationStatus.RESOLUTION_REQUIRED
    assert outcome.context.revision == 1
    assert len(outcome.claims_to_resolve) == 1
    assert outcome.claims_to_resolve[0] == proposal.claims[1]
    assert any(isinstance(operation, SetFactOperation) for operation in outcome.generated_operations)
    assert outcome.context.active_fact(
        "person.age",
        "person:self",
        "scope:primary-household",
    ) is not None
    assert any(
        entity.relationship_to_user == "spouse" for entity in outcome.context.entities
    )


def test_validation_tool_maps_semantic_verdicts_by_exact_claim_id():
    registry = build_default_fact_registry()

    class RejectingReviewer:
        async def review(self, request):
            claim = request.proposal.claims[0]
            return ContextSemanticReviewOutput(
                reviews=(
                    SemanticClaimReview(
                        claim_id=claim.claim_id,
                        supported=False,
                        reason="The value is assigned to the wrong subject.",
                        evidence=claim.evidence,
                    ),
                ),
            )

    composition = compose_runtime(
        tools=(
            ValidateContextChangeTool(
                ContextChangeValidator(ContextReducer(registry), registry),
                RejectingReviewer(),
            ),
        ),
        capabilities=(),
    )
    prior = ConversationContext.initial("conversation")
    proposal = ContextChangeProposal(
        expected_revision=prior.revision,
        changes=(
            FactClaim(
                claim_id="opaque-claim-id",
                concept="age",
                definition_key="person.age",
                subject_references=("person:self",),
                scope_id="scope:primary-household",
                value=IntegerFactValue(value=27),
                evidence="27",
            ),
        ),
    )
    call_context = composition.executor.context(
        request_id="request",
        conversation_id="conversation",
        turn_id="turn-1",
        is_cancelled=_not_cancelled,
    )

    outcome = asyncio.run(
        composition.executor.invoke_tool(
            "validate_context_change",
            ValidateContextChangeInput(
                context=prior,
                proposal=proposal,
                turn_id="turn-1",
                evidence="27",
            ),
            caller=CallerType.RUNTIME,
            context=call_context,
        )
    )

    assert isinstance(outcome, ContextValidationOutcome)
    assert outcome.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert outcome.issues[0].code == "semantic_claim_mismatch"
    assert outcome.issues[0].claim_index == 0
    assert outcome.semantic_reviews[0].claim_id == "opaque-claim-id"


def test_context_validator_accepts_a_grounded_short_integer_answer_with_paraphrased_evidence():
    registry = build_default_fact_registry()
    validator = ContextChangeValidator(ContextReducer(registry), registry)
    prior = ConversationContext.initial("conversation")
    proposal = ContextChangeProposal(
        expected_revision=0,
        changes=(
            FactClaim(
                concept="person.age",
                definition_key="person.age",
                subject_references=("person:self",),
                scope_id="scope:primary-household",
                value=IntegerFactValue(value=27),
                evidence="User stated age is 27 in response to the question.",
            ),
        ),
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=proposal,
            turn_id="turn-1",
            evidence="27",
        )
    )

    assert outcome.status is ContextValidationStatus.READY_TO_COMMIT
    assert outcome.context.active_fact(
        "person.age",
        "person:self",
        "scope:primary-household",
    ) is not None


def test_context_validator_rejects_a_response_to_a_capability_identifier():
    registry = build_default_fact_registry()
    validator = ContextChangeValidator(ContextReducer(registry), registry)
    prior = ConversationContext.initial("conversation")

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=ContextChangeProposal(
                expected_revision=0,
                changes=(
                    PendingFactResolutionResponse(
                        proposal_id="household_analysis",
                        action=PendingResolutionAction.ACCEPT,
                        evidence="27",
                    ),
                ),
            ),
            turn_id="turn-1",
            evidence="27",
        )
    )

    assert outcome.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert outcome.issues[0].code == "unknown_pending_resolution_response"
    assert outcome.context == prior


def test_context_validator_requires_direct_claims_for_a_clarification_record():
    registry = build_default_fact_registry()
    validator = ContextChangeValidator(ContextReducer(registry), registry)
    initial = ConversationContext.initial("conversation")
    prior = initial.model_copy(
        update={
            "pending_fact_resolutions": (
                PendingFactResolution(
                    proposal_id="clarification-1",
                    claim_id="claim-1",
                    source_turn_id="turn-0",
                    scope_id="scope:primary-household",
                    referenced_entity_ids=("person:self",),
                    evidence="Clarify the value.",
                    status=FactResolutionStatus.NEEDS_CLARIFICATION,
                    prompt="What value should I use?",
                    relationship=FactClaimRelationship.DIRECT,
                    created_revision=0,
                ),
            ),
        }
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=ContextChangeProposal(
                expected_revision=0,
                changes=(
                    PendingFactResolutionResponse(
                        proposal_id="clarification-1",
                        action=PendingResolutionAction.ACCEPT,
                        evidence="Yes",
                    ),
                ),
            ),
            turn_id="turn-1",
            evidence="Yes.",
        )
    )

    assert outcome.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert outcome.issues[0].code == "pending_resolution_not_confirmable"
    assert outcome.context == prior


def test_context_validator_merges_a_grounded_period_with_the_retained_source_claim():
    registry = build_default_fact_registry()
    reducer = ContextReducer(registry)
    with_spouse = reducer.reduce(
        ConversationContext.initial("conversation"),
        ContextPatch(
            expected_revision=0,
            operations=(
                EnsureEntityOperation(
                    reference="new:spouse",
                    kind=EntityKind.PERSON,
                    aliases=("my spouse",),
                    relationship_to_user="spouse",
                ),
            ),
        ),
        turn_id="turn-0",
        evidence="I have a spouse.",
    ).context
    spouse_id = next(
        entity.entity_id
        for entity in with_spouse.entities
        if entity.relationship_to_user == "spouse"
    )
    source_claim = FactClaim(
        claim_id="collective-income",
        concept="employment income",
        definition_key="person.employment_income",
        subject_references=("person:self", spouse_id),
        relationship=FactClaimRelationship.SUM,
        value=ClaimedMoneyValue(amount=Decimal("70000")),
        evidence="we collectively have £70,000 of employment income",
    )
    prior = with_spouse.model_copy(
        update={
            "pending_fact_resolutions": (
                PendingFactResolution(
                    proposal_id="period-question",
                    claim_id=source_claim.claim_id,
                    source_turn_id="turn-1",
                    source_claim=source_claim,
                    scope_id="scope:primary-household",
                    referenced_entity_ids=("person:self", spouse_id),
                    evidence=source_claim.evidence,
                    status=FactResolutionStatus.NEEDS_CLARIFICATION,
                    prompt="What period should I use?",
                    variable_name="employment_income",
                    variable_entity="person",
                    relationship=FactClaimRelationship.SUM,
                    expected_total=Decimal("70000"),
                    created_revision=with_spouse.revision,
                ),
            ),
        }
    )
    response = PendingFactResolutionResponse(
        proposal_id="period-question",
        action=PendingResolutionAction.SUPPLY,
        updates=(
            FactClaimFieldUpdate(
                path=("value", "period"),
                value="annual",
                evidence="annual",
            ),
        ),
        evidence="Yes, annual",
    )

    outcome = ContextChangeValidator(reducer, registry).validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=ContextChangeProposal(
                expected_revision=prior.revision,
                changes=(response,),
            ),
            turn_id="turn-2",
            evidence="Yes, annual",
        )
    )

    assert outcome.status is ContextValidationStatus.RESOLUTION_REQUIRED
    assert [issue.code for issue in outcome.issues] == [
        "authoritative_resolution_required"
    ]
    assert len(outcome.claims_to_resolve) == 1
    merged = outcome.claims_to_resolve[0]
    assert merged.claim_id == source_claim.claim_id
    assert isinstance(merged.value, ClaimedMoneyValue)
    assert merged.value.amount == Decimal("70000")
    assert merged.value.period is MoneyPeriod.ANNUAL
    assert merged.evidence == source_claim.evidence

    rejected = ContextChangeValidator(reducer, registry).validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=ContextChangeProposal(
                expected_revision=prior.revision,
                    changes=(
                        response.model_copy(
                        update={
                            "updates": (
                                FactClaimFieldUpdate(
                                    path=("value", "period"),
                                    value="monthly",
                                    evidence="annual",
                                ),
                            )
                        }
                    ),
                ),
            ),
            turn_id="turn-2",
            evidence="Yes, annual",
        )
    )

    assert rejected.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert [issue.code for issue in rejected.issues] == [
        "ungrounded_fact_claim_field_update"
    ]
    assert rejected.claims_to_resolve == ()
    assert rejected.context == prior

    resolver_owned_path = ContextChangeValidator(reducer, registry).validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=ContextChangeProposal(
                expected_revision=prior.revision,
                changes=(
                    response.model_copy(
                        update={
                            "updates": (
                                FactClaimFieldUpdate(
                                    path=("terms", "0", "known_value"),
                                    value=50000,
                                    evidence="£50,000",
                                ),
                            ),
                                "evidence": "Use £50,000 for me.",
                            }
                        ),
                        FactClaim(
                            concept="employment income",
                            definition_key="person.employment_income",
                            subject_references=("person:self",),
                            scope_id="scope:primary-household",
                            value=ClaimedMoneyValue(
                                amount=Decimal("50000"),
                                period=MoneyPeriod.ANNUAL,
                            ),
                            evidence="£50,000 for me",
                        ),
                    ),
            ),
            turn_id="turn-2",
            evidence="Use £50,000 for me.",
        )
    )

    assert resolver_owned_path.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert [issue.code for issue in resolver_owned_path.issues] == [
        "unknown_fact_claim_field_update"
    ]
    assert resolver_owned_path.context == prior


def test_context_validator_reports_structural_and_fact_conflicts_together():
    registry = build_default_fact_registry()
    reducer = ContextReducer(registry)
    validator = ContextChangeValidator(reducer, registry)
    seeded = reducer.reduce(
        ConversationContext.initial("conversation"),
        ContextPatch(
            expected_revision=0,
            operations=(
                SetFactOperation(
                    definition_key="person.employment_income",
                    definition_version="1",
                    subject_reference="person:self",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=Decimal("20000"),
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                ),
            ),
        ),
        turn_id="turn-0",
        evidence="£20,000",
    ).context
    prior = seeded.model_copy(
        update={
            "pending_fact_resolutions": (
                PendingFactResolution(
                    proposal_id="clarification-1",
                    claim_id="claim-1",
                    source_turn_id="turn-0",
                    scope_id="scope:primary-household",
                    referenced_entity_ids=("person:self",),
                    evidence="Clarify the value.",
                    status=FactResolutionStatus.NEEDS_CLARIFICATION,
                    prompt="What value should I use?",
                    relationship=FactClaimRelationship.DIRECT,
                    created_revision=seeded.revision,
                ),
            ),
        }
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=ContextChangeProposal(
                expected_revision=prior.revision,
                changes=(
                    FactClaim(
                        concept="employment income",
                        definition_key="person.employment_income",
                        subject_references=("person:self",),
                        scope_id="scope:primary-household",
                        value=ClaimedMoneyValue(
                            amount=Decimal("30000"),
                            period=MoneyPeriod.ANNUAL,
                        ),
                        evidence="£30,000",
                    ),
                    PendingFactResolutionResponse(
                        proposal_id="clarification-1",
                        action=PendingResolutionAction.ACCEPT,
                        evidence="Use £30,000 for me.",
                    ),
                ),
            ),
            turn_id="turn-1",
            evidence="Use £30,000 for me.",
        )
    )

    assert outcome.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert {issue.code for issue in outcome.issues} == {
        "pending_resolution_not_confirmable",
        "context_operation_conflicted",
    }
    assert all(
        operation.operation != "confirm_pending_fact_resolution"
        for operation in outcome.generated_operations
    )
    assert outcome.context == prior


def test_context_validator_routes_periodless_money_to_authoritative_resolution():
    registry = build_default_fact_registry()
    validator = ContextChangeValidator(ContextReducer(registry), registry)
    prior = ConversationContext.initial("conversation")
    proposal = ContextChangeProposal(
        expected_revision=0,
        changes=(
            FactClaim(
                concept="income",
                definition_key="person.employment_income",
                subject_references=("person:self",),
                scope_id="scope:primary-household",
                value=ClaimedMoneyValue(amount=Decimal("50000")),
                evidence="£50,000 of income",
            ),
        ),
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=proposal,
            turn_id="turn-1",
            evidence="How much tax would I pay on £50,000 of income?",
        )
    )

    assert outcome.status is ContextValidationStatus.RESOLUTION_REQUIRED
    assert outcome.generated_operations == ()
    assert outcome.claims_to_resolve == proposal.claims
    assert [issue.code for issue in outcome.issues] == [
        "authoritative_resolution_required"
    ]


def test_context_validator_inherits_period_from_one_compatible_active_fact():
    registry = build_default_fact_registry()
    reducer = ContextReducer(registry)
    prior = reducer.reduce(
        ConversationContext.initial("conversation"),
        ContextPatch(
            expected_revision=0,
            operations=(
                SetFactOperation(
                    definition_key="person.employment_income",
                    subject_reference="person:self",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=Decimal("50000"),
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                ),
            ),
        ),
        turn_id="turn-1",
        evidence="I earn £50,000 annually.",
    ).context
    validator = ContextChangeValidator(reducer, registry)
    proposal = ContextChangeProposal(
        expected_revision=prior.revision,
        changes=(
            FactClaim(
                concept="employment income",
                definition_key="person.employment_income",
                subject_references=("person:self",),
                scope_id="scope:primary-household",
                value=ClaimedMoneyValue(amount=Decimal("45000")),
                correction=True,
                evidence="Now use £45,000 for me.",
            ),
        ),
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=proposal,
            turn_id="turn-2",
            evidence="Now use £45,000 for me.",
        )
    )

    assert outcome.status is ContextValidationStatus.READY_TO_COMMIT
    active = outcome.context.active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    )
    assert active is not None
    assert active.assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("45000"),
            period=MoneyPeriod.ANNUAL,
        )
    )


def test_context_validator_rejects_conflicting_claims_for_one_fact_target():
    registry = build_default_fact_registry()
    validator = ContextChangeValidator(ContextReducer(registry), registry)
    prior = ConversationContext.initial("conversation")
    proposal = ContextChangeProposal(
        expected_revision=0,
        changes=(
            FactClaim(
                concept="age",
                definition_key="person.age",
                subject_references=("person:self",),
                scope_id="scope:primary-household",
                value=IntegerFactValue(value=27),
                evidence="I am 27.",
            ),
            FactClaim(
                concept="age",
                definition_key="person.age",
                subject_references=("person:self",),
                scope_id="scope:primary-household",
                value=IntegerFactValue(value=30),
                evidence="I am 30.",
            ),
        ),
    )

    outcome = validator.validate(
        ValidateContextChangeInput(
            context=prior,
            proposal=proposal,
            turn_id="turn-1",
            evidence="I am 27 and I am 30.",
        )
    )

    assert outcome.status is ContextValidationStatus.NEEDS_CLARIFICATION
    assert outcome.context == prior
    assert outcome.issues[0].code == "conflicting_fact_claims"
    assert outcome.issues[0].claim_index == 1


@pytest.mark.parametrize(
    "relationship,subject_references,value",
    (
        (
            FactClaimRelationship.DIRECT,
            ("person:self", "person:spouse"),
            IntegerFactValue(value=26),
        ),
        (
            FactClaimRelationship.SUM,
            ("person:self",),
            ClaimedMoneyValue(amount=Decimal("70000"), period=MoneyPeriod.ANNUAL),
        ),
    ),
)
def test_fact_claim_relationship_cardinality_is_structurally_validated(
    relationship,
    subject_references,
    value,
):
    with pytest.raises(ValidationError):
        FactClaim(
            concept="employment income",
            subject_references=subject_references,
            relationship=relationship,
            value=value,
            evidence="cited user statement",
        )


def test_default_fact_registry_covers_current_household_and_policy_inputs():
    registry = build_default_fact_registry()
    keys = {definition.key for definition in registry.definitions()}

    assert {
        "person.age",
        "person.name",
        "person.employment_income",
        "person.self_employment_income",
        "person.pension_income",
        "person.childcare_expenses",
        "person.medical_expenses",
        "household.members",
        "household.is_married",
        "household.has_children",
        "household.rent",
        "household.council_tax",
        "household.country",
        "analysis.policy_year",
        "analysis.requested_outputs",
        "policy.reform_instruction",
    } <= keys
    assert registry.get("household.rent").allow_explicit_absence is True
    assert registry.get("person.age").engine_binding == "person.age"
    assert (
        registry.get("analysis.requested_outputs").update_policy
        is FactUpdatePolicy.REPLACE_ON_EXPLICIT_ASSERTION
    )


def test_registry_rejects_duplicate_and_incomplete_definitions():
    definition = FactDefinition(
        key="person.test",
        value_kind=FactValueKind.INTEGER,
        subject_kinds=frozenset({EntityKind.PERSON}),
        label="Test",
    )
    with pytest.raises(ValueError, match="Duplicate fact definition"):
        FactDefinitionRegistry((definition, definition))
    with pytest.raises(ValidationError):
        FactDefinition(
            key="person.invalid",
            value_kind=FactValueKind.INTEGER,
            subject_kinds=frozenset(),
            label="Invalid",
        )


def test_reducer_distinguishes_duplicate_conflict_and_correction():
    reducer = ContextReducer(build_default_fact_registry())
    original = ConversationContext.initial("conversation")
    first = reducer.reduce(
        original,
        _age_patch(0, 40),
        turn_id="turn-1",
        evidence="I am 40",
    )
    assert first.context.revision == 1
    assert first.decisions[0].status is FactDecisionStatus.ACCEPTED

    duplicate = reducer.reduce(
        first.context,
        _age_patch(1, 40),
        turn_id="turn-2",
        evidence="I am 40",
    )
    assert duplicate.context.revision == 1
    assert duplicate.decisions[0].status is FactDecisionStatus.IGNORED

    conflict = reducer.reduce(
        first.context,
        _age_patch(1, 41),
        turn_id="turn-3",
        evidence="41",
    )
    assert conflict.context.revision == 1
    assert conflict.decisions[0].status is FactDecisionStatus.CONFLICTED

    corrected = reducer.reduce(
        first.context,
        _age_patch(1, 41, correction=True),
        turn_id="turn-4",
        evidence="Actually, I am 41",
    )
    assert corrected.context.revision == 2
    assert corrected.decisions[0].status is FactDecisionStatus.SUPERSEDED
    active = corrected.context.active_fact(
        "person.age", "person:self", "scope:primary-household"
    )
    assert active is not None
    assert active.assertion == PresentAssertion(value=IntegerFactValue(value=41))
    assert active.supersedes_fact_id == first.context.facts[0].fact_id


def test_new_requested_outputs_supersede_the_previous_request_without_correction():
    reducer = ContextReducer(build_default_fact_registry())

    def output_patch(revision: int, output: str) -> ContextPatch:
        return ContextPatch(
            expected_revision=revision,
            operations=(
                SetFactOperation(
                    definition_key="analysis.requested_outputs",
                    subject_reference="household:primary",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=TextSetFactValue(values=(output,))
                    ),
                ),
            ),
        )

    first = reducer.reduce(
        ConversationContext.initial("conversation"),
        output_patch(0, "income_tax"),
        turn_id="turn-1",
        evidence="Show income tax.",
    )
    second = reducer.reduce(
        first.context,
        output_patch(1, "universal_credit"),
        turn_id="turn-2",
        evidence="Now show Universal Credit.",
    )

    assert second.decisions[0].status is FactDecisionStatus.SUPERSEDED
    assert "new explicit assertion" in second.decisions[0].reason
    assert HouseholdContextView(second.context).requested_outputs() == (
        "universal_credit",
    )


def test_explicit_absence_is_not_numeric_zero_and_household_view_satisfies_costs():
    reducer = ContextReducer(build_default_fact_registry())
    context = ConversationContext.initial("conversation")
    reduced = reducer.reduce(
        context,
        ContextPatch(
            expected_revision=0,
            operations=(
                SetFactOperation(
                    definition_key="household.rent",
                    subject_reference="household:primary",
                    scope_id="scope:primary-household",
                    assertion=ExplicitAbsenceAssertion(),
                ),
                SetFactOperation(
                    definition_key="household.council_tax",
                    subject_reference="household:primary",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=Decimal("0"),
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                ),
            ),
        ),
        turn_id="turn-1",
        evidence="Neither applies",
    )

    rent, council_tax = reduced.context.active_facts()
    assert isinstance(rent.assertion, ExplicitAbsenceAssertion)
    assert isinstance(council_tax.assertion, PresentAssertion)
    evidence = HouseholdContextView(reduced.context).evidence()
    assert evidence.rent is not None and evidence.rent.amount == 0
    assert evidence.council_tax is not None and evidence.council_tax.amount == 0


def test_household_view_exposes_typed_analysis_year_and_requested_outputs():
    reducer = ContextReducer(build_default_fact_registry())
    reduced = reducer.reduce(
        ConversationContext.initial("conversation"),
        ContextPatch(
            expected_revision=0,
            operations=(
                SetFactOperation(
                    definition_key="analysis.policy_year",
                    subject_reference="household:primary",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=IntegerFactValue(value=2027)
                    ),
                ),
                SetFactOperation(
                    definition_key="analysis.requested_outputs",
                    subject_reference="household:primary",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=TextSetFactValue(values=("income tax",))
                    ),
                ),
            ),
        ),
        turn_id="turn-1",
        evidence="Calculate my 2027 income tax.",
    )

    view = HouseholdContextView(reduced.context)
    assert view.policy_year() == 2027
    assert view.requested_outputs() == ("income tax",)


def test_new_spouse_has_stable_identity_and_enters_active_household_scope():
    reducer = ContextReducer(build_default_fact_registry())
    context = ConversationContext.initial("conversation")
    reduced = reducer.reduce(
        context,
        ContextPatch(
            expected_revision=0,
            operations=(
                EnsureEntityOperation(
                    reference="new:spouse",
                    kind=EntityKind.PERSON,
                    aliases=("my spouse", "Sam"),
                    relationship_to_user="spouse",
                ),
                SetFactOperation(
                    definition_key="person.age",
                    subject_reference="new:spouse",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(value=IntegerFactValue(value=33)),
                ),
            ),
        ),
        turn_id="turn-1",
        evidence="My spouse Sam is 33",
    )

    spouse = next(
        item for item in reduced.context.entities if item.relationship_to_user == "spouse"
    )
    assert spouse.entity_id.startswith("entity:")
    assert spouse.entity_id in reduced.context.scopes[0].subject_entity_ids
    assert HouseholdContextView(reduced.context).evidence().people[1].entity_id == spouse.entity_id


def test_pending_requirements_accept_one_answer_for_multiple_registered_facts():
    reducer = ContextReducer(build_default_fact_registry())
    context = ConversationContext.initial("conversation")
    requirements = tuple(
        FactRequirement(
            requirement_id=f"household:{field}",
            fact_key=f"household.{field}",
            subject_entity_id="household:primary",
            scope_id="scope:primary-household",
            expected_value_kind="money",
            allow_explicit_absence=True,
            reason=f"Required to resolve household input household.{field}.",
        )
        for field in ("rent", "council_tax")
    )
    waiting = reducer.reduce(
        context,
        ContextPatch(
            expected_revision=0,
            operations=(
                ReplacePendingQuestionsOperation(
                    questions=(
                        PendingQuestion(
                            question_id="question-1",
                            capability_id="household_analysis",
                            prompt=(
                                "Does the household pay rent or Council Tax? Say that "
                                "either does not apply when appropriate."
                            ),
                            requirements=requirements,
                            created_turn_id="turn-1",
                        ),
                    )
                ),
            ),
        ),
        turn_id="turn-1",
        evidence="Capability requirement update.",
    ).context

    assert waiting.pending_questions[0].requirements == requirements
    answered = reducer.reduce(
        waiting,
        ContextPatch(
            expected_revision=waiting.revision,
            operations=tuple(
                SetFactOperation(
                    definition_key=requirement.fact_key,
                    subject_reference=requirement.subject_entity_id or "",
                    scope_id=requirement.scope_id,
                    assertion=ExplicitAbsenceAssertion(),
                )
                for requirement in waiting.pending_questions[0].requirements
            ),
        ),
        turn_id="turn-2",
        evidence="Neither applies.",
    )

    assert [decision.status for decision in answered.decisions] == [
        FactDecisionStatus.ACCEPTED,
        FactDecisionStatus.ACCEPTED,
    ]
    assert answered.context.pending_questions[0].status is (
        PendingQuestionStatus.ANSWER_RECEIVED
    )
    assert all(
        isinstance(
            answered.context.active_fact(
                fact_key,
                "household:primary",
                "scope:primary-household",
            ).assertion,
            ExplicitAbsenceAssertion,
        )
        for fact_key in ("household.rent", "household.council_tax")
    )


def test_context_rejects_duplicate_pending_questions_for_one_invocation():
    context = ConversationContext.initial("conversation")
    requirement = FactRequirement(
        requirement_id="household:age",
        fact_key="person.age",
        subject_entity_id="person:self",
        scope_id="scope:primary-household",
        expected_value_kind="integer",
        reason="Age is required for the calculation.",
    )
    reference = CapabilityInvocationReference(
        invocation_id="invocation-1",
        capability_id="household_analysis",
        capability_version="1",
        context_scope_id="scope:primary-household",
        context_revision=0,
    )
    payload = context.model_dump(mode="json")
    payload["pending_questions"] = [
        PendingQuestion(
            question_id="question-1",
            capability_id="household_analysis",
            capability_invocation=reference,
            prompt="What age should I use?",
            requirements=(requirement,),
            created_turn_id="turn-1",
        ).model_dump(mode="json"),
        PendingQuestion(
            question_id="question-2",
            capability_id="household_analysis",
            capability_invocation=reference,
            prompt="Please provide your age.",
            requirements=(requirement,),
            created_turn_id="turn-1",
        ).model_dump(mode="json"),
    ]

    with pytest.raises(
        ValidationError,
        match="cannot own more than one pending question",
    ):
        ConversationContext.model_validate(payload)


def test_context_repository_round_trip_conflict_and_delete(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'context.sqlite'}")
    SQLModel.metadata.create_all(engine)
    repository = SQLConversationContextRepository(engine=engine)
    reducer = ContextReducer(build_default_fact_registry())
    context = reducer.reduce(
        repository.load("conversation"),
        _age_patch(0, 42),
        turn_id="turn-1",
        evidence="I am 42",
    ).context

    repository.save(context, expected_revision=0)
    assert repository.load("conversation") == context
    with pytest.raises(ValueError, match="changed after it was loaded"):
        repository.save(
            context.model_copy(update={"revision": 2}),
            expected_revision=0,
        )
    repository.delete("conversation")
    assert repository.load("conversation").revision == 0


def test_chat_turn_persists_fact_and_exposes_private_decisions_in_debug(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'turn.sqlite'}")
    SQLModel.metadata.create_all(engine)
    registry = build_default_fact_registry()
    context_repository = SQLConversationContextRepository(engine=engine)
    trace_repository = SQLInvocationTraceRepository(engine=engine)
    composition = compose_runtime(
        tools=(
            AssessRelevanceTool(FakeRelevanceAssessor()),
            ProposeContextChangeTool(FakeContextInterpreter()),
            ValidateContextChangeTool(
                ContextChangeValidator(ContextReducer(registry), registry)
            ),
            ApplyContextChangeTool(ContextChangeApplier(context_repository)),
            ReduceContextPatchTool(ContextReducer(registry)),
        ),
        capabilities=(ConversationRelevanceCapability(),),
        tracer=InvocationTracer(sink=trace_repository),
    )
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=DirectModel(),
        context_repository=context_repository,
        fact_registry=registry,
    )
    context = composition.executor.context(
        request_id="request",
        conversation_id="conversation",
        turn_id="turn-1",
        is_cancelled=_not_cancelled,
    )
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "I am 42"}],
        session_id="conversation",
        turn_id="turn-1",
        debug=True,
    )

    events = asyncio.run(_collect(service, turn, context))

    assert isinstance(events[-1], TurnCompleted)
    active = context_repository.load("conversation").active_fact(
        "person.age", "person:self", "scope:primary-household"
    )
    assert active is not None
    activity = [event for event in events if isinstance(event, InvocationActivity)]
    identifiers = {event.record.identifier for event in activity}
    assert "propose_context_change" in identifiers
    assert "validate_context_change" in identifiers
    proposals = [
        event.record
        for event in activity
        if event.phase == "finished"
        and event.record.identifier == "propose_context_change"
    ]
    assert proposals
    assert proposals[-1].debug_output is not None
    assert proposals[-1].debug_output["changes"][0]["concept"] == "age"
    assert proposals[-1].debug_output["changes"][0]["subject_references"] == [
        "person:self"
    ]
    reductions = [
        event.record
        for event in activity
        if event.phase == "finished"
        and event.record.identifier == "validate_context_change"
    ]
    assert reductions
    assert reductions[-1].debug_input is not None
    assert reductions[-1].debug_input["proposal"]["changes"][0]["concept"] == "age"
    assert reductions[-1].debug_output is not None
    assert reductions[-1].debug_output["decisions"][0]["status"] == "accepted"
    assert reductions[-1].debug_output["generated_operations"][0]["operation"] == (
        "set_fact"
    )

    next_context = composition.executor.context(
        request_id="request-2",
        conversation_id="conversation",
        turn_id="turn-2",
        is_cancelled=_not_cancelled,
    )
    next_turn = ChatTurnInput(
        messages=[
            {"role": "user", "content": "I am 42"},
            {"role": "assistant", "content": "Thanks."},
            {"role": "user", "content": "Please retain my age."},
        ],
        session_id="conversation",
        turn_id="turn-2",
        debug=True,
    )

    next_events = asyncio.run(_collect(service, next_turn, next_context))

    assert isinstance(next_events[-1], TurnCompleted)
    retained = context_repository.load("conversation").active_fact(
        "person.age", "person:self", "scope:primary-household"
    )
    assert retained is not None
    assert retained.assertion == PresentAssertion(value=IntegerFactValue(value=42))
    assert context_repository.load("conversation").revision == 1


def test_chat_turn_retries_invalid_whole_proposal_without_partial_write(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'invalid-turn.sqlite'}")
    SQLModel.metadata.create_all(engine)
    registry = build_default_fact_registry()
    context_repository = SQLConversationContextRepository(engine=engine)

    class InvalidProposalInterpreter:
        def __init__(self):
            self.requests = []

        async def propose(self, request):
            self.requests.append(request)
            if len(self.requests) == 2:
                assert request.repair_issues[0].code == "unregistered_fact_claim"
                assert request.repair_issues[0].claim_index == 0
                assert request.previous_proposal is not None
                assert request.previous_proposal.claims[0].definition_key == (
                    "person.not_registered"
                )
            return ProposeContextChangeOutput(
                expected_revision=request.context.revision,
                provider_attempts=2,
                candidate_entities=(
                    ContextEntityCandidate(
                        reference="new:spouse",
                        kind=EntityKind.PERSON,
                        aliases=("my spouse",),
                        relationship_to_user="spouse",
                    ),
                ),
                changes=(
                    FactClaim(
                        concept="age",
                        definition_key="person.not_registered",
                        subject_references=("new:spouse",),
                        scope_id="scope:primary-household",
                        value=IntegerFactValue(value=30),
                        evidence="I have a spouse and one new household detail.",
                    ),
                ),
            )

    interpreter = InvalidProposalInterpreter()
    composition = compose_runtime(
        tools=(
            AssessRelevanceTool(FakeRelevanceAssessor()),
            ProposeContextChangeTool(interpreter),
            ValidateContextChangeTool(
                ContextChangeValidator(ContextReducer(registry), registry)
            ),
            ApplyContextChangeTool(ContextChangeApplier(context_repository)),
            ReduceContextPatchTool(ContextReducer(registry)),
        ),
        capabilities=(ConversationRelevanceCapability(),),
    )
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=NaturalContextClarificationModel(),
        context_repository=context_repository,
        fact_registry=registry,
    )
    capability_context = composition.executor.context(
        request_id="request",
        conversation_id="conversation-invalid",
        turn_id="turn-1",
        is_cancelled=_not_cancelled,
    )
    turn = ChatTurnInput(
        messages=[
            {
                "role": "user",
                "content": "I have a spouse and one new household detail.",
            }
        ],
        session_id="conversation-invalid",
        turn_id="turn-1",
        debug=True,
    )

    events = asyncio.run(_collect(service, turn, capability_context))

    assert isinstance(events[-1], TurnCompleted)
    assert events[-1].content == "Could you clarify which household detail you meant?"
    assert len(interpreter.requests) == 2
    persisted = context_repository.load("conversation-invalid")
    assert persisted.revision == 0
    assert all(
        entity.relationship_to_user != "spouse" for entity in persisted.entities
    )
    validation_results = [
        event.record
        for event in events
        if isinstance(event, InvocationActivity)
        and event.phase == "finished"
        and event.record.identifier == "validate_context_change"
    ]
    assert len(validation_results) == 2
    assert validation_results[-1].debug_output["status"] == "needs_clarification"
    assert validation_results[-1].debug_output["issues"][0]["claim_index"] == 0


def test_chat_turn_persists_calculated_fact_proposal_before_capability_use(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'resolution-turn.sqlite'}")
    SQLModel.metadata.create_all(engine)
    registry = build_default_fact_registry()
    context_repository = SQLConversationContextRepository(engine=engine)
    trace_repository = SQLInvocationTraceRepository(engine=engine)
    composition = compose_runtime(
        tools=(
            AssessRelevanceTool(FakeRelevanceAssessor()),
            ProposeContextChangeTool(AggregateIncomeInterpreter()),
            ValidateContextChangeTool(
                ContextChangeValidator(ContextReducer(registry), registry)
            ),
            ApplyContextChangeTool(ContextChangeApplier(context_repository)),
            ReduceContextPatchTool(ContextReducer(registry)),
            EmploymentIncomeSearchTool(),
            ResolveContextChangeTool(
                ContextChangeResolver(registry, EmploymentIncomeMapper())
            ),
        ),
        capabilities=(ConversationRelevanceCapability(),),
        tracer=InvocationTracer(sink=trace_repository),
    )
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=PlainDirectModel(),
        context_repository=context_repository,
        fact_registry=registry,
    )
    context = composition.executor.context(
        request_id="request",
        conversation_id="conversation",
        turn_id="turn-1",
        is_cancelled=_not_cancelled,
    )
    turn = ChatTurnInput(
        messages=[
            {
                "role": "user",
                "content": (
                    "I earn £50,000 and have a spouse. Together we earn £70,000."
                ),
            }
        ],
        session_id="conversation",
        turn_id="turn-1",
        debug=True,
    )

    events = asyncio.run(_collect(service, turn, context))

    assert isinstance(events[-1], TurnCompleted)
    persisted = context_repository.load("conversation")
    assert persisted.revision == 1
    proposal = persisted.pending_fact_resolutions[0]
    assert proposal.status.value == "awaiting_confirmation"
    assert proposal.variable_name == "employment_income"
    assert proposal.assignments[0].assertion.value.amount == Decimal("20000")
    identifiers = {
        event.record.identifier
        for event in events
        if isinstance(event, InvocationActivity)
    }
    assert {
        "propose_context_change",
        "validate_context_change",
        "resolve_context_change",
        "apply_context_change",
        "search_variables",
    } <= identifiers

    core_finished = [
        event.record
        for event in events
        if isinstance(event, InvocationActivity)
        and event.phase == "finished"
        and event.record.identifier
        in {
            "propose_context_change",
            "validate_context_change",
            "resolve_context_change",
            "apply_context_change",
        }
    ]
    assert [record.identifier for record in core_finished] == [
        "propose_context_change",
        "validate_context_change",
        "resolve_context_change",
        "validate_context_change",
        "apply_context_change",
    ]

    proposed_output = core_finished[0].debug_output
    first_validation_output = core_finished[1].debug_output
    resolver_input = core_finished[2].debug_input
    second_validation_input = core_finished[3].debug_input
    application_input = core_finished[4].debug_input
    assert proposed_output is not None
    assert first_validation_output is not None
    assert resolver_input is not None
    assert second_validation_input is not None
    assert application_input is not None
    assert first_validation_output["status"] == "resolution_required"
    assert first_validation_output["issues"][0]["code"] == (
        "authoritative_resolution_required"
    )
    assert resolver_input["proposal"]["changes"] == proposed_output["changes"]
    assert resolver_input["claims"] == first_validation_output["claims_to_resolve"]
    assert resolver_input["validation_issues"] == first_validation_output["issues"]
    assert second_validation_input["proposal"]["changes"] == proposed_output["changes"]
    assert second_validation_input["claims_resolved"] is True
    assert application_input["outcome"]["status"] == "ready_to_commit"


async def _collect(service, turn, context):
    return [
        event
        async for event in service.run(
            turn,
            is_cancelled=_not_cancelled,
            context=context,
        )
    ]
