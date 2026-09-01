from __future__ import annotations

import asyncio

from capabilities.artifacts import (
    ArtifactProvenance,
    PolicyScenarioRef,
)
from capabilities.composition import compose_runtime
from capabilities.contracts import Completed, Failed, NeedsInput
from capabilities.input_resolution import InputSource
from capabilities.policy_information import (
    PolicyInformationCapability,
)
from capabilities.policy_reform import (
    AnthropicReformCandidateResolver,
    PolicyReformCapability,
    ReformMeaning,
    ReformResolutionDecision,
    ReformResolutionKind,
    ResolveReformTool,
)
from tools.contracts import CallerType, Visibility
from tools.typed_dispatch import build_dispatch_tools


class MemoryArtifacts:
    def __init__(self, artifacts=()):
        self.artifacts = list(artifacts)
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


class FakeResolver:
    def __init__(self, decisions, corrections=()):
        self.decisions = list(decisions)
        self.corrections = list(corrections)
        self.resolve_calls = []
        self.correction_calls = []

    async def resolve(self, **kwargs):
        self.resolve_calls.append(kwargs)
        return self.decisions.pop(0)

    async def correct_representation(self, **kwargs):
        self.correction_calls.append(kwargs)
        return self.corrections.pop(0)


def _resolved(path="gov.example.amount", value=15_000):
    return ReformResolutionDecision(
        outcome=ReformResolutionKind.RESOLVED,
        summary="Set Example amount to £15,000.",
        reform={path: value},
        meaning=ReformMeaning(
            parameter_path=path,
            operation="set",
            value=value,
            unit="currency-GBP",
            effective_date="2026-01-01",
            population="all eligible UK households",
            jurisdiction="United Kingdom",
        ),
        usage={"input_tokens": 7, "output_tokens": 3},
    )


def _runtime(monkeypatch, resolver, *, artifacts=None, validate_results=None):
    from tools import typed_dispatch

    calls = []
    validation_queue = list(
        validate_results
        or [
            {
                "valid": True,
                "normalized_reform": {"gov.example.amount": 15_000},
                "parameter_paths": ["gov.example.amount"],
            }
        ]
    )

    def execute(identifier, payload, context=None):
        del context
        calls.append((identifier, payload))
        if identifier == "search_parameters":
            if "universal credit" not in payload["query"].casefold():
                return {"status": "success", "parameters": []}
            return {
                "status": "success",
                "parameters": [
                    {
                        "path": "gov.dwp.universal_credit.amount",
                        "label": "Universal Credit amount",
                        "description": "The amount used by the calculation.",
                        "unit": "currency-GBP",
                    }
                ],
            }
        if identifier == "search_variables":
            if "universal credit" not in payload["query"].casefold():
                return {"status": "success", "variables": []}
            return {
                "status": "success",
                "variables": [
                    {
                        "name": "universal_credit",
                        "label": "Universal Credit",
                        "description": "Calculated Universal Credit entitlement.",
                        "entity": "benunit",
                        "definition_period": "month",
                        "unit": "currency-GBP",
                        "quantity_type": "flow",
                        "reference": ["Example regulations"],
                        "defined_for": "is_uc_eligible",
                        "min_value": 0,
                        "max_value": None,
                        "is_period_size_independent": False,
                        "metadata": {"category": "benefit"},
                        "default_value": 0,
                    }
                ],
            }
        if identifier == "get_parameter":
            return {
                "status": "success",
                "parameter": {
                    "path": payload["path"],
                    "label": "Example amount",
                    "description": "Example policy amount.",
                    "unit": "currency-GBP",
                    "year": payload["year"],
                    "value": 10_000,
                },
            }
        if identifier == "get_variable":
            return {
                "status": "success",
                "variable": {
                    "name": payload["name"],
                    "label": "Universal Credit",
                    "description": "Calculated entitlement.",
                    "entity": "benunit",
                    "definition_period": "month",
                    "unit": "currency-GBP",
                    "quantity_type": "flow",
                    "reference": ["Example regulations"],
                    "defined_for": "is_uc_eligible",
                    "min_value": 0,
                    "max_value": None,
                    "is_period_size_independent": False,
                    "metadata": {"category": "benefit"},
                    "default_value": 0,
                },
            }
        if identifier == "list_reform_targets":
            return {
                "status": "success",
                "targets": [
                    {
                        "path": "gov.example.amount",
                        "label": "Example amount",
                        "description": "Example policy amount.",
                    }
                ],
            }
        if identifier == "validate_reform":
            return validation_queue.pop(0)
        raise AssertionError(f"Unexpected tool: {identifier}")

    monkeypatch.setattr(typed_dispatch, "execute_tool", execute)
    tools = [*build_dispatch_tools(), ResolveReformTool(resolver)]
    composition = compose_runtime(
        tools=tools,
        capabilities=[
            PolicyInformationCapability(),
            PolicyReformCapability(),
        ],
    )
    artifact_access = artifacts or MemoryArtifacts()

    async def not_cancelled():
        return False

    context = composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=not_cancelled,
        artifacts=artifact_access,
    )
    return composition, context, artifact_access, calls


