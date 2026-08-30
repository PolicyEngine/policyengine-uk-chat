"""Compatibility checks for the Anthropic SDK calls used by UK Chat."""

import asyncio
import inspect
import json
from types import SimpleNamespace

import pytest

import chat.model_port as model_port
import conversation_context.tools as context_tools
import conversation_context.variable_resolution as variable_resolution
from chat.model_port import AnthropicConversationModel
from config.clients import get_async_client, get_sync_client
from conversation_context.models import (
    ClaimedMoneyValue,
    ContextEntityCandidate,
    ContextPatch,
    ConversationContext,
    EntityKind,
    EnsureEntityOperation,
    FactClaim,
    FactClaimRelationship,
    MoneyPeriod,
    TextFactValue,
)
from conversation_context.projection import project_context
from conversation_context.reducer import ContextReducer
from conversation_context.registry import build_default_fact_registry
from conversation_context.change_pipeline import (
    ContextChangeProposal,
    ContextChangeValidator,
    ContextValidationIssue,
    ContextValidationStatus,
    ValidateContextChangeInput,
)
from conversation_context.tools import (
    AnthropicContextProposalReviewer,
    AnthropicContextInterpreter,
    ContextProposalStatus,
    ProposeContextChangeInput,
    ContextConversationExcerpt,
)
from conversation_context.variable_resolution import (
    AnthropicVariableMapper,
    MappingConfidence,
    MappingStatus,
    PolicyEngineVariableCandidate,
)


def context_with_spouse(*, revision: int = 2) -> ConversationContext:
    registry = build_default_fact_registry()
    context = ContextReducer(registry).reduce(
        ConversationContext.initial("conversation"),
        ContextPatch(
            expected_revision=0,
            operations=(
                EnsureEntityOperation(
                    reference="entity:spouse",
                    kind=EntityKind.PERSON,
                    aliases=("spouse",),
                    relationship_to_user="spouse",
                ),
            ),
        ),
        turn_id="turn-spouse",
        evidence="I have a spouse.",
    ).context
    return context.model_copy(update={"revision": revision})


def _usage(*, input_tokens: int = 3, output_tokens: int = 2):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )


def _request(
    message: str,
    *,
    context: ConversationContext | None = None,
) -> ProposeContextChangeInput:
    resolved_context = context or ConversationContext.initial("conversation")
    return ProposeContextChangeInput(
        current_message=message,
        conversation=(ContextConversationExcerpt(role="user", content=message),),
        context=project_context(resolved_context),
        fact_definitions=build_default_fact_registry().definitions(),
    )


def test_anthropic_sdk_accepts_configured_sampling_parameter(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    sync_parameters = inspect.signature(get_sync_client().messages.create).parameters
    async_parameters = inspect.signature(get_async_client().messages.stream).parameters

    assert "temperature" in sync_parameters
    assert "temperature" in async_parameters


def test_numerical_redraft_removes_unverified_derived_values(monkeypatch):
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text="Verified values only.")],
                stop_reason="end_turn",
                usage=_usage(input_tokens=1, output_tokens=1),
            )

    monkeypatch.setattr(
        model_port,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )

    response = asyncio.run(
        AnthropicConversationModel(model="test-model").redraft_numerical(
            draft="Tax is £7,486 and inferred take-home pay is £42,514.",
            unsupported_claims=("£42,514",),
            fact_summary="Income Tax: 7486 GBP/year",
        )
    )

    assert response.text == "Verified values only."
    system = calls[0]["system"]
    assert "Do not calculate a new total, difference, rate" in system
    assert "do not repeat any expression listed as unsupported" in system


