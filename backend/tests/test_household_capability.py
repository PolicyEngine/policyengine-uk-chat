from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from capabilities.composition import compose_runtime
from capabilities.contracts import Completed, Failed, NeedsInput
from capabilities.household import (
    AmountFrequency,
    AssembleHouseholdCandidateTool,
    HouseholdAnalysisCapability,
    HouseholdAnalysisDraft,
    HouseholdAnalysisInput,
    HouseholdEvidenceCoordinator,
    HouseholdEvidence,
    HouseholdEvidenceAmbiguity,
    HouseholdEvidenceAmbiguityKind,
    HouseholdEvidenceResult,
    HouseholdInputResolver,
    HouseholdInvocationCoordinator,
    HouseholdResultPresenter,
    PeriodicAmount,
    PersonEvidence,
)
from capabilities.input_resolution import InputSource
from capabilities.policy_reform import (
    PolicyReformCapability,
    ReformResolutionDecision,
    ReformResolutionKind,
    ResolveReformTool,
)
from tools.contracts import CallerType
from tools.analysis_support import ExtractResultFindingsTool
from tools.typed_dispatch import build_dispatch_tools
from conversation_context.models import (
    ContextPatch,
    ExplicitAbsenceAssertion,
    FactClaimRelationship,
    FactResolutionAssignment,
    FactResolutionStatus,
    MoneyFactValue,
    MoneyPeriod,
    PendingFactResolution,
    PresentAssertion,
    SetFactOperation,
    ConversationContext,
)
from conversation_context.engine_projection import HouseholdEngineFactProjector
from conversation_context.reducer import ContextReducer
from conversation_context.registry import FactValueKind, build_default_fact_registry


class MemoryArtifacts:
    def __init__(self):
        self.artifacts = []
        self.waiting = []

    async def find_artifacts(self, *, conversation_id, artifact_model):
        return tuple(
            item
            for item in self.artifacts
            if item.provenance.conversation_id == conversation_id
            and isinstance(item, artifact_model)
        )

    async def save_artifact(self, *, conversation_id, artifact):
        assert artifact.provenance.conversation_id == conversation_id
        self.artifacts.append(artifact)
        return artifact

    async def save_waiting(self, invocation):
        self.waiting.append(invocation)
        return invocation

    async def list_waiting(self, *, conversation_id, capability_id):
        return tuple(
            item
            for item in self.waiting
            if item.conversation_id == conversation_id
            and item.capability_id == capability_id
        )

    async def update_waiting(self, *, invocation_id, partial_input):
        for index, item in enumerate(self.waiting):
            if item.invocation_id == invocation_id:
                updated = item.model_copy(update={"partial_input": partial_input})
                self.waiting[index] = updated
                return updated
        raise KeyError(invocation_id)

    async def remove_waiting(self, *, invocation_id):
        self.waiting = [
            item for item in self.waiting if item.invocation_id != invocation_id
        ]


def test_household_evidence_coordinator_merges_current_values_over_retained_values():
    coordinator = HouseholdEvidenceCoordinator()
    retained = HouseholdEvidence(
        people=(
            PersonEvidence(
                age=35,
                employment_income=PeriodicAmount(
                    amount=50_000,
                    frequency=AmountFrequency.ANNUAL,
                ),
                sources={"age": "user", "employment_income": "user"},
            ),
        ),
        country="ENGLAND",
        sources={"country": "default"},
    )
    current = HouseholdEvidence(
        people=(
            PersonEvidence(age=36, sources={"age": "user"}),
        ),
    )

    merged = coordinator.merge(retained, current)

    assert merged.people[0].age == 36
    assert merged.people[0].employment_income.amount == 50_000
    assert merged.country == "ENGLAND"


def test_household_result_presenter_builds_scenario_outputs_and_defaults():
    presenter = HouseholdResultPresenter()

    outputs = presenter.extract_outputs(
        {
            "status": "success",
            "year": 2026,
            "reform_applied": True,
            "result_id": "result-1",
            "baseline": {"income_tax": 7_000},
            "reform": {"income_tax": 7_500},
        },
        ("income_tax",),
    )
    assumptions = presenter.completed_assumptions(
        (),
        year=2026,
        year_source=InputSource.SERVER_DEFAULT,
        defaulted_to_current_policy=True,
    )

    assert [(item.metric_id, item.value) for item in outputs] == [
        ("baseline", 7_000),
        ("reform", 7_500),
        ("change", 500),
    ]
    assert [item.field_id for item in assumptions] == [
        "policy.year",
        "policy.scenario",
    ]


def test_household_invocation_coordinator_merges_only_resumable_input():
    coordinator = HouseholdInvocationCoordinator(
        capability_id="household_analysis",
        capability_version="1",
    )
    retained = HouseholdAnalysisDraft(
        description="Original request",
        year=2026,
        requested_outputs=("income_tax",),
        reform_instruction="current law",
    )

    merged = coordinator.merge_input(
        retained,
        HouseholdAnalysisInput(
            description="I am 35",
            requested_outputs=("universal_credit",),
        ),
    )

    assert merged.description == "I am 35"
    assert merged.year == 2026
    assert merged.reform_instruction == "current law"
    assert merged.requested_outputs == ("income_tax",)


