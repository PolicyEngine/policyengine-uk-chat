from __future__ import annotations

import asyncio
from decimal import Decimal

from sqlmodel import SQLModel, create_engine

from capabilities.composition import compose_runtime
from capabilities.tracing import InvocationTracer
from conversation_context.engine_projection import HouseholdEngineFactProjector
from conversation_context.household_view import HouseholdContextView
from conversation_context.models import (
    AddPendingFactResolutionOperation,
    ClaimedMoneyValue,
    ConfirmPendingFactResolutionOperation,
    ContextPatch,
    ConversationContext,
    EntityKind,
    EnsureEntityOperation,
    FactClaimRelationship,
    FactResolutionAssignment,
    FactResolutionStatus,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolution,
    PresentAssertion,
    SetFactOperation,
    FactClaim,
)
from conversation_context.reducer import ContextReducer
from conversation_context.registry import FactValueKind, build_default_fact_registry
from conversation_context.variable_resolution import (
    MappingConfidence,
    MappingStatus,
    FactConstraintIssue,
    _solve_constraint,
    ContextChangeResolver,
    PolicyEngineVariableCandidate,
    ResolveContextChangeTool,
    ResolveContextChangeInput,
    VariableMappingResult,
    VariableMappingSelection,
)
from persistence.context_repository import SQLConversationContextRepository
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility
from tools.typed_models import SafeToolOutput, SearchVariablesInput


class FixedMapper:
    def __init__(self, selection: VariableMappingSelection) -> None:
        self.selection = selection
        self.calls = 0

    async def select(self, **_kwargs) -> VariableMappingResult:
        self.calls += 1
        return VariableMappingResult(selection=self.selection)


class FixedVariableSearchTool(Tool[SearchVariablesInput, SafeToolOutput]):
    spec = ToolSpec(
        identifier="search_variables",
        version="1",
        description="Return one fixed variable candidate.",
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


async def _not_cancelled() -> bool:
    return False


def _context(*, self_income: Decimal | None = Decimal("50000")):
    registry = build_default_fact_registry()
    reducer = ContextReducer(registry)
    operations = [
        EnsureEntityOperation(
            reference="new:spouse",
            kind=EntityKind.PERSON,
            aliases=("my spouse",),
            relationship_to_user="spouse",
        )
    ]
    if self_income is not None:
        operations.append(
            SetFactOperation(
                definition_key="person.employment_income",
                subject_reference="person:self",
                scope_id="scope:primary-household",
                assertion=PresentAssertion(
                    value=MoneyFactValue(
                        amount=self_income,
                        period=MoneyPeriod.ANNUAL,
                    )
                ),
            )
        )
    reduced = reducer.reduce(
        ConversationContext.initial("conversation"),
        ContextPatch(expected_revision=0, operations=tuple(operations)),
        turn_id="turn-1",
        evidence="I earn £50,000 and have a spouse.",
    )
    spouse = next(
        item
        for item in reduced.context.entities
        if item.relationship_to_user == "spouse"
    )
    return registry, reducer, reduced.context, spouse.entity_id


def _candidate() -> PolicyEngineVariableCandidate:
    return PolicyEngineVariableCandidate(
        name="employment_income",
        label="Employment income",
        entity="person",
        description="Income from employment.",
        definition_period="year",
        value_type="float",
    )


def _matched_mapper() -> FixedMapper:
    return FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.MATCHED,
            variable_name="employment_income",
            confidence=MappingConfidence.HIGH,
            target_period=MoneyPeriod.ANNUAL,
        )
    )


def _collective_claim() -> FactClaim:
    return FactClaim(
        claim_id="collective-income",
        concept="employment income",
        value=ClaimedMoneyValue(amount=Decimal("70000")),
        subject_references=("person:self", "my spouse"),
        relationship=FactClaimRelationship.SUM,
        evidence="We make £70,000 together.",
    )