def test_context_interpreter_uses_one_declarative_fact_claim_route(monkeypatch):
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_context_change",
                        input={
                            "expected_revision": 0,
                            "changes": [
                                {
                                    "kind": "fact_claim",
                                    "concept": "age",
                                    "definition_key": "person.age",
                                    "subject_references": ["person:self"],
                                    "relationship": "direct",
                                    "value": {"kind": "integer", "value": 42},
                                    "scope_id": "scope:primary-household",
                                    "evidence": "I am 42",
                                }
                            ],
                        },
                    )
                ],
                usage=_usage(),
            )

    monkeypatch.setattr(
        context_tools,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    result = asyncio.run(AnthropicContextInterpreter().propose(_request("I am 42")))

    assert result.claims[0].definition_key == "person.age"
    assert result.claims[0].subject_references == ("person:self",)
    assert result.usage.input_tokens == 3
    assert calls[0]["tool_choice"] == {
        "type": "tool",
        "name": "submit_context_change",
    }
    schema = calls[0]["tools"][0]["input_schema"]
    schema_text = json.dumps(schema)
    assert "changes" in schema_text
    assert "candidate_entities" in schema_text
    assert "operations" not in schema["properties"]
    assert "unresolved_claims" not in schema_text
    assert "ContextPatch" not in schema_text
    system = calls[0]["system"].casefold()
    assert "every supported assertion exactly once" in system
    assert "do not infer an income source" in system
    assert "apply a default" in system
    assert '"current_message":"I am 42"' in calls[0]["messages"][0]["content"]


def test_context_proposal_reviewer_uses_exact_claim_ids_without_retained_facts(
    monkeypatch,
):
    calls = []
    claim = FactClaim(
        claim_id="opaque-claim-42",
        concept="age",
        definition_key="person.age",
        subject_references=("person:self",),
        value={"kind": "integer", "value": 42},
        evidence="I am 42",
    )

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_context_semantic_review",
                        input={
                            "reviews": [
                                {
                                    "claim_id": "opaque-claim-42",
                                    "supported": True,
                                    "reason": "The message directly states the age.",
                                    "evidence": "I am 42",
                                }
                            ]
                        },
                    )
                ],
                usage=_usage(),
            )

    monkeypatch.setattr(
        context_tools,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    request = ValidateContextChangeInput(
        context=ConversationContext.initial("conversation"),
        proposal=ContextChangeProposal(
            expected_revision=0,
            changes=(claim,),
        ),
        turn_id="turn",
        evidence="I am 42",
    )

    result = asyncio.run(AnthropicContextProposalReviewer().review(request))

    assert result.reviews[0].claim_id == "opaque-claim-42"
    assert result.reviews[0].supported is True
    payload = json.loads(calls[0]["messages"][0]["content"])
    assert set(payload) == {
        "current_message",
        "known_entities",
        "active_scope_id",
        "proposal",
    }
    assert "facts" not in json.dumps(payload["known_entities"])
    assert calls[0]["tool_choice"] == {
        "type": "tool",
        "name": "submit_context_semantic_review",
    }


def test_context_interpreter_returns_additive_and_direct_values_in_same_claim_list(
    monkeypatch,
):
    class FakeMessages:
        async def create(self, **_kwargs):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_context_change",
                        input={
                            "expected_revision": 2,
                            "changes": [
                                {
                                    "kind": "fact_claim",
                                    "concept": "age",
                                    "definition_key": "person.age",
                                    "subject_references": ["person:self"],
                                    "relationship": "direct",
                                    "value": {"kind": "integer", "value": 26},
                                    "scope_id": "scope:primary-household",
                                    "evidence": "I am 26.",
                                },
                                {
                                    "kind": "fact_claim",
                                    "concept": "employment income",
                                    "subject_references": [
                                        "person:self",
                                        "entity:spouse",
                                    ],
                                    "relationship": "sum",
                                    "value": {
                                        "kind": "money",
                                        "amount": "70000",
                                        "period": "annual",
                                        "currency": "GBP",
                                    },
                                    "evidence": "We earn £70,000 together.",
                                }
                            ],
                        },
                    )
                ],
                usage=_usage(),
            )

    monkeypatch.setattr(
        context_tools,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    result = asyncio.run(
        AnthropicContextInterpreter().propose(
            _request(
                "I am 26. We earn £70,000 together.",
                context=context_with_spouse(),
            )
        )
    )

    assert result.expected_revision == 2
    assert len(result.claims) == 2
    assert result.claims[0].relationship is FactClaimRelationship.DIRECT
    assert result.claims[0].definition_key == "person.age"
    assert result.claims[1].relationship is FactClaimRelationship.SUM
    assert isinstance(result.claims[1].value, ClaimedMoneyValue)
    assert result.claims[1].value.amount == 70000


def test_context_interpreter_repairs_invalid_relationship_cardinality(monkeypatch):
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            subjects = ["person:self"]
            if len(calls) == 2:
                subjects.append("entity:spouse")
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_context_change",
                        input={
                            "expected_revision": 2,
                            "changes": [
                                {
                                    "kind": "fact_claim",
                                    "concept": "employment income",
                                    "subject_references": subjects,
                                    "relationship": "sum",
                                    "value": {
                                        "kind": "money",
                                        "amount": "70000",
                                        "period": "annual",
                                    },
                                    "evidence": "We earn £70,000 together.",
                                }
                            ],
                        },
                    )
                ],
                usage=_usage(),
            )

    monkeypatch.setattr(
        context_tools,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    result = asyncio.run(
        AnthropicContextInterpreter().propose(
            _request("We earn £70,000 together.", context=context_with_spouse())
        )
    )

    assert len(calls) == 2
    assert result.claims[0].subject_references == ("person:self", "entity:spouse")
    repair_content = calls[1]["messages"][0]["content"]
    assert '"code": "invalid_context_submission"' in repair_content
    assert '"changes", "0"' in repair_content
    assert "do not ask the user a question" in repair_content


def test_context_validator_rejects_a_missing_monetary_claim_without_phrase_rules():
    context = context_with_spouse()
    validator = ContextChangeValidator(
        ContextReducer(build_default_fact_registry()),
        build_default_fact_registry(),
    )

    result = validator.validate(
        ValidateContextChangeInput(
            context=context,
            proposal=ContextChangeProposal(expected_revision=context.revision),
            turn_id="turn",
            evidence="What if we made 70k?",
        )
    )

    assert [issue.code for issue in result.issues] == [
        "missing_monetary_fact_claim"
    ]
    assert result.issues[0].evidence == "70k"


@pytest.mark.parametrize(
    "message",
    (
        "70k",
        "70,000",
        "70.000",
        "70 000",
        "70\u00a0000",
        "70 thousand",
        "seventy thousand",
        "GBP 70,000",
        "£70k",
        "0.07 million",
    ),
)
def test_fact_claim_value_validation_normalizes_monetary_forms(message):
    claim = FactClaim(
        concept="income",
        subject_references=("person:self", "entity:spouse"),
        relationship=FactClaimRelationship.SUM,
        value=ClaimedMoneyValue(amount=70000),
        evidence=message,
    )

    context = context_with_spouse()
    registry = build_default_fact_registry()
    result = ContextChangeValidator(ContextReducer(registry), registry).validate(
        ValidateContextChangeInput(
            context=context,
            proposal=ContextChangeProposal(
                expected_revision=context.revision,
                changes=(claim,),
            ),
            turn_id="turn",
            evidence=message,
        )
    )

    assert result.status is ContextValidationStatus.RESOLUTION_REQUIRED
    assert {issue.code for issue in result.issues} == {
        "authoritative_resolution_required"
    }


def test_fact_claim_value_validation_rejects_missing_and_copied_values():
    copied = FactClaim(
        concept="income",
        subject_references=("person:self",),
        value=ClaimedMoneyValue(amount=50000),
        evidence="copied from an earlier turn",
    )

    context = context_with_spouse()
    registry = build_default_fact_registry()
    result = ContextChangeValidator(ContextReducer(registry), registry).validate(
        ValidateContextChangeInput(
            context=context,
            proposal=ContextChangeProposal(
                expected_revision=context.revision,
                changes=(copied,),
            ),
            turn_id="turn",
            evidence="What if we made 70k?",
        )
    )

    assert [issue.code for issue in result.issues] == [
        "uncited_fact_claim",
        "missing_monetary_fact_claim",
        "uncited_monetary_fact_claim",
    ]


def test_text_fact_can_preserve_an_embedded_monetary_reform_value():
    message = "Set the personal allowance to £15,000."
    context = ConversationContext.initial("conversation")
    registry = build_default_fact_registry()

    result = ContextChangeValidator(ContextReducer(registry), registry).validate(
        ValidateContextChangeInput(
            context=context,
            proposal=ContextChangeProposal(
                expected_revision=context.revision,
                candidate_entities=(
                    ContextEntityCandidate(
                        reference="new:scenario",
                        kind=EntityKind.POLICY_SCENARIO,
                        aliases=("the reform",),
                    ),
                ),
                changes=(
                    FactClaim(
                        concept="policy reform instruction",
                        definition_key="policy.reform_instruction",
                        subject_references=("new:scenario",),
                        scope_id="scope:primary-household",
                        value=TextFactValue(value=message),
                        evidence=message,
                    ),
                ),
            ),
            turn_id="turn",
            evidence=message,
        )
    )

    assert result.status is ContextValidationStatus.READY_TO_COMMIT
    assert result.issues == ()


def test_context_interpreter_keeps_periodless_direct_money_declarative(monkeypatch):
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_context_change",
                        input={
                            "expected_revision": 0,
                            "changes": [
                                {
                                    "kind": "fact_claim",
                                    "concept": "income",
                                    "subject_references": ["person:self"],
                                    "relationship": "direct",
                                    "value": {
                                        "kind": "money",
                                        "amount": "50000",
                                        "period": None,
                                    },
                                    "evidence": "£50,000 of income",
                                }
                            ],
                        },
                    )
                ],
                usage=_usage(),
            )

    monkeypatch.setattr(
        context_tools,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    result = asyncio.run(
        AnthropicContextInterpreter().propose(
            _request("How much tax would I pay on £50,000 of income?")
        )
    )

    assert len(calls) == 1
    assert len(result.claims) == 1
    assert isinstance(result.claims[0].value, ClaimedMoneyValue)
    assert result.claims[0].value.period is None
    assert "leave an unstated monetary period null" in calls[0]["system"].casefold()


def test_context_interpreter_rejects_server_owned_operations_and_retries(monkeypatch):
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            value = {"expected_revision": 0}
            if len(calls) == 1:
                value["operations"] = []
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_context_change",
                        input=value,
                    )
                ],
                usage=_usage(input_tokens=1, output_tokens=1),
            )

    monkeypatch.setattr(
        context_tools,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    result = asyncio.run(AnthropicContextInterpreter().propose(_request("Thanks.")))

    assert len(calls) == 2
    assert result.claims == ()
    assert result.candidate_entities == ()


def test_context_interpreter_returns_structured_issues_after_invalid_retry(monkeypatch):
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_context_change",
                        input={"expected_revision": 0, "operations": []},
                    )
                ],
                usage=_usage(input_tokens=2, output_tokens=1),
            )

    monkeypatch.setattr(
        context_tools,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    result = asyncio.run(AnthropicContextInterpreter().propose(_request("Thanks.")))

    assert len(calls) == 2
    assert result.status is ContextProposalStatus.NEEDS_CLARIFICATION
    assert result.claims == ()
    assert result.provider_attempts == 2
    assert result.usage.input_tokens == 4
    assert result.issues[0].code == "invalid_context_submission"
    assert result.issues[0].path == ("operations",)


def test_variable_mapper_can_select_only_an_exact_catalogue_candidate(monkeypatch):
    calls = []

    class FakeMessages:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="submit_variable_mapping",
                        input={
                            "status": "matched",
                            "variable_name": "employment_income",
                            "confidence": "high",
                            "target_period": "annual",
                        },
                    )
                ],
                usage=_usage(input_tokens=4, output_tokens=2),
            )

    monkeypatch.setattr(
        variable_resolution,
        "get_async_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )
    result = asyncio.run(
        AnthropicVariableMapper().select(
            claim=FactClaim(
                claim_id="combined-income",
                concept="employment income",
                value=ClaimedMoneyValue(
                    amount=70000,
                    period=MoneyPeriod.ANNUAL,
                ),
                subject_references=("person:self", "entity:spouse"),
                relationship=FactClaimRelationship.SUM,
                evidence="We earn £70,000 together.",
            ),
            candidates=(
                PolicyEngineVariableCandidate(
                    name="employment_income",
                    label="Employment income",
                    entity="person",
                    definition_period="year",
                    value_type="float",
                ),
            ),
            context=context_with_spouse(revision=1),
            registry=build_default_fact_registry(),
            validation_issues=(
                ContextValidationIssue(
                    code="authoritative_resolution_required",
                    message="Select an authoritative mapping.",
                    claim_index=0,
                ),
            ),
        )
    )

    assert result.selection.status is MappingStatus.MATCHED
    assert result.selection.confidence is MappingConfidence.HIGH
    assert result.selection.variable_name == "employment_income"
    payload = json.loads(calls[0]["messages"][0]["content"])
    assert payload["validation_issues"] == [
        {
            "code": "authoritative_resolution_required",
            "message": "Select an authoritative mapping.",
            "path": [],
            "claim_index": 0,
            "operation_index": None,
            "evidence": None,
        }
    ]