class FakeAssembler:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def assemble(self, **kwargs):
        self.calls.append(kwargs)
        return self.results.pop(0)


class UnusedReformResolver:
    async def resolve(self, **kwargs):
        raise AssertionError("Current-law household analysis must not call the resolver")

    async def correct_representation(self, **kwargs):
        raise AssertionError("No correction is expected")


class ClarifyingReformResolver:
    async def resolve(self, **kwargs):
        del kwargs
        return ReformResolutionDecision(
            outcome=ReformResolutionKind.NEEDS_CLARIFICATION,
            summary="Which allowance should change?",
            clarification="Which allowance should change?",
        )

    async def correct_representation(self, **kwargs):
        raise AssertionError("No correction is expected")


def _evidence(*, age=35, employment_income=None, label_usage=True):
    return HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(
                    age=age,
                    employment_income=(
                        PeriodicAmount(
                            amount=employment_income,
                            frequency=AmountFrequency.ANNUAL,
                        )
                        if employment_income is not None
                        else None
                    ),
                    sources={"age": "user", **(
                        {"employment_income": "user"}
                        if employment_income is not None
                        else {}
                    )},
                ),
            ),
            rent=PeriodicAmount(amount=0, frequency=AmountFrequency.ANNUAL),
            council_tax=PeriodicAmount(
                amount=0,
                frequency=AmountFrequency.ANNUAL,
            ),
            sources={"rent": "user", "council_tax": "user"},
        ),
        usage={"input_tokens": 4 if label_usage else 0, "output_tokens": 2},
    )


def _runtime(
    monkeypatch,
    assembler,
    *,
    validation=None,
    missing_label=None,
    simulation=None,
    reform_resolver=None,
    household_capability=None,
):
    from tools import typed_dispatch

    calls = []
    validation = validation or {"valid": True, "year": 2026}
    simulation = simulation or {
        "status": "success",
        "year": 2026,
        "reform_applied": False,
        "household_net_income": 24_000,
        "universal_credit": 5_000,
        "result_id": "household_simulation_private",
    }

    def execute(identifier, payload, context=None):
        calls.append((identifier, payload, context))
        if identifier == "get_variable":
            if payload["name"] == missing_label:
                return {"status": "error", "error": "Unknown variable"}
            return {
                "status": "success",
                "variable": {
                    "name": payload["name"],
                    "label": payload["name"].replace("_", " ").title(),
                    "entity": "person",
                },
            }
        if identifier == "search_variables":
            if "universal" in payload["query"].casefold():
                return {
                    "status": "success",
                    "variables": [
                        {
                            "name": "universal_credit",
                            "label": "Universal Credit",
                            "entity": "benunit",
                        }
                    ],
                }
            return {"status": "success", "variables": []}
        if identifier == "list_reform_targets":
            return {
                "status": "success",
                "targets": [
                    {
                        "path": "gov.example.amount",
                        "label": "Example amount",
                    }
                ],
            }
        if identifier == "get_parameter":
            return {
                "status": "success",
                "parameter": {
                    "path": payload["path"],
                    "label": "Example amount",
                    "unit": "currency-GBP",
                    "year": payload["year"],
                    "value": 10_000,
                },
            }
        if identifier == "validate_household":
            return validation
        if identifier == "run_household_simulation":
            result = dict(simulation)
            if result.get("status") == "success" and "result_id" not in result:
                result["result_id"] = context.result_store.put(
                    "household_simulation",
                    object(),
                    result,
                )
            return result
        raise AssertionError(f"Unexpected retained tool: {identifier}")

    monkeypatch.setattr(typed_dispatch, "execute_tool", execute)
    artifacts = MemoryArtifacts()
    composition = compose_runtime(
        tools=[
            *build_dispatch_tools(),
            ResolveReformTool(reform_resolver or UnusedReformResolver()),
            AssembleHouseholdCandidateTool(assembler),
            ExtractResultFindingsTool(),
        ],
        capabilities=[
            PolicyReformCapability(),
            household_capability or HouseholdAnalysisCapability(),
        ],
    )

    async def not_cancelled():
        return False

    context = composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=not_cancelled,
        artifacts=artifacts,
    )
    return composition, context, artifacts, calls


def _invoke(composition, context, payload):
    return asyncio.run(
        composition.executor.invoke_capability(
            "household_analysis",
            payload,
            caller=CallerType.MODEL,
            context=context,
        )
    )