def test_collective_value_resolves_one_unknown_and_requires_confirmation():
    registry, reducer, context, spouse_id = _context()
    resolver = ContextChangeResolver(registry, _matched_mapper())

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )

    proposal = output.decisions[0].proposal
    assert proposal.status is FactResolutionStatus.AWAITING_CONFIRMATION
    assert proposal.variable_name == "employment_income"
    assert proposal.expected_total == Decimal("70000")
    assert proposal.terms[0].known_value == Decimal("50000")
    assert proposal.terms[1].known_value is None
    assert proposal.assignments[0].subject_entity_id == spouse_id
    value = proposal.assignments[0].assertion.value
    assert isinstance(value, MoneyFactValue)
    assert value.amount == Decimal("20000")
    assert "£20,000" in proposal.prompt
    assert "correct breakdown" in proposal.prompt
    assert "household.income_total" not in {
        item.key for item in registry.definitions()
    }

    pending = reducer.reduce(
        context,
        output.patch,
        turn_id="turn-2",
        evidence="We make £70,000 collectively.",
    ).context
    assert HouseholdContextView(pending).pending_fact_resolutions() == (proposal,)
    assert pending.active_fact(
        "person.employment_income",
        spouse_id,
        "scope:primary-household",
    ) is None


def test_model_selected_registered_definition_is_validated_without_second_semantic_mapping():
    registry, _reducer, context, spouse_id = _context()
    mapper = FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.AMBIGUOUS,
            confidence=MappingConfidence.LOW,
        )
    )
    resolver = ContextChangeResolver(registry, mapper)
    claim = _collective_claim().model_copy(
        update={"definition_key": "person.employment_income"}
    )

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(claim,),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )

    decision = output.decisions[0]
    assert mapper.calls == 0
    assert decision.selection_source == "proposal"
    assert decision.selection.variable_name == "employment_income"
    assert decision.proposal.status is FactResolutionStatus.AWAITING_CONFIRMATION
    assert decision.proposal.assignments[0].subject_entity_id == spouse_id
    assert decision.proposal.assignments[0].assertion.value.amount == Decimal("20000")


def test_collective_value_does_not_substitute_an_artifact_for_missing_context_fact():
    registry, _reducer, context, spouse_id = _context(self_income=None)
    resolver = ContextChangeResolver(registry, _matched_mapper())

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )

    proposal = output.decisions[0].proposal
    assert proposal is not None
    assert proposal.status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert proposal.assignments == ()
    assert all(term.known_value is None for term in proposal.terms)
    assert spouse_id in {term.subject_entity_id for term in proposal.terms}


def test_direct_periodless_claim_persists_at_authoritative_definition_period():
    registry = build_default_fact_registry()
    reducer = ContextReducer(registry)
    context = ConversationContext.initial("conversation")
    mapper = FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.AMBIGUOUS,
            confidence=MappingConfidence.LOW,
        )
    )
    resolver = ContextChangeResolver(registry, mapper)
    claim = FactClaim(
        claim_id="direct-income",
        concept="employment income",
        definition_key="person.employment_income",
        value=ClaimedMoneyValue(amount=Decimal("50000")),
        subject_references=("person:self",),
        relationship=FactClaimRelationship.DIRECT,
        evidence="£50,000 of income",
    )

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(claim,),
                turn_id="turn-1",
                evidence="How much tax would I pay on £50,000 of income?",
            ),
            ((_candidate(),),),
        )
    )

    decision = output.decisions[0]
    assert mapper.calls == 0
    assert decision.status == "resolved"
    assert decision.proposal is None
    assert decision.operation == output.patch.operations[0]
    reduced = reducer.reduce(
        context,
        output.patch,
        turn_id="turn-1",
        evidence="How much tax would I pay on £50,000 of income?",
    )
    fact = reduced.context.active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    )
    assert fact is not None
    assert fact.assertion == PresentAssertion(
        value=MoneyFactValue(
            amount=Decimal("50000"),
            period=MoneyPeriod.ANNUAL,
        )
    )