def _invoke(composition, context, identifier, payload):
    return asyncio.run(
        composition.executor.invoke_capability(
            identifier,
            payload,
            caller=CallerType.MODEL,
            context=context,
        )
    )


def test_reform_resolution_schema_constrains_the_semantic_parameter_path():
    schema = AnthropicReformCandidateResolver._resolution_schema(
        (
            {"path": "gov.example.amount", "label": "Example amount"},
            {"path": "gov.example.other", "label": "Other amount"},
        )
    )

    meaning = schema["$defs"]["ReformMeaning"]
    properties = meaning["properties"]
    assert "target" not in properties
    assert properties["parameter_path"]["enum"] == [
        "gov.example.amount",
        "gov.example.other",
    ]


def test_policy_information_uses_ordinary_language_and_fixed_year(monkeypatch):
    resolver = FakeResolver([])
    composition, context, _artifacts, calls = _runtime(monkeypatch, resolver)

    outcome = _invoke(
        composition,
        context,
        "policy_information",
        {
            "question": "How is Universal Credit calculated for a household?",
        },
    )

    assert isinstance(outcome, Completed)
    assert outcome.value.year == 2026
    assert outcome.value.year_source is InputSource.SERVER_DEFAULT
    assert {fact.identifier for fact in outcome.value.facts} == {
        "gov.dwp.universal_credit.amount",
        "universal_credit",
    }
    variable = next(
        fact for fact in outcome.value.facts if fact.identifier == "universal_credit"
    )
    assert variable.definition_period == "month"
    assert variable.unit == "currency-GBP"
    assert variable.quantity_type == "flow"
    assert variable.reference == ["Example regulations"]
    assert variable.defined_for == "is_uc_eligible"
    assert variable.min_value == 0
    assert variable.max_value is None
    assert variable.is_period_size_independent is False
    assert variable.metadata == {"category": "benefit"}
    assert all(
        identifier not in {"run_household_simulation", "run_society_simulation"}
        for identifier, _payload in calls
    )


def test_policy_information_year_precedence_uses_explicit_then_referenced(monkeypatch):
    scenario = PolicyScenarioRef(
        artifact_id="scenario-2025",
        provenance=ArtifactProvenance(
            conversation_id="conversation-1",
            turn_id="older-turn",
            capability_id="policy_reform",
            capability_version="1",
            invocation_id="older-invocation",
        ),
        year=2025,
        scenario_revision="revision-1",
        catalogue_version="catalogue-1",
        calculation_engine_version="engine-1",
        baseline=True,
    )
    composition, context, _artifacts, _calls = _runtime(
        monkeypatch,
        FakeResolver([]),
        artifacts=MemoryArtifacts([scenario]),
    )

    referenced = _invoke(
        composition,
        context,
        "policy_information",
        {
            "question": "Universal Credit",
            "referenced_policy_scenario_id": "scenario-2025",
        },
    )
    explicit = _invoke(
        composition,
        context,
        "policy_information",
        {
            "question": "Universal Credit",
            "year": 2024,
            "referenced_policy_scenario_id": "scenario-2025",
        },
    )

    assert referenced.value.year == 2025
    assert referenced.value.year_source is InputSource.REFERENCED_ARTIFACT
    assert explicit.value.year == 2024
    assert explicit.value.year_source is InputSource.CURRENT_REQUEST


def test_resolved_reform_is_validated_and_only_then_persisted(monkeypatch):
    resolver = FakeResolver([_resolved()])
    composition, context, artifacts, calls = _runtime(monkeypatch, resolver)

    outcome = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "Set the Example amount to £15,000"},
    )

    assert isinstance(outcome, Completed)
    assert outcome.value.scenario.year == 2026
    assert outcome.value.year_source is InputSource.SERVER_DEFAULT
    assert outcome.value.scenario.verified_changes[0].parameter_path == (
        "gov.example.amount"
    )
    assert artifacts.artifacts == [outcome.value.scenario]
    assert [identifier for identifier, _payload in calls].count("validate_reform") == 1
    assert context.model_usage.snapshot().input_tokens == 7
    records = composition.tracer.records("conversation-1", include_private=True)
    validate_record = next(
        record for record in records if record.identifier == "validate_reform"
    )
    assert validate_record.visibility is Visibility.PRIVATE