def test_current_law_household_calculation_reports_every_material_assumption(
    monkeypatch,
):
    assembler = FakeAssembler([_evidence()])
    composition, context, artifacts, calls = _runtime(monkeypatch, assembler)

    outcome = _invoke(
        composition,
        context,
        {
            "description": "A single 35-year-old adult with no rent or Council Tax",
            "requested_outputs": ["Universal Credit"],
        },
    )

    assert isinstance(outcome, Completed)
    assumptions = {item.plain_statement for item in outcome.value.assumptions}
    assert "Policy year: 2026." in assumptions
    assert "Policy scenario: current policy." in assumptions
    assert "The household has no children." in assumptions
    assert "The household has no partner or spouse." in assumptions
    assert "The household has no childcare expenses." in assumptions
    assert "The household lives in England." in assumptions
    assert "The household has no rent." not in assumptions
    assert "The household has no council tax." not in assumptions
    assert any("employment income" in statement for statement in assumptions)
    assert any("self employment income" in statement for statement in assumptions)
    assert any("pension income" in statement for statement in assumptions)
    assert all("_" not in item.label for item in outcome.value.assumptions)
    output_ids = {output.output_id for output in outcome.value.result.outputs}
    assert output_ids == {"universal_credit"}
    assert 5_000 in {fact.value for fact in outcome.value.narration_facts}
    assert outcome.value.narration_fallback.startswith("### Results")
    assert "- Universal Credit: £5,000.00 per year" in (
        outcome.value.narration_fallback
    )
    assert "The calculation gives" not in outcome.value.narration_fallback
    assert "Report every calculated value" in (
        outcome.value.narration_requirement
    )
    assert "absent from result.outputs" in outcome.value.narration_requirement
    assert "### Assumptions used" in outcome.value.narration_fallback
    assert "- The household lives in England." in (
        outcome.value.narration_fallback
    )
    assert set(outcome.value.assumption_statements) == assumptions
    facts_by_label = {
        fact.label: fact.value for fact in outcome.value.narration_facts
    }
    assert facts_by_label["Number of people in the household"] == 1
    assert facts_by_label["Number of children in the household"] == 0
    assert facts_by_label["Age"] == 35
    assert all("Person 1" not in label for label in facts_by_label)
    assert facts_by_label["Monthly universal credit"] == 5_000 / 12
    assert facts_by_label["Weekly universal credit"] == 5_000 / 52
    assert "No employment income." in assumptions
    assert all("Person 1" not in statement for statement in assumptions)
    assert context.model_usage.snapshot().input_tokens == 4
    artifact_types = [item.artifact_type for item in artifacts.artifacts]
    assert artifact_types == ["policy_scenario", "household", "household_result"]
    result = outcome.value.result
    assert not hasattr(result, "result_id")
    assert "household_simulation_private" not in result.model_dump_json()
    assert [identifier for identifier, _payload, _context in calls].count(
        "run_household_simulation"
    ) == 1


def test_named_output_is_detected_when_model_omits_requested_outputs(monkeypatch):
    assembler = FakeAssembler([_evidence(employment_income=50_000)])
    composition, context, _artifacts, calls = _runtime(
        monkeypatch,
        assembler,
        simulation={
            "status": "success",
            "year": 2026,
            "reform_applied": False,
            "income_tax": 7_486,
            "household_net_income": 39_339.55,
        },
    )

    outcome = _invoke(
        composition,
        context.with_current_user_message(
            "How much income tax would I pay on £50,000 of annual employment income?"
        ),
        {
            "description": "A 35-year-old with £50,000 annual employment income",
        },
    )

    assert isinstance(outcome, Completed)
    assert [item.output_id for item in outcome.value.result.outputs] == [
        "income_tax"
    ]
    simulation_input = next(
        payload
        for identifier, payload, _context in calls
        if identifier == "run_household_simulation"
    )
    assert simulation_input["extra_variables"] == ["income_tax"]


def test_consequential_missing_age_returns_local_persisted_needs_input(monkeypatch):
    assembler = FakeAssembler([_evidence(age=None)])
    composition, context, artifacts, calls = _runtime(monkeypatch, assembler)

    outcome = _invoke(
        composition,
        context,
        {"description": "One adult with no other details and no housing costs"},
    )

    assert isinstance(outcome, NeedsInput)
    assert "age" in outcome.prompt
    assert len(artifacts.waiting) == 1
    assert artifacts.waiting[0].capability_id == "household_analysis"
    assert "run_household_simulation" not in [
        identifier for identifier, _payload, _context in calls
    ]


def test_tax_only_request_uses_exact_user_evidence_and_omits_housing_questions(
    monkeypatch,
):
    first_evidence = HouseholdEvidenceResult(
        evidence=HouseholdEvidence()
    )
    answer_evidence = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(PersonEvidence(age=26, sources={"age": "user"}),),
            rent=PeriodicAmount(amount=0, frequency=AmountFrequency.ANNUAL),
            sources={"rent": "user"},
        )
    )
    assembler = FakeAssembler([first_evidence, answer_evidence])
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        assembler,
        simulation={
            "status": "success",
            "year": 2026,
            "reform_applied": False,
            "income_tax": 7_486,
            "national_insurance": 3_011.60,
            "household_tax": 10_497.60,
        },
    )

    first = _invoke(
        composition,
        context.with_current_user_message(
            "How much tax would I pay on £50,000 of income?"
        ),
        {
            "description": "A person with £50,000 of annual income",
            "requested_outputs": [
                "income tax",
                "national insurance contributions",
                "total tax",
            ],
        },
    )

    assert isinstance(first, NeedsInput)
    assert first.prompt == "What age should I use for this calculation?"
    assert "Person 1" not in first.prompt
    assert "rent" not in first.prompt.casefold()
    assert "council tax" not in first.prompt.casefold()
    assert assembler.calls[0]["description"] == (
        "How much tax would I pay on £50,000 of income?"
    )
    second = _invoke(
        composition,
        context.with_current_user_message("I am 26 and I don't pay rent."),
        {
            "description": (
                "A single person, age 26, with £50,000 annual income, does not pay "
                "rent or Council Tax."
            ),
            "requested_outputs": ["tax"],
        },
    )

    assert isinstance(second, Completed)
    assert artifacts.waiting == []
    assert assembler.calls[1]["description"] == "I am 26 and I don't pay rent."
    validation_input = [
        payload
        for identifier, payload, _context in calls
        if identifier == "validate_household"
    ][-1]
    assert validation_input["people"][0]["employment_income"] == 50_000
    assert validation_input["household"]["rent"] == 0
    assert validation_input["household"]["council_tax"] == 0
    assert {item.output_id for item in second.value.result.outputs} == {
        "income_tax",
        "national_insurance",
        "household_tax",
    }
    assert (
        "The £50,000.00 income amount is treated as annual employment income."
        in second.value.assumption_statements
    )
    assert "total_wealth" not in {
        payload.get("name")
        for identifier, payload, _context in calls
        if identifier == "get_variable"
    }