def test_confirmation_applies_stored_assignment_without_recalculation():
    registry, reducer, context, spouse_id = _context()
    resolver = ContextChangeResolver(registry, _matched_mapper())
    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )
    pending = reducer.reduce(
        context,
        output.patch,
        turn_id="turn-2",
        evidence="We make £70,000 collectively.",
    ).context
    proposal = pending.pending_fact_resolutions[0]

    confirmed = reducer.reduce(
        pending,
        ContextPatch(
            expected_revision=pending.revision,
            operations=(
                ConfirmPendingFactResolutionOperation(
                    proposal_id=proposal.proposal_id,
                    accepted=True,
                ),
            ),
        ),
        turn_id="turn-3",
        evidence="Yes, that is correct.",
    )

    fact = confirmed.context.active_fact(
        "person.employment_income",
        spouse_id,
        "scope:primary-household",
    )
    assert fact is not None
    assert isinstance(fact.assertion, PresentAssertion)
    assert isinstance(fact.assertion.value, MoneyFactValue)
    assert fact.assertion.value.amount == Decimal("20000")
    assert confirmed.context.pending_fact_resolutions == ()
    assert confirmed.decisions[0].operation == "confirm_pending_fact_resolution"


def test_rejected_calculation_requests_an_explicit_breakdown():
    registry, reducer, context, spouse_id = _context()
    resolver = ContextChangeResolver(registry, _matched_mapper())
    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )
    pending = reducer.reduce(
        context,
        output.patch,
        turn_id="turn-2",
        evidence="We make £70,000 collectively.",
    ).context

    rejected = reducer.reduce(
        pending,
        ContextPatch(
            expected_revision=pending.revision,
            operations=(
                ConfirmPendingFactResolutionOperation(
                    proposal_id=pending.pending_fact_resolutions[0].proposal_id,
                    accepted=False,
                ),
            ),
        ),
        turn_id="turn-3",
        evidence="No.",
    )

    proposal = rejected.context.pending_fact_resolutions[0]
    assert proposal.status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert "exact amounts" in proposal.prompt
    assert len(rejected.context.active_facts()) == 1

    corrected = reducer.reduce(
        rejected.context,
        ContextPatch(
            expected_revision=rejected.context.revision,
            operations=(
                SetFactOperation(
                    definition_key="person.employment_income",
                    subject_reference=spouse_id,
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
        turn_id="turn-4",
        evidence="My spouse earns £20,000 per year.",
    )

    assert corrected.context.pending_fact_resolutions == ()
    spouse_income = corrected.context.active_fact(
        "person.employment_income",
        spouse_id,
        "scope:primary-household",
    )
    assert spouse_income is not None
    assert isinstance(spouse_income.assertion, PresentAssertion)
    assert isinstance(spouse_income.assertion.value, MoneyFactValue)
    assert spouse_income.assertion.value.amount == Decimal("20000")


def test_rejected_calculation_retains_inconsistent_explicit_breakdown():
    registry, reducer, context, spouse_id = _context()
    resolver = ContextChangeResolver(registry, _matched_mapper())
    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )
    pending = reducer.reduce(
        context,
        output.patch,
        turn_id="turn-2",
        evidence="We make £70,000 collectively.",
    ).context
    rejected = reducer.reduce(
        pending,
        ContextPatch(
            expected_revision=pending.revision,
            operations=(
                ConfirmPendingFactResolutionOperation(
                    proposal_id=pending.pending_fact_resolutions[0].proposal_id,
                    accepted=False,
                ),
            ),
        ),
        turn_id="turn-3",
        evidence="No.",
    ).context

    inconsistent = reducer.reduce(
        rejected,
        ContextPatch(
            expected_revision=rejected.revision,
            operations=(
                SetFactOperation(
                    definition_key="person.employment_income",
                    subject_reference=spouse_id,
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=Decimal("25000"),
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                ),
            ),
        ),
        turn_id="turn-4",
        evidence="My spouse earns £25,000 per year.",
    )

    assert len(inconsistent.context.pending_fact_resolutions) == 1


def test_legacy_incomplete_proposal_is_cleared_by_exact_later_breakdown():
    _registry, reducer, context, spouse_id = _context(self_income=None)
    proposal = PendingFactResolution(
        proposal_id="legacy-collective-income",
        claim_id="legacy-claim",
        source_turn_id="turn-2",
        scope_id="scope:primary-household",
        referenced_entity_ids=("person:self", spouse_id),
        evidence="We make £70,000 collectively.",
        status=FactResolutionStatus.NEEDS_CLARIFICATION,
        prompt="How should the total be divided?",
        relationship=FactClaimRelationship.SUM,
        expected_total=Decimal("70000"),
        period=MoneyPeriod.ANNUAL,
        created_revision=context.revision,
    )
    pending = reducer.reduce(
        context,
        ContextPatch(
            expected_revision=context.revision,
            operations=(AddPendingFactResolutionOperation(proposal=proposal),),
        ),
        turn_id="turn-2",
        evidence=proposal.evidence,
    ).context

    repaired = reducer.reduce(
        pending,
        ContextPatch(
            expected_revision=pending.revision,
            operations=tuple(
                SetFactOperation(
                    definition_key="person.employment_income",
                    subject_reference=entity_id,
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=amount,
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                )
                for entity_id, amount in (
                    ("person:self", Decimal("50000")),
                    (spouse_id, Decimal("20000")),
                )
            ),
        ),
        turn_id="turn-3",
        evidence="£50,000 for me and £20,000 for my spouse.",
    )

    assert repaired.context.pending_fact_resolutions == ()


def test_legacy_stuck_proposal_is_repaired_on_a_later_empty_patch():
    _registry, reducer, context, spouse_id = _context(self_income=None)
    with_facts = reducer.reduce(
        context,
        ContextPatch(
            expected_revision=context.revision,
            operations=tuple(
                SetFactOperation(
                    definition_key="person.employment_income",
                    subject_reference=entity_id,
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=amount,
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                )
                for entity_id, amount in (
                    ("person:self", Decimal("50000")),
                    (spouse_id, Decimal("20000")),
                )
            ),
        ),
        turn_id="turn-3",
        evidence="£50,000 for me and £20,000 for my spouse.",
    ).context
    legacy_proposal = PendingFactResolution(
        proposal_id="legacy-stuck-proposal",
        claim_id="legacy-stuck-claim",
        source_turn_id="turn-2",
        scope_id="scope:primary-household",
        referenced_entity_ids=("person:self", spouse_id),
        evidence="We make £70,000 collectively.",
        status=FactResolutionStatus.NEEDS_CLARIFICATION,
        prompt="How should the total be divided?",
        relationship=FactClaimRelationship.SUM,
        expected_total=Decimal("70000"),
        period=MoneyPeriod.ANNUAL,
        created_revision=context.revision,
    )
    stuck = with_facts.model_copy(
        update={"pending_fact_resolutions": (legacy_proposal,)}
    )

    repaired = reducer.reduce(
        stuck,
        ContextPatch(expected_revision=stuck.revision),
        turn_id="turn-4",
        evidence="Continue.",
    )

    assert repaired.context.pending_fact_resolutions == ()
    assert repaired.context.revision == stuck.revision + 1


def test_legacy_stuck_proposal_without_typed_total_is_repaired_from_evidence():
    _registry, reducer, context, spouse_id = _context(self_income=None)
    with_facts = reducer.reduce(
        context,
        ContextPatch(
            expected_revision=context.revision,
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
                SetFactOperation(
                    definition_key="person.employment_income",
                    subject_reference=spouse_id,
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
        turn_id="turn-3",
        evidence="50k for me, 20k for them",
    ).context
    malformed = PendingFactResolution(
        proposal_id="legacy-missing-constraint",
        claim_id="legacy-missing-constraint-claim",
        source_turn_id="turn-2",
        scope_id="scope:primary-household",
        referenced_entity_ids=("person:self", spouse_id),
        evidence="What if we collectively made 70k?",
        status=FactResolutionStatus.NEEDS_CLARIFICATION,
        prompt="An obsolete internal clarification",
        relationship=FactClaimRelationship.SUM,
        created_revision=context.revision,
    )
    stuck = with_facts.model_copy(
        update={"pending_fact_resolutions": (malformed,)}
    )

    repaired = reducer.reduce(
        stuck,
        ContextPatch(expected_revision=stuck.revision),
        turn_id="turn-4",
        evidence="Continue.",
    )

    assert repaired.context.pending_fact_resolutions == ()
    assert repaired.context.revision == stuck.revision + 1


def test_incomplete_confirmable_proposal_is_rejected():
    _registry, reducer, context, spouse_id = _context(self_income=None)
    proposal = PendingFactResolution(
        proposal_id="invalid-confirmable",
        claim_id="invalid-claim",
        source_turn_id="turn-2",
        scope_id="scope:primary-household",
        referenced_entity_ids=("person:self", spouse_id),
        evidence="We make £70,000 collectively.",
        status=FactResolutionStatus.AWAITING_CONFIRMATION,
        prompt="Is £20,000 correct?",
        relationship=FactClaimRelationship.SUM,
        assignments=(
            FactResolutionAssignment(
                definition_key="person.employment_income",
                subject_entity_id=spouse_id,
                scope_id="scope:primary-household",
                assertion=PresentAssertion(
                    value=MoneyFactValue(
                        amount=Decimal("20000"),
                        period=MoneyPeriod.ANNUAL,
                    )
                ),
            ),
        ),
        created_revision=context.revision,
    )

    reduced = reducer.reduce(
        context,
        ContextPatch(
            expected_revision=context.revision,
            operations=(AddPendingFactResolutionOperation(proposal=proposal),),
        ),
        turn_id="turn-2",
        evidence=proposal.evidence,
    )

    assert reduced.context.pending_fact_resolutions == ()
    assert reduced.decisions[0].status.value == "rejected"
    assert "validated variable" in reduced.decisions[0].reason


def test_additive_constraint_with_two_unknowns_asks_for_breakdown():
    registry, _reducer, context, _spouse_id = _context(self_income=None)
    resolver = ContextChangeResolver(registry, _matched_mapper())

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )

    proposal = output.decisions[0].proposal
    assert proposal.status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert proposal.assignments == ()
    assert "divided between" in proposal.prompt


def test_direct_monthly_claim_converts_to_annual_before_confirmation():
    registry = build_default_fact_registry()
    context = ConversationContext.initial("conversation")
    resolver = ContextChangeResolver(registry, _matched_mapper())
    claim = FactClaim(
        claim_id="monthly-income",
        concept="employment income",
        value=ClaimedMoneyValue(
            amount=Decimal("5000"),
            period=MoneyPeriod.MONTHLY,
        ),
        subject_references=("person:self",),
        relationship=FactClaimRelationship.DIRECT,
        evidence="I make £5,000 per month.",
    )

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(claim,),
                turn_id="turn-1",
                evidence="I receive £5,000 a month.",
            ),
            ((_candidate(),),),
        )
    )

    proposal = output.decisions[0].proposal
    value = proposal.assignments[0].assertion.value
    assert isinstance(value, MoneyFactValue)
    assert value.amount == Decimal("60000")
    assert value.period is MoneyPeriod.ANNUAL
    assert proposal.status is FactResolutionStatus.AWAITING_CONFIRMATION