def test_final_value_reform_envelope_is_normalized_to_catalogue_mapping(monkeypatch):
    decision = _resolved().model_copy(
        update={
            "reform": {
                "path": "gov.example.amount",
                "operation": "increase",
                "from": 10_000,
                "to": 15_000,
                "unit": "currency-GBP",
                "year": 2026,
            }
        }
    )
    resolver = FakeResolver([decision])
    composition, context, _artifacts, calls = _runtime(monkeypatch, resolver)

    outcome = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "Increase the Example amount to £15,000"},
    )

    assert isinstance(outcome, Completed)
    validation_payload = next(
        payload for identifier, payload in calls if identifier == "validate_reform"
    )
    assert validation_payload["reform"] == {"gov.example.amount": 15_000}
    assert outcome.value.scenario.verified_changes[0].value == 15_000


def test_inconsistent_parameter_mapping_is_repaired_without_user_clarification(
    monkeypatch,
):
    meaning = _resolved().meaning.model_copy(
        update={"operation": "increase", "value": 5_000}
    )
    inconsistent = _resolved().model_copy(
        update={
            "reform": {"gov.example.other": 15_000},
            "meaning": meaning,
        }
    )
    corrected = inconsistent.model_copy(
        update={"reform": {"gov.example.amount": 15_000}}
    )
    resolver = FakeResolver([inconsistent], corrections=[corrected])
    composition, context, artifacts, _calls = _runtime(monkeypatch, resolver)

    outcome = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "Increase the Example amount by £5,000"},
    )

    assert isinstance(outcome, Completed)
    assert len(resolver.correction_calls) == 1
    assert outcome.value.scenario.verified_changes[0].parameter_path == (
        "gov.example.amount"
    )
    assert artifacts.waiting == []


def test_unknown_catalogue_path_and_missing_magnitude_never_create_scenarios(
    monkeypatch,
):
    unknown = _resolved(path="invented.path")
    resolver = FakeResolver([unknown])
    composition, context, artifacts, calls = _runtime(monkeypatch, resolver)

    outcome = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "Set an invented amount to £15,000"},
    )

    assert isinstance(outcome, Failed)
    assert artifacts.artifacts == []
    assert "validate_reform" not in [identifier for identifier, _payload in calls]

    clarification_resolver = FakeResolver(
        [
            ReformResolutionDecision(
                outcome=ReformResolutionKind.NEEDS_CLARIFICATION,
                summary="The requested increase has no amount.",
                clarification="How much should the amount increase by?",
            )
        ]
    )
    composition, context, artifacts, _calls = _runtime(
        monkeypatch,
        clarification_resolver,
    )
    clarification = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "Increase the Example amount"},
    )
    assert isinstance(clarification, NeedsInput)
    assert "How much" in clarification.prompt
    assert artifacts.artifacts == []


def test_representation_correction_runs_at_most_once_and_cannot_change_meaning(
    monkeypatch,
):
    first = _resolved()
    corrected = _resolved()
    resolver = FakeResolver([first], corrections=[corrected])
    invalid_shape = {
        "valid": False,
        "errors": [{"message": "Reform value has invalid type shape"}],
    }
    composition, context, _artifacts, _calls = _runtime(
        monkeypatch,
        resolver,
        validate_results=[
            invalid_shape,
            {"valid": False, "errors": [{"message": "still invalid type shape"}]},
        ],
    )

    outcome = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "Set the Example amount to £15,000"},
    )

    assert isinstance(outcome, Failed)
    assert len(resolver.correction_calls) == 1

    changed = _resolved(value=16_000)
    changed = changed.model_copy(
        update={
            "reform": {"gov.example.amount": 16_000},
            "meaning": changed.meaning.model_copy(update={"value": 16_000}),
        }
    )
    resolver = FakeResolver([first], corrections=[changed])
    composition, context, artifacts, _calls = _runtime(
        monkeypatch,
        resolver,
        validate_results=[invalid_shape],
    )
    outcome = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "Set the Example amount to £15,000"},
    )
    assert isinstance(outcome, Failed)
    assert artifacts.artifacts == []


def test_current_law_baseline_skips_model_resolution_and_uses_2026(monkeypatch):
    resolver = FakeResolver([])
    composition, context, artifacts, calls = _runtime(monkeypatch, resolver)

    outcome = _invoke(
        composition,
        context,
        "policy_reform",
        {"instruction": "current law"},
    )

    assert isinstance(outcome, Completed)
    assert outcome.value.scenario.baseline is True
    assert outcome.value.scenario.year == 2026
    assert resolver.resolve_calls == []
    assert calls == []
    assert artifacts.artifacts == [outcome.value.scenario]