def test_production_context_path_retains_generic_tax_income_across_age_clarification(
    monkeypatch,
):
    registry = build_default_fact_registry()
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        None,
        simulation={
            "status": "success",
            "year": 2026,
            "reform_applied": False,
            "income_tax": 7_486,
            "national_insurance": 2_994.40,
            "household_tax": 10_660.45,
        },
        household_capability=HouseholdAnalysisCapability(
            HouseholdEngineFactProjector(registry)
        ),
    )
    conversation_context = ConversationContext.initial("conversation-1")
    context = context.with_conversation_context(conversation_context)

    first = _invoke(
        composition,
        context.with_current_user_message(
            "How much tax would I pay on £50,000 of income?"
        ),
        {
            "description": "A person with £50,000 of income",
            "requested_outputs": ["tax"],
        },
    )

    assert isinstance(first, NeedsInput)
    assert first.missing_fields == ("people[0].age",)
    assert "rent" not in first.prompt.casefold()
    assert "council tax" not in first.prompt.casefold()
    assert artifacts.waiting[0].partial_input.requested_outputs == (
        "income_tax",
        "national_insurance",
        "household_tax",
    )
    retained_income = (
        artifacts.waiting[0]
        .partial_input.invocation_defaults.evidence.people[0]
        .employment_income
    )
    assert retained_income is not None
    assert retained_income.amount == 50_000

    conversation_context = ContextReducer(registry).reduce(
        conversation_context,
        ContextPatch(
            expected_revision=conversation_context.revision,
            operations=(
                SetFactOperation(
                    definition_key="person.age",
                    subject_reference="person:self",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(value={"kind": "integer", "value": 38}),
                ),
            ),
        ),
        turn_id="turn-2",
        evidence="38",
    ).context

    second = _invoke(
        composition,
        context.with_conversation_context(conversation_context).with_current_user_message(
            "38"
        ),
        {
            "description": "A 38-year-old person with £50,000 of income.",
            "requested_outputs": ["tax"],
        },
    )

    assert isinstance(second, Completed)
    validation_input = next(
        payload
        for identifier, payload, _context in reversed(calls)
        if identifier == "validate_household"
    )
    assert validation_input["people"][0]["employment_income"] == 50_000
    assert validation_input["extra_variables"] == [
        "income_tax",
        "national_insurance",
        "household_tax",
    ]
    assert 50_000 in {
        fact.value for fact in second.value.narration_facts
    }


def test_production_context_path_does_not_simulate_an_unaccounted_sterling_input(
    monkeypatch,
):
    registry = build_default_fact_registry()
    conversation_context = ContextReducer(registry).reduce(
        ConversationContext.initial("conversation-1"),
        ContextPatch(
            expected_revision=0,
            operations=(
                SetFactOperation(
                    definition_key="person.age",
                    subject_reference="person:self",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(value={"kind": "integer", "value": 38}),
                ),
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
                    assertion=ExplicitAbsenceAssertion(),
                ),
            ),
        ),
        turn_id="turn-1",
        evidence="I am 38 and pay no rent or Council Tax.",
    ).context
    composition, context, _artifacts, calls = _runtime(
        monkeypatch,
        None,
        household_capability=HouseholdAnalysisCapability(
            HouseholdEngineFactProjector(registry)
        ),
    )

    outcome = _invoke(
        composition,
        context.with_conversation_context(conversation_context).with_current_user_message(
            "How much Universal Credit would I get on £50,000 of income?"
        ),
        {
            "description": "A 38-year-old asking about Universal Credit.",
            "requested_outputs": ["Universal Credit"],
        },
    )

    assert isinstance(outcome, NeedsInput)
    assert "£50,000" in outcome.prompt
    assert "what each amount represents" in outcome.prompt
    assert not any(
        identifier in {"validate_household", "run_household_simulation"}
        for identifier, _payload, _context in calls
    )