def test_ambiguous_catalogue_mapping_does_not_create_a_fact_definition():
    registry, _reducer, context, _spouse_id = _context()
    mapper = FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.AMBIGUOUS,
            confidence=MappingConfidence.LOW,
        )
    )
    resolver = ContextChangeResolver(registry, mapper)
    keys_before = {item.key for item in registry.definitions()}

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            (
                (
                    PolicyEngineVariableCandidate(
                        name="gross_pay",
                        label="Gross pay",
                        entity="person",
                        definition_period="year",
                        value_type="float",
                    ),
                ),
            ),
        )
    )

    proposal = output.decisions[0].proposal
    assert proposal.status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert proposal.expected_total == Decimal("70000")
    assert proposal.period is None
    assert "PolicyEngine" not in proposal.prompt
    assert {item.key for item in registry.definitions()} == keys_before


def test_exact_catalogue_name_still_requires_model_semantic_selection():
    registry, _reducer, context, _spouse_id = _context()
    mapper = FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.MATCHED,
            variable_name="employment_income",
            confidence=MappingConfidence.HIGH,
            target_period=MoneyPeriod.ANNUAL,
        )
    )
    resolver = ContextChangeResolver(registry, mapper)

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim().model_copy(
                    update={
                        "concept": "employment_income",
                        "value": ClaimedMoneyValue(
                            amount=Decimal("70000"),
                            period=MoneyPeriod.ANNUAL,
                        ),
                    }
                ),),
                turn_id="turn-2",
                evidence="What if we collectively made 70k?",
            ),
            ((_candidate().model_copy(update={"definition_period": None}),),),
        )
    )

    proposal = output.decisions[0].proposal
    assert mapper.calls == 1
    assert proposal.status is FactResolutionStatus.AWAITING_CONFIRMATION
    assert proposal.variable_name == "employment_income"
    assert proposal.expected_total == Decimal("70000")
    assert proposal.period is MoneyPeriod.ANNUAL
    assert proposal.assignments[0].assertion.value.amount == Decimal("20000")
    assert "PolicyEngine" not in proposal.prompt
    assert "employment_income" not in proposal.prompt