def test_accepted_context_income_overrides_retained_invocation_default(monkeypatch):
    registry = build_default_fact_registry()
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        None,
        simulation={
            "status": "success",
            "year": 2026,
            "reform_applied": False,
            "income_tax": 9_886,
            "national_insurance": 3_394.40,
            "household_tax": 13_460.45,
        },
        household_capability=HouseholdAnalysisCapability(
            HouseholdEngineFactProjector(registry)
        ),
    )
    conversation_context = ConversationContext.initial("conversation-1")

    first = _invoke(
        composition,
        context.with_conversation_context(conversation_context).with_current_user_message(
            "How much tax would I pay on £50,000 of income?"
        ),
        {
            "description": "A person with £50,000 of income",
            "requested_outputs": ["tax"],
        },
    )
    assert isinstance(first, NeedsInput)

    conversation_context = ContextReducer(registry).reduce(
        conversation_context,
        ContextPatch(
            expected_revision=conversation_context.revision,
            operations=(
                SetFactOperation(
                    definition_key="person.age",
                    subject_reference="person:self",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value={"kind": "integer", "value": 38}
                    ),
                ),
                SetFactOperation(
                    definition_key="person.employment_income",
                    subject_reference="person:self",
                    scope_id="scope:primary-household",
                    assertion=PresentAssertion(
                        value=MoneyFactValue(
                            amount=Decimal("60000"),
                            period=MoneyPeriod.ANNUAL,
                        )
                    ),
                ),
            ),
        ),
        turn_id="turn-2",
        evidence="I am 38. Actually, use £60,000 of annual employment income.",
    ).context

    second = _invoke(
        composition,
        context.with_conversation_context(conversation_context).with_current_user_message(
            "I am 38. Actually, use £60,000 of annual employment income."
        ),
        {
            "description": "A 38-year-old with £60,000 annual employment income.",
            "requested_outputs": ["tax"],
        },
    )

    assert isinstance(second, Completed)
    validation_input = next(
        payload
        for identifier, payload, _context in reversed(calls)
        if identifier == "validate_household"
    )
    assert validation_input["people"][0]["employment_income"] == 60_000
    assert artifacts.waiting == []
    assert all("£50,000.00" not in item for item in second.value.assumption_statements)
    assert 60_000 in {fact.value for fact in second.value.narration_facts}


def test_reform_clarification_is_translated_to_household_partial_input(monkeypatch):
    assembler = FakeAssembler([_evidence()])
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        assembler,
        reform_resolver=ClarifyingReformResolver(),
    )

    outcome = _invoke(
        composition,
        context,
        {
            "description": "A single 35-year-old adult with no rent or Council Tax",
            "reform_instruction": "Increase the allowance",
        },
    )

    assert isinstance(outcome, NeedsInput)
    assert outcome.prompt == "Which allowance should change?"
    assert outcome.partial_input["description"] == (
        "A single 35-year-old adult with no rent or Council Tax"
    )
    assert outcome.partial_input["reform_instruction"] == "Increase the allowance"
    assert "resuming_invocation_id" not in outcome.partial_input
    assert artifacts.waiting[-1].capability_id == "household_analysis"
    assert "run_household_simulation" not in [
        identifier for identifier, _payload, _context in calls
    ]


def test_missing_catalogue_label_fails_before_validation_or_simulation(monkeypatch):
    assembler = FakeAssembler([_evidence()])
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        assembler,
        missing_label="rent",
    )

    outcome = _invoke(
        composition,
        context,
        {"description": "A single 35-year-old adult with no rent or Council Tax"},
    )

    assert isinstance(outcome, Failed)
    assert outcome.error_code == "household_assembly_contract"
    assert artifacts.artifacts == []
    assert "validate_household" not in [
        identifier for identifier, _payload, _context in calls
    ]


def test_invalid_household_never_reaches_simulation_and_preserves_partial_input(
    monkeypatch,
):
    assembler = FakeAssembler([_evidence(employment_income=30_000)])
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        assembler,
        validation={
            "valid": False,
            "errors": [{"path": "people[0].age", "message": "Age is unsupported"}],
        },
    )

    outcome = _invoke(
        composition,
        context,
        {"description": "A 35-year-old earning £30,000 with no rent or Council Tax"},
    )

    assert isinstance(outcome, NeedsInput)
    assert outcome.prompt.startswith("Age is unsupported")
    assert "current policy" not in outcome.prompt
    assert "assum" not in outcome.prompt.casefold()
    assert len(artifacts.waiting) == 1
    assert "run_household_simulation" not in [
        identifier for identifier, _payload, _context in calls
    ]


def test_explicit_absences_are_not_misreported_as_defaults(monkeypatch):
    evidence = _evidence().model_copy(
        update={
            "evidence": _evidence().evidence.model_copy(
                update={
                    "has_children": False,
                    "is_married": False,
                    "sources": {
                        "has_children": "user",
                        "is_married": "user",
                    },
                }
            )
        }
    )
    composition, context, _artifacts, _calls = _runtime(
        monkeypatch,
        FakeAssembler([evidence]),
    )

    outcome = _invoke(
        composition,
        context,
        {
            "description": (
                "A single 35-year-old with no children, rent, or Council Tax"
            )
        },
    )

    statements = {item.plain_statement for item in outcome.value.assumptions}
    assert "The household has no children." not in statements
    assert "The household has no partner or spouse." not in statements


def test_reported_assumption_can_be_corrected_and_recalculated(monkeypatch):
    corrected_evidence = _evidence().model_copy(
        update={
            "evidence": _evidence().evidence.model_copy(
                update={
                    "childcare_expenses": PeriodicAmount(
                        amount=2_400,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    "sources": {"childcare_expenses": "user"},
                }
            )
        }
    )
    assembler = FakeAssembler([_evidence(), corrected_evidence])
    composition, context, _artifacts, calls = _runtime(monkeypatch, assembler)

    first = _invoke(
        composition,
        context,
        {"description": "A single 35-year-old adult with no rent or Council Tax"},
    )
    household_id = first.value.result.household_artifact_id
    second = _invoke(
        composition,
        context,
        {
            "description": "Correction: childcare costs are £2,400 per year",
            "referenced_household_id": household_id,
        },
    )

    assert assembler.calls[1]["description"] == (
        "Correction: childcare costs are £2,400 per year"
    )
    assert assembler.calls[1]["retained_ambiguities"] == ()
    assert assembler.calls[1]["retained_evidence"].people == ()
    statements = {item.plain_statement for item in second.value.assumptions}
    assert "The household has no childcare expenses." not in statements
    second_validation = [
        payload
        for identifier, payload, _context in calls
        if identifier == "validate_household"
    ][-1]
    assert second_validation["people"][0]["age"] == 35
    assert [identifier for identifier, _payload, _context in calls].count(
        "run_household_simulation"
    ) == 2


def test_periodic_amounts_are_annualized_and_childcare_uses_person_entity(
    monkeypatch,
):
    evidence = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(
                    age=35,
                    employment_income=PeriodicAmount(
                        amount=1_000,
                        frequency=AmountFrequency.MONTHLY,
                    ),
                    sources={"age": "user", "employment_income": "user"},
                ),
            ),
            childcare_expenses=PeriodicAmount(
                amount=100,
                frequency=AmountFrequency.WEEKLY,
            ),
            rent=PeriodicAmount(
                amount=1_000,
                frequency=AmountFrequency.MONTHLY,
            ),
            council_tax=PeriodicAmount(
                amount=100,
                frequency=AmountFrequency.MONTHLY,
            ),
            sources={
                "childcare_expenses": "user",
                "rent": "user",
                "council_tax": "user",
            },
        )
    )
    composition, context, _artifacts, calls = _runtime(
        monkeypatch,
        FakeAssembler([evidence]),
    )

    outcome = _invoke(
        composition,
        context,
        {"description": "One adult with monthly income and rent"},
    )

    assert isinstance(outcome, Completed)
    validation_input = next(
        payload
        for identifier, payload, _context in calls
        if identifier == "validate_household"
    )
    assert validation_input["people"][0]["employment_income"] == 12_000
    assert validation_input["people"][0]["childcare_expenses"] == 5_200
    assert "childcare_expenses" not in validation_input["benunit"]
    assert validation_input["household"]["rent"] == 12_000
    assert validation_input["household"]["council_tax"] == 1_200