def test_exact_catalogue_name_is_not_promoted_from_medium_model_confidence():
    registry, _reducer, context, _spouse_id = _context()
    mapper = FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.MATCHED,
            variable_name="employment_income",
            confidence=MappingConfidence.MEDIUM,
            target_period=MoneyPeriod.ANNUAL,
        )
    )
    resolver = ContextChangeResolver(registry, mapper)

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(
                    _collective_claim().model_copy(
                        update={"concept": "employment_income"}
                    ),
                ),
                turn_id="turn-2",
                evidence="What if we collectively made 70k?",
            ),
            ((_candidate(),),),
        )
    )

    proposal = output.decisions[0].proposal
    assert mapper.calls == 1
    assert proposal.status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert proposal.variable_name is None
    assert proposal.assignments == ()


def test_context_binding_is_evidence_for_model_semantic_selection_not_a_shortcut():
    registry, _reducer, context, spouse_id = _context()
    mapper = FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.MATCHED,
            variable_name="employment_income",
            confidence=MappingConfidence.HIGH,
            target_period=MoneyPeriod.ANNUAL,
        )
    )
    resolver = ContextChangeResolver(registry, mapper)
    claim = _collective_claim().model_copy(update={"concept": "income"})

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(claim,),
                turn_id="turn-2",
                evidence="What if we collectively made 70k?",
            ),
            (
                (
                    _candidate(),
                    PolicyEngineVariableCandidate(
                        name="self_employment_income",
                        label="Self-employment income",
                        entity="person",
                        definition_period="year",
                        value_type="float",
                    ),
                ),
            ),
        )
    )

    proposal = output.decisions[0].proposal
    assert mapper.calls == 1
    assert proposal.status is FactResolutionStatus.AWAITING_CONFIRMATION
    assert proposal.variable_name == "employment_income"
    assert proposal.period is MoneyPeriod.ANNUAL
    assert proposal.assignments[0].subject_entity_id == spouse_id
    assert proposal.assignments[0].assertion.value.amount == Decimal("20000")


def test_pending_resolution_round_trips_through_context_repository(tmp_path):
    registry, reducer, context, _spouse_id = _context()
    resolver = ContextChangeResolver(registry, _matched_mapper())
    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )
    pending = reducer.reduce(
        context,
        output.patch,
        turn_id="turn-2",
        evidence="We make £70,000 collectively.",
    ).context
    engine = create_engine(f"sqlite:///{tmp_path / 'fact-resolution.sqlite'}")
    SQLModel.metadata.create_all(engine)
    repository = SQLConversationContextRepository(engine=engine)

    repository.save(pending, expected_revision=0)

    assert repository.load("conversation") == pending


def test_debug_trace_exposes_mapping_candidates_and_calculated_assignment():
    registry, _reducer, conversation_context, _spouse_id = _context()
    tracer = InvocationTracer()
    composition = compose_runtime(
        tools=(
            FixedVariableSearchTool(),
            ResolveContextChangeTool(
                ContextChangeResolver(registry, _matched_mapper())
            ),
        ),
        capabilities=(),
        tracer=tracer,
    )
    context = composition.executor.context(
        request_id="request",
        conversation_id="conversation",
        turn_id="turn-2",
        is_cancelled=_not_cancelled,
        conversation_context=conversation_context,
    )

    output = asyncio.run(
        composition.executor.invoke_tool(
            "resolve_context_change",
            ResolveContextChangeInput(
                context=conversation_context,
                claims=(_collective_claim(),),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            caller=CallerType.RUNTIME,
            context=context,
        )
    )

    assert output.patch.operations
    public_records = tracer.records("conversation", include_private=False)
    assert [record.identifier for record in public_records] == ["search_variables"]
    debug_records = tracer.records("conversation", include_private=True)
    assert [record.identifier for record in debug_records] == [
        "resolve_context_change",
        "search_variables",
    ]
    resolution = debug_records[0]
    search = debug_records[1]
    assert search.parent_invocation_id == resolution.invocation_id
    assert resolution.debug_input is not None
    assert resolution.debug_output is not None
    decisions = resolution.debug_output["decisions"]
    assert decisions[0]["candidates"][0]["name"] == "employment_income"
    assignment = decisions[0]["proposal"]["assignments"][0]
    assert assignment["subject_entity_id"].startswith("entity:")
    assert assignment["assertion"]["value"]["amount"] == "20000"


def test_persisted_catalogue_fact_restores_its_engine_definition_after_restart():
    registry = build_default_fact_registry()
    definition = registry.ensure_engine_definition(
        variable_name="employee_pension_contributions",
        entity="person",
        label="Employee pension contributions",
        value_kind=FactValueKind.MONEY,
    )
    context = ContextReducer(registry).reduce(
        ConversationContext.initial("conversation"),
        ContextPatch(
            expected_revision=0,
            operations=(
                SetFactOperation(
                    definition_key=definition.key,
                    subject_reference="person:self",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=Decimal("1200"),
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                ),
            ),
        ),
        turn_id="turn-1",
        evidence="I contribute £1,200 per year to my pension.",
    ).context
    restored_context = ConversationContext.model_validate_json(
        context.model_dump_json()
    )
    fresh_registry = build_default_fact_registry()

    projection = HouseholdEngineFactProjector(fresh_registry).project(
        restored_context,
        scope_id="scope:primary-household",
        person_entity_ids=("person:self",),
        household_entity_id="household:primary",
    )

    restored_definition = fresh_registry.get(definition.key)
    assert restored_definition.engine_binding == (
        "person.employee_pension_contributions"
    )
    assert projection.people[0].values["employee_pension_contributions"] == 1200.0


def test_money_claim_cannot_target_a_registered_non_money_variable():
    registry = build_default_fact_registry()
    mapper = FixedMapper(
        VariableMappingSelection(
            status=MappingStatus.MATCHED,
            variable_name="age",
            confidence=MappingConfidence.HIGH,
            target_period=MoneyPeriod.ANNUAL,
        )
    )
    resolver = ContextChangeResolver(registry, mapper)
    claim = FactClaim(
        claim_id="wrong-type",
        concept="age",
        value=ClaimedMoneyValue(
            amount=Decimal("50"),
            period=MoneyPeriod.ANNUAL,
        ),
        subject_references=("person:self",),
        relationship=FactClaimRelationship.DIRECT,
        evidence="My age amount is £50.",
    )

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=ConversationContext.initial("conversation"),
                claims=(claim,),
                turn_id="turn-1",
                evidence="My age is worth £50.",
            ),
            (
                (
                    PolicyEngineVariableCandidate(
                        name="age",
                        label="Age",
                        entity="person",
                        definition_period="year",
                        value_type="int",
                    ),
                ),
            ),
        )
    )

    proposal = output.decisions[0].proposal
    assert proposal.status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert proposal.assignments == ()
    assert "does not accept a monetary amount" in proposal.prompt