def test_resolver_returns_all_current_relationship_and_income_questions():
    resolution = HouseholdInputResolver().resolve(
        HouseholdEvidence(
            people=(
                PersonEvidence(
                    age=40,
                    employment_income=PeriodicAmount(
                        amount=50_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={
                        "age": "user",
                        "employment_income": "user",
                    },
                ),
                PersonEvidence(age=38, sources={"age": "user"}),
            )
        ),
        (
            HouseholdEvidenceAmbiguity(
                kind=HouseholdEvidenceAmbiguityKind.INCOME_OWNER,
                field="employment_income",
            ),
            HouseholdEvidenceAmbiguity(
                kind=HouseholdEvidenceAmbiguityKind.INCOME_FREQUENCY,
                field="employment_income",
            ),
        ),
    )

    assert resolution.missing_fields == (
        "benunit.is_married",
        "income.owner",
        "income.frequency",
        "household.rent",
        "household.council_tax",
    )
    assert resolution.questions == (
        "Do the adults form one couple or civil partnership?",
        "Which person receives each income amount you gave?",
        "For each income amount, is it weekly, monthly, or annual?",
        (
            "Does the household pay rent or Council Tax? For each that applies, "
            "please give the amount and say whether it is weekly, monthly, or "
            "annual; otherwise say that it does not apply."
        ),
    )
    assert not hasattr(resolution, "proposed_default_statements")


def test_pending_fact_resolution_prevents_defaults_validation_and_simulation(
    monkeypatch,
):
    assembler = FakeAssembler([_evidence(age=23, employment_income=50_000)])
    composition, context, _artifacts, calls = _runtime(monkeypatch, assembler)
    proposal = PendingFactResolution(
        proposal_id="proposal-1",
        claim_id="claim-1",
        source_turn_id="turn-1",
        scope_id="scope:primary-household",
        referenced_entity_ids=("person:self",),
        evidence="Our combined income is £70,000.",
        status=FactResolutionStatus.AWAITING_CONFIRMATION,
        prompt="Should I use £20,000 for your spouse?",
        variable_name="employment_income",
        variable_entity="person",
        variable_label="Employment income",
        definition_period="year",
        mapping_confidence="high",
        relationship=FactClaimRelationship.SUM,
        expected_total=Decimal("70000"),
        period=MoneyPeriod.ANNUAL,
        assignments=(
            FactResolutionAssignment(
                definition_key="person.employment_income",
                subject_entity_id="person:self",
                scope_id="scope:primary-household",
                assertion=PresentAssertion(
                    value=MoneyFactValue(
                        amount=Decimal("20000"),
                        period=MoneyPeriod.ANNUAL,
                    )
                ),
            ),
        ),
        created_revision=0,
    )
    conversation_context = ConversationContext.initial(
        "conversation-1"
    ).model_copy(update={"pending_fact_resolutions": (proposal,)})
    context = context.with_conversation_context(conversation_context)

    outcome = _invoke(
        composition,
        context,
        {"description": "Calculate our income tax."},
    )

    assert isinstance(outcome, NeedsInput)
    assert outcome.prompt == proposal.prompt
    assert assembler.calls == []
    assert not any(
        identifier in {"validate_household", "run_household_simulation"}
        for identifier, _payload, _context in calls
    )


def test_catalogue_backed_context_fact_reaches_household_engine_input(monkeypatch):
    registry = build_default_fact_registry()
    definition = registry.ensure_engine_definition(
        variable_name="employee_pension_contributions",
        entity="person",
        label="Employee pension contributions",
        value_kind=FactValueKind.MONEY,
    )
    reducer = ContextReducer(registry)
    conversation_context = reducer.reduce(
        ConversationContext.initial("conversation-1"),
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
    assembler = FakeAssembler([_evidence(age=35, employment_income=50_000)])
    composition, context, _artifacts, calls = _runtime(
        monkeypatch,
        assembler,
        household_capability=HouseholdAnalysisCapability(
            HouseholdEngineFactProjector(registry)
        ),
    )
    context = context.with_conversation_context(conversation_context)

    outcome = _invoke(
        composition,
        context,
        {
            "description": "Calculate my income tax.",
            "requested_outputs": ["income_tax"],
        },
    )

    assert isinstance(outcome, Completed)
    engine_calls = [
        payload
        for identifier, payload, _context in calls
        if identifier in {"validate_household", "run_household_simulation"}
    ]
    assert len(engine_calls) == 2
    assert all(
        payload["people"][0]["employee_pension_contributions"] == 1200.0
        for payload in engine_calls
    )


def test_clarification_asks_consequential_fields_and_typed_draft_resumes(monkeypatch):
    first_evidence = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(
                    display_label="you",
                    relationship_to_user="self",
                    employment_income=PeriodicAmount(
                        amount=50_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={"employment_income": "user"},
                ),
                PersonEvidence(
                    display_label="your spouse",
                    relationship_to_user="spouse",
                ),
            )
        )
    )
    answer_evidence = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(age=40, sources={"age": "user"}),
                PersonEvidence(age=38, sources={"age": "user"}),
            ),
            is_married=True,
            rent=PeriodicAmount(amount=0, frequency=AmountFrequency.ANNUAL),
            council_tax=PeriodicAmount(
                amount=0,
                frequency=AmountFrequency.ANNUAL,
            ),
            sources={
                "is_married": "user",
                "rent": "user",
                "council_tax": "user",
            },
        )
    )
    assembler = FakeAssembler([first_evidence, answer_evidence])
    composition, context, artifacts, calls = _runtime(monkeypatch, assembler)

    first = _invoke(
        composition,
        context,
        {"description": "Two adults; one earns £50,000 per year."},
    )

    assert isinstance(first, NeedsInput)
    assert "ages should I use for you and your spouse" in first.prompt
    assert "whether the adults form one couple" in first.prompt
    assert "pay rent or Council Tax" in first.prompt
    assert "2026" not in first.prompt
    assert "England" not in first.prompt
    assert "assum" not in first.prompt.casefold()
    assert len(artifacts.waiting) == 1
    retained = artifacts.waiting[0].partial_input
    assert retained.evidence.people[0].employment_income.amount == 50_000

    second = _invoke(
        composition,
        context,
        {
            "description": (
                "They are 40 and 38, they are a couple, and they pay no rent or "
                "Council Tax."
            ),
        },
    )

    assert isinstance(second, Completed)
    assert artifacts.waiting == []
    validation_input = [
        payload
        for identifier, payload, _context in calls
        if identifier == "validate_household"
    ][-1]
    assert validation_input["people"][0]["age"] == 40
    assert validation_input["people"][1]["age"] == 38
    assert validation_input["people"][0]["employment_income"] == 50_000
    assert validation_input["benunit"]["is_married"] is True


def test_resumed_household_merges_new_typed_age_with_retained_input(monkeypatch):
    first_evidence = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(
                    age=35,
                    employment_income=PeriodicAmount(
                        amount=50_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={"age": "user", "employment_income": "user"},
                ),
                PersonEvidence(
                    employment_income=PeriodicAmount(
                        amount=20_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={"employment_income": "user"},
                ),
            ),
            is_married=True,
            sources={"is_married": "user"},
        )
    )
    targeted_answer = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(),
                PersonEvidence(age=33, sources={"age": "user"}),
            )
        )
    )
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        FakeAssembler([first_evidence, targeted_answer]),
        simulation={
            "status": "success",
            "year": 2026,
            "reform_applied": False,
            "income_tax": 10_000,
        },
    )

    first = _invoke(
        composition,
        context.with_current_user_message(
            "I am 35, my spouse earns £20,000 annually, and I earn £50,000 annually."
        ),
        {
            "description": "A married couple with annual incomes.",
            "requested_outputs": ["income tax"],
        },
    )

    assert isinstance(first, NeedsInput)
    assert first.missing_fields == ("people[1].age",)
    assert artifacts.waiting[0].partial_input.pending_fields == (
        "people[1].age",
    )

    second = _invoke(
        composition,
        context.with_current_user_message("I SAID 33"),
        {
            "description": "The user says the missing age is 33.",
        },
    )

    assert isinstance(second, Completed)
    validation_input = [
        payload
        for identifier, payload, _context in calls
        if identifier == "validate_household"
    ][-1]
    assert validation_input["people"][0]["age"] == 35
    assert validation_input["people"][1]["age"] == 33


def test_typed_spouse_age_remains_attached_to_its_person(monkeypatch):
    evidence = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(
                    age=35,
                    employment_income=PeriodicAmount(
                        amount=50_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={"age": "user", "employment_income": "user"},
                ),
                PersonEvidence(
                    age=33,
                    employment_income=PeriodicAmount(
                        amount=20_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={"age": "user", "employment_income": "user"},
                ),
            ),
            is_married=True,
            sources={"is_married": "user"},
        )
    )
    composition, context, artifacts, _calls = _runtime(
        monkeypatch,
        FakeAssembler([evidence]),
    )

    outcome = _invoke(
        composition,
        context.with_current_user_message(
            "I am 35, my spouse is 33, I make £50,000 annually, and my partner "
            "makes £20,000 annually."
        ),
        {
            "description": "A married couple with annual incomes.",
            "requested_outputs": ["income tax"],
        },
    )

    assert isinstance(outcome, Completed)
    household = next(
        artifact
        for artifact in artifacts.artifacts
        if artifact.artifact_type == "household"
    )
    values = {value.field_id: value.value for value in household.values}
    assert values["people[0].age"] == 35
    assert values["people[1].age"] == 33


def test_unresolved_income_candidate_persists_until_later_binding(monkeypatch):
    ambiguous = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(
                    age=40,
                    employment_income=PeriodicAmount(
                        amount=50_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={
                        "age": "user",
                        "employment_income": "user",
                    },
                ),
                PersonEvidence(age=38, sources={"age": "user"}),
            ),
            is_married=True,
            rent=PeriodicAmount(amount=0, frequency=AmountFrequency.ANNUAL),
            council_tax=PeriodicAmount(
                amount=0,
                frequency=AmountFrequency.ANNUAL,
            ),
            sources={
                "is_married": "user",
                "rent": "user",
                "council_tax": "user",
            },
        ),
        ambiguities=(
            HouseholdEvidenceAmbiguity(
                kind=HouseholdEvidenceAmbiguityKind.INCOME_OWNER,
                field="employment_income",
                amount=50_000,
                frequency=AmountFrequency.ANNUAL,
            ),
        ),
    )
    bound = HouseholdEvidenceResult(
        evidence=HouseholdEvidence(
            people=(
                PersonEvidence(
                    employment_income=PeriodicAmount(
                        amount=50_000,
                        frequency=AmountFrequency.ANNUAL,
                    ),
                    sources={"employment_income": "user"},
                ),
            )
        )
    )
    composition, context, artifacts, calls = _runtime(
        monkeypatch,
        FakeAssembler([ambiguous, bound]),
    )

    first = _invoke(
        composition,
        context,
        {
            "description": (
                "We are 40 and 38; one earns GBP 50,000 annually; we pay no rent "
                "or Council Tax."
            )
        },
    )

    assert isinstance(first, NeedsInput)
    assert "Which person receives each income amount" in first.prompt
    retained_ambiguity = artifacts.waiting[0].partial_input.ambiguities[0]
    assert retained_ambiguity.amount == 50_000
    assert retained_ambiguity.frequency is AmountFrequency.ANNUAL
    assert (
        artifacts.waiting[0].partial_input.evidence.people[0].employment_income
        is None
    )

    second = _invoke(
        composition,
        context,
        {
            "description": "The first adult receives it.",
        },
    )

    assert isinstance(second, Completed)
    assert artifacts.waiting == []
    validation_input = [
        payload
        for identifier, payload, _context in calls
        if identifier == "validate_household"
    ][-1]
    assert validation_input["people"][0]["employment_income"] == 50_000


def test_explicit_new_household_request_does_not_consume_pending_draft(monkeypatch):
    assembler = FakeAssembler([_evidence(age=None), _evidence(age=52)])
    composition, context, artifacts, _calls = _runtime(monkeypatch, assembler)

    pending = _invoke(
        composition,
        context,
        {"description": "Calculate benefits for an adult whose age I do not know."},
    )
    separate = _invoke(
        composition,
        context,
        {
            "description": "Separately, calculate a single 52-year-old adult.",
            "start_new_invocation": True,
        },
    )

    assert isinstance(pending, NeedsInput)
    assert isinstance(separate, Completed)
    assert len(artifacts.waiting) == 1
    assert "resuming_invocation_id" not in pending.partial_input