def test_constraint_solver_exposes_typed_inconsistent_total_decision():
    solution = _solve_constraint(
        variable_name="employment_income",
        relationship=FactClaimRelationship.SUM,
        entity_ids=("person:self", "entity:spouse"),
        known_values={
            "person:self": Decimal("80000"),
            "entity:spouse": None,
        },
        expected_total=Decimal("70000"),
    )

    assert solution.issue is FactConstraintIssue.INCONSISTENT_TOTAL
    assert solution.subject_entity_id is None
    assert solution.amount is None
    assert solution.terms[0].known_value == Decimal("80000")


def test_unresolved_entity_reference_cannot_be_silently_dropped():
    registry, _reducer, context, _spouse_id = _context()
    resolver = ContextChangeResolver(registry, _matched_mapper())
    claim = _collective_claim().model_copy(
        update={"subject_references": ("person:self", "unknown person")}
    )

    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(claim,),
                turn_id="turn-2",
                evidence="We make £70,000 collectively.",
            ),
            ((_candidate(),),),
        )
    )

    proposal = output.decisions[0].proposal
    assert proposal.status is FactResolutionStatus.NEEDS_CLARIFICATION
    assert proposal.assignments == ()
    assert "Which exact people" in proposal.prompt


def test_direct_correction_supersedes_the_active_engine_fact_without_confirmation():
    registry, reducer, context, _spouse_id = _context()
    resolver = ContextChangeResolver(registry, _matched_mapper())
    claim = FactClaim(
        claim_id="corrected-income",
        concept="employment income",
        value=ClaimedMoneyValue(
            amount=Decimal("60000"),
            period=MoneyPeriod.ANNUAL,
        ),
        subject_references=("person:self",),
        relationship=FactClaimRelationship.DIRECT,
        correction=True,
        evidence="Actually, my employment income is £60,000.",
    )
    output = asyncio.run(
        resolver.resolve(
            ResolveContextChangeInput(
                context=context,
                claims=(claim,),
                turn_id="turn-2",
                evidence="Actually, use £60,000 per year for me.",
            ),
            ((_candidate(),),),
        )
    )
    corrected = reducer.reduce(
        context,
        output.patch,
        turn_id="turn-2",
        evidence="Actually, use £60,000 per year for me.",
    )
    assert output.decisions[0].status == "resolved"
    assert corrected.context.pending_fact_resolutions == ()

    fact = corrected.context.active_fact(
        "person.employment_income",
        "person:self",
        "scope:primary-household",
    )
    assert fact is not None
    assert isinstance(fact.assertion, PresentAssertion)
    assert isinstance(fact.assertion.value, MoneyFactValue)
    assert fact.assertion.value.amount == Decimal("60000")
    assert fact.supersedes_fact_id is not None
