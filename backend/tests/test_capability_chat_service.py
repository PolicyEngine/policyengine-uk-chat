from __future__ import annotations

import asyncio
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, SQLModel, create_engine

from capabilities.composition import compose_runtime
from capabilities.contracts import Capability, CapabilitySpec, Completed, NeedsInput
from capabilities.relevance import (
    AssessRelevanceTool,
    ConversationRelevanceCapability,
    RelevanceAssessment,
    RelevanceResult,
)
from chat.capability_service import ChatTurnService
from chat.events import (
    InvocationActivity,
    TextChunk,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.model_port import (
    ConversationModelResponse,
    ModelCapabilityCall,
    ModelUsage,
)
from chat.turn_input import ChatTurnInput
from persistence.idempotency import ReceiptStatus, SQLIdempotencyRepository
from persistence.rows import CapabilityCallReceiptRow, TurnReceiptRow
from tools.analysis_support import NumericalFact, VerifyNumericalResponseTool
from tools.contracts import CallerType, Visibility


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EchoInput(StrictModel):
    text: str
    optional_note: str | None = None


class EchoOutput(StrictModel):
    text: str
    narration_facts: tuple[NumericalFact, ...] = ()
    assumption_statements: tuple[str, ...] = ()
    narration_fallback: str | None = None
    numerical_verification: Literal["disabled"] | None = None


class EchoCapability(Capability[EchoInput, EchoOutput]):
    spec = CapabilitySpec(
        identifier="echo_capability",
        version="1",
        description="Return a typed test fact.",
        required_use="Use when the test asks for an echo.",
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL}),
        input_model=EchoInput,
        output_model=EchoOutput,
    )

    async def run(self, capability_input, context):
        del context
        facts = ()
        if capability_input.text in {"numeric", "numeric-unverified"}:
            facts = (
                NumericalFact(
                    label="Net cost",
                    value=1_200_000_000,
                    unit="GBP/year",
                ),
            )
        assumptions = (
            ("The household has no childcare expenses.",)
            if capability_input.text == "requirements"
            else ()
        )
        return Completed(
            value=EchoOutput(
                text=capability_input.text,
                narration_facts=facts,
                assumption_statements=assumptions,
                narration_fallback=(
                    "### Results\n\n- Net cost: £1.2 billion."
                    if capability_input.text in {"numeric", "numeric-unverified"}
                    else None
                ),
                numerical_verification=(
                    "disabled"
                    if capability_input.text == "numeric-unverified"
                    else None
                ),
            )
        )


class ClarifyCapability(Capability[EchoInput, EchoOutput]):
    spec = EchoCapability.spec.model_copy(
        update={
            "identifier": "clarify_capability",
            "description": "Request one missing test value.",
        }
    )

    async def run(self, capability_input, context):
        del capability_input, context
        return NeedsInput(
            prompt="Which amount?",
            missing_fields=("text",),
        )


class FailingCapability(Capability[EchoInput, EchoOutput]):
    spec = EchoCapability.spec.model_copy(
        update={
            "identifier": "failing_capability",
            "description": "Fail safely for a localized-failure test.",
        }
    )

    async def run(self, capability_input, context):
        del capability_input, context
        raise RuntimeError("private provider failure")


class SlowCapability(Capability[EchoInput, EchoOutput]):
    spec = EchoCapability.spec.model_copy(
        update={
            "identifier": "slow_capability",
            "description": "Wait until a cancellation test interrupts the call.",
        }
    )

    async def run(self, capability_input, context):
        del capability_input, context
        await asyncio.Event().wait()
        return Completed(value=EchoOutput(text="unreachable"))


class FakeRelevanceAssessor:
    def __init__(self, result=RelevanceResult.RELEVANT):
        self.result = result
        self.requests = []

    async def assess(self, request):
        self.requests.append(request)
        return RelevanceAssessment(
            result=self.result,
            explanation="bounded test result",
            usage={"input_tokens": 2, "output_tokens": 1},
        )


class FakeConversationModel:
    def __init__(self, responses, redraft=""):
        self.responses = list(responses)
        self.requests = []
        self.redraft = redraft
        self.redraft_calls = []

    async def respond(self, request):
        self.requests.append(request)
        return self.responses.pop(0)

    async def redraft_numerical(
        self,
        *,
        draft,
        unsupported_claims,
        fact_summary,
    ):
        self.redraft_calls.append((draft, unsupported_claims, fact_summary))
        return ConversationModelResponse(
            text=self.redraft,
            model="fake-model",
            usage=ModelUsage(input_tokens=3, output_tokens=2),
        )


async def not_cancelled() -> bool:
    return False


def _runtime(model, assessor, *, idempotency=None, include_slow=False):
    capabilities = [
        ConversationRelevanceCapability(),
        EchoCapability(),
        ClarifyCapability(),
        FailingCapability(),
    ]
    if include_slow:
        capabilities.append(SlowCapability())
    composition = compose_runtime(
        tools=[AssessRelevanceTool(assessor), VerifyNumericalResponseTool()],
        capabilities=capabilities,
    )
    service = ChatTurnService(
        executor=composition.executor,
        capabilities=composition.capabilities,
        model=model,
        idempotency=idempotency,
    )
    context = composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=not_cancelled,
    )
    return composition, service, context


def _turn(content="Hello", *, turn_id="turn-1", debug=False):
    return ChatTurnInput(
        messages=[
            {"role": "user", "content": "Earlier question"},
            {"role": "assistant", "content": "Earlier answer"},
            {"role": "user", "content": content},
        ],
        session_id="conversation-1",
        turn_id=turn_id,
        debug=debug,
    )


def _collect(service, turn, context, cancellation=not_cancelled):
    async def collect():
        return [
            event
            async for event in service.run(
                turn,
                is_cancelled=cancellation,
                context=context,
            )
        ]

    return asyncio.run(collect())


def test_direct_answer_keeps_full_history_and_private_relevance_out_of_model_tools():
    assessor = FakeRelevanceAssessor()
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                text="Natural direct answer.",
                model="fake-model",
                stop_reason="end_turn",
                usage=ModelUsage(input_tokens=5, output_tokens=4),
            )
        ]
    )
    _composition, service, context = _runtime(model, assessor)

    events = _collect(service, _turn(), context)

    assert [type(event) for event in events] == [TextChunk, TurnCompleted]
    assert events[0].content == "Natural direct answer."
    assert len(assessor.requests) == 1
    assert assessor.requests[0].current_message == "Hello"
    assert len(model.requests[0].messages) == 3
    capability_ids = {
        item["identifier"] for item in model.requests[0].capabilities
    }
    assert capability_ids == {
        "echo_capability",
        "clarify_capability",
        "failing_capability",
    }
    assert "conversation_relevance" not in capability_ids
    assert "policy_information" in model.requests[0].system
    assert "household_analysis" in model.requests[0].system
    assert "society_analysis" in model.requests[0].system
    assert "Do not conduct a" in model.requests[0].system
    assert "separate informal household intake" in model.requests[0].system
    assert "sole authority for" in model.requests[0].system
    assert "which household details require clarification" in model.requests[0].system
    assert "Assumptions used" in model.requests[0].system
    assert "Markdown bullet" in model.requests[0].system
    assert "referenced_household_id" in model.requests[0].system
    assert "same household" in model.requests[0].system
    assert "every category in required_output_ids" in model.requests[0].system
    assert "Do not infer policy mechanisms" in model.requests[0].system
    assert events[-1].usage.input_tokens == 7
    assert events[-1].usage.output_tokens == 5


def test_clearly_out_of_scope_turn_is_local_and_skips_conversation_model():
    assessor = FakeRelevanceAssessor(RelevanceResult.CLEARLY_OUT_OF_SCOPE)
    model = FakeConversationModel([])
    _composition, service, context = _runtime(model, assessor)

    events = _collect(service, _turn("Calculate Canadian tax"), context)

    assert [type(event) for event in events] == [TextChunk, TurnCompleted]
    assert events[-1].outcome == "out_of_scope"
    assert model.requests == []
    assert len(assessor.requests) == 1


def test_multiple_capability_outcomes_return_to_same_model_without_global_waiting():
    assessor = FakeRelevanceAssessor()
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-1",
                        capability_id="echo_capability",
                        input={"text": "complete"},
                    ),
                    ModelCapabilityCall(
                        call_id="call-2",
                        capability_id="clarify_capability",
                        input={"text": "missing"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="I completed one part. Which amount should I use for the other?",
                model="fake-model",
                stop_reason="end_turn",
            ),
        ]
    )
    _composition, service, context = _runtime(model, assessor)

    events = _collect(service, _turn(), context)

    assert [type(event) for event in events] == [
        InvocationActivity,
        InvocationActivity,
        InvocationActivity,
        InvocationActivity,
        TextChunk,
        TurnCompleted,
    ]
    statuses = [
        event.record.status.value
        for event in events
        if isinstance(event, InvocationActivity) and event.phase == "finished"
    ]
    assert statuses == ["completed", "needs_input"]
    result_blocks = model.requests[1].messages[-1]["content"]
    assert '"status": "completed"' in result_blocks[0]["content"]
    assert '"status": "needs_input"' in result_blocks[1]["content"]


def test_debug_projection_changes_activity_visibility_but_not_capability_result():
    responses = [
        ConversationModelResponse(
            capability_calls=(
                ModelCapabilityCall(
                    call_id="call-1",
                    capability_id="echo_capability",
                    input={"text": "same calculation"},
                ),
            ),
            model="fake-model",
        ),
        ConversationModelResponse(
            text="Same grounded result.",
            model="fake-model",
            stop_reason="end_turn",
        ),
    ]
    normal_model = FakeConversationModel(responses)
    debug_model = FakeConversationModel(responses)
    normal_composition, normal_service, normal_context = _runtime(
        normal_model,
        FakeRelevanceAssessor(),
    )
    debug_composition, debug_service, debug_context = _runtime(
        debug_model,
        FakeRelevanceAssessor(),
    )

    normal_events = _collect(normal_service, _turn(debug=False), normal_context)
    debug_events = _collect(debug_service, _turn(debug=True), debug_context)

    assert next(event.content for event in normal_events if isinstance(event, TextChunk)) == (
        "Same grounded result."
    )
    assert next(event.content for event in debug_events if isinstance(event, TextChunk)) == (
        "Same grounded result."
    )
    normal_ids = {
        event.record.identifier
        for event in normal_events
        if isinstance(event, InvocationActivity)
    }
    debug_ids = {
        event.record.identifier
        for event in debug_events
        if isinstance(event, InvocationActivity)
    }
    assert normal_ids == {"echo_capability"}
    assert debug_ids == {
        "conversation_relevance",
        "assess_relevance",
        "echo_capability",
    }
    assert all(
        event.record.debug_input is None and event.record.debug_output is None
        for event in normal_events
        if isinstance(event, InvocationActivity)
    )
    assert all(
        event.record.debug_input is not None
        for event in debug_events
        if isinstance(event, InvocationActivity)
    )
    assert all(
        event.record.debug_output is not None
        for event in debug_events
        if isinstance(event, InvocationActivity) and event.phase == "finished"
    )
    assert {
        record.identifier
        for record in normal_composition.tracer.records(
            "conversation-1",
            include_private=True,
        )
    } == debug_ids
    assert {
        record.identifier
        for record in debug_composition.tracer.records(
            "conversation-1",
            include_private=True,
        )
    } == debug_ids


def test_model_capability_trace_matches_the_json_exchanged_with_the_model():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-1",
                        capability_id="clarify_capability",
                        input={"text": "missing"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="Which amount?",
                model="fake-model",
                stop_reason="end_turn",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(debug=True), context)

    finished = next(
        event.record
        for event in events
        if isinstance(event, InvocationActivity)
        and event.phase == "finished"
        and event.record.identifier == "clarify_capability"
    )
    model_result = json.loads(
        model.requests[1].messages[-1]["content"][0]["content"]
    )
    assert finished.debug_input == {"text": "missing"}
    assert "optional_note" not in finished.debug_input
    assert finished.debug_output == model_result
    assert finished.debug_output["response_guidance"].startswith(
        "Ask the supplied prompt"
    )


def test_completed_capability_tells_model_to_report_results_before_follow_up():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-completed",
                        capability_id="echo_capability",
                        input={"text": "completed result"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="Here is the completed result.",
                model="fake-model",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    _collect(service, _turn(), context)

    result = json.loads(model.requests[1].messages[-1]["content"][0]["content"])
    assert result["status"] == "completed"
    assert "Answer the current request now" in result["response_guidance"]
    assert "Do not ask for input or an output choice" in (
        result["response_guidance"]
    )


def test_model_result_omits_verifier_only_narration_fields():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-numeric",
                        capability_id="echo_capability",
                        input={"text": "numeric"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="Net cost is £1.2 billion.",
                model="fake-model",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(debug=True), context)

    model_result = json.loads(
        model.requests[1].messages[-1]["content"][0]["content"]
    )
    assert "narration_facts" not in model_result["value"]
    assert "narration_fallback" not in model_result["value"]
    assert "numerical_verification" not in model_result["value"]
    assert model_result["value"]["text"] == "numeric"
    finished = next(
        event.record
        for event in events
        if isinstance(event, InvocationActivity)
        and event.phase == "finished"
        and event.record.identifier == "echo_capability"
    )
    assert finished.debug_output == model_result


def test_failed_capability_is_returned_to_model_without_blocking_sibling_call():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-failed",
                        capability_id="failing_capability",
                        input={"text": "fail"},
                    ),
                    ModelCapabilityCall(
                        call_id="call-complete",
                        capability_id="echo_capability",
                        input={"text": "continue"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="One operation failed, while the other completed.",
                model="fake-model",
                stop_reason="end_turn",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(debug=True), context)

    result_blocks = model.requests[1].messages[-1]["content"]
    assert '"status": "failed"' in result_blocks[0]["content"]
    assert "private provider failure" not in result_blocks[0]["content"]
    assert '"status": "completed"' in result_blocks[1]["content"]
    failed_trace = next(
        event.record
        for event in events
        if isinstance(event, InvocationActivity)
        and event.phase == "finished"
        and event.record.identifier == "failing_capability"
    )
    assert failed_trace.debug_output == json.loads(result_blocks[0]["content"])
    assert any(
        isinstance(event, TextChunk)
        and event.content == "One operation failed, while the other completed."
        for event in events
    )


def test_needs_input_cannot_be_retried_or_replaced_with_estimated_numbers():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-needs-input",
                        capability_id="clarify_capability",
                        input={"text": "missing"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-retry",
                        capability_id="clarify_capability",
                        input={"text": "invented"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="The estimated answer is £15,000.",
                model="fake-model",
                stop_reason="end_turn",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(), context)

    finished_clarifications = [
        event
        for event in events
        if isinstance(event, InvocationActivity)
        and event.phase == "finished"
        and event.record.identifier == "clarify_capability"
    ]
    assert len(finished_clarifications) == 1
    assert any(
        definition["identifier"] == "clarify_capability"
        for definition in model.requests[0].capabilities
    )
    assert all(
        definition["identifier"] != "clarify_capability"
        for request in model.requests[1:]
        for definition in request.capabilities
    )
    assert len(model.requests) == 2
    repeated_result = json.loads(
        model.requests[1].messages[-1]["content"][0]["content"]
    )
    assert repeated_result["status"] == "needs_input"
    assert repeated_result["prompt"] == "Which amount?"
    assert "safe_message" not in repeated_result
    assert "capability_repeated_without_new_user_evidence" not in str(
        repeated_result
    )
    assert events[-2].content == "Which amount?"
    assert "15,000" not in events[-2].content
    assert model.redraft_calls == []


def test_natural_numbered_clarification_skips_calculation_verification():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-needs-input",
                        capability_id="clarify_capability",
                        input={"text": "missing"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text=(
                    "I need a little more information:\n\n"
                    "1. What is your age?\n"
                    "2. Which amount should I use?"
                ),
                model="fake-model",
                stop_reason="end_turn",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(debug=True), context)

    assert events[-2].content == (
        "I need a little more information:\n\n"
        "1. What is your age?\n"
        "2. Which amount should I use?"
    )
    assert not any(
        isinstance(event, InvocationActivity)
        and event.record.identifier == "verify_numerical_response"
        for event in events
    )


def test_quantitative_response_gets_one_free_form_correction_and_usage_accounting():
    assessor = FakeRelevanceAssessor()
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-1",
                        capability_id="echo_capability",
                        input={"text": "numeric"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="The reform costs £2 billion.",
                model="fake-model",
                usage=ModelUsage(input_tokens=4, output_tokens=3),
            ),
        ],
        redraft="The reform costs £1.2 billion.",
    )
    _composition, service, context = _runtime(model, assessor)

    events = _collect(service, _turn(), context)

    assert events[-2].content == "The reform costs £1.2 billion."
    assert len(model.redraft_calls) == 1
    assert events[-1].usage.input_tokens == 9
    assert events[-1].usage.output_tokens == 6


def test_capability_can_disable_numerical_verification_for_model_narration():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-unverified",
                        capability_id="echo_capability",
                        input={"text": "numeric-unverified"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="The model may describe this as approximately £1.2 billion.",
                model="fake-model",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(debug=True), context)

    assert events[-2].content == (
        "The model may describe this as approximately £1.2 billion."
    )
    assert model.redraft_calls == []
    assert not any(
        isinstance(event, InvocationActivity)
        and event.record.identifier == "verify_numerical_response"
        for event in events
    )


def test_remaining_unsupported_sentence_is_removed_before_fact_list_fallback():
    draft = (
        "The verified net cost is £1.2 billion.\n\n"
        "An unsupported estimate is £2 billion.\n\n"
        "The policy direction is unchanged."
    )
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-sanitize",
                        capability_id="echo_capability",
                        input={"text": "numeric"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(text=draft, model="fake-model"),
        ],
        redraft=draft,
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(), context)

    assert events[-2].content == (
        "The verified net cost is £1.2 billion.\n\n"
        "The policy direction is unchanged."
    )
    assert "£2 billion" not in events[-2].content
    assert not events[-2].content.startswith("### Results")
    assert len(model.redraft_calls) == 1


def test_missing_assumptions_are_appended_as_a_markdown_list():
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        call_id="call-requirements",
                        capability_id="echo_capability",
                        input={"text": "requirements"},
                    ),
                ),
                model="fake-model",
            ),
            ConversationModelResponse(
                text="Here is the naturally written result.",
                model="fake-model",
            ),
        ]
    )
    _composition, service, context = _runtime(model, FakeRelevanceAssessor())

    events = _collect(service, _turn(), context)

    assert events[-2].content == (
        "Here is the naturally written result.\n\n"
        "### Assumptions used\n\n"
        "- The household has no childcare expenses."
    )


def test_missing_assumption_is_added_to_an_existing_markdown_list():
    response = (
        "Here is the result.\n\n"
        "### Assumptions used\n\n"
        "- Policy year: 2026.\n\n"
        "### Next steps\n\n"
        "You can revise the household."
    )

    completed = ChatTurnService._ensure_assumption_list(
        response,
        ["Policy year: 2026.", "The household lives in England."],
    )

    assert completed.count("### Assumptions used") == 1
    assert "- Policy year: 2026." in completed
    assert "- The household lives in England.\n### Next steps" in completed


def test_bold_assumption_heading_is_not_duplicated():
    response = (
        "Here is the result.\n\n"
        "**Assumptions used**\n\n"
        "- Policy year: 2026.\n"
        "- The household lives in England."
    )

    completed = ChatTurnService._ensure_assumption_list(
        response,
        ["Policy year: 2026.", "The household lives in England."],
    )

    assert completed == response
    assert completed.casefold().count("assumptions used") == 1


def test_request_cancellation_stops_before_relevance_or_model():
    assessor = FakeRelevanceAssessor()
    model = FakeConversationModel([])
    _composition, service, context = _runtime(model, assessor)

    async def cancelled() -> bool:
        return True

    events = _collect(service, _turn(), context, cancelled)

    assert len(events) == 1
    assert isinstance(events[0], TurnCancelled)
    assert assessor.requests == []
    assert model.requests == []


def test_request_cancellation_awaits_an_active_capability_and_finishes_its_trace(
    tmp_path,
):
    assessor = FakeRelevanceAssessor()
    model = FakeConversationModel(
        [
            ConversationModelResponse(
                capability_calls=(
                    ModelCapabilityCall(
                        capability_id="slow_capability",
                        call_id="slow-call",
                        input={"text": "wait"},
                    ),
                ),
                model="fake-model",
            )
        ]
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'cancellation.sqlite'}")
    SQLModel.metadata.create_all(engine)
    composition, service, context = _runtime(
        model,
        assessor,
        idempotency=SQLIdempotencyRepository(engine=engine),
        include_slow=True,
    )
    checks = 0

    async def cancel_during_capability() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 3

    events = _collect(service, _turn(), context, cancel_during_capability)

    assert isinstance(events[-1], TurnCancelled)
    records = composition.tracer.records(
        "conversation-1",
        include_private=True,
    )
    slow = next(record for record in records if record.identifier == "slow_capability")
    assert slow.status.value == "cancelled"
    assert slow.completed_at is not None
    assert all(record.status.value != "running" for record in records)
    with Session(engine) as session:
        turn_receipt = session.get(TurnReceiptRow, "turn-1")
        call_receipt = session.get(CapabilityCallReceiptRow, "slow-call")
    assert turn_receipt is not None
    assert call_receipt is not None
    assert turn_receipt.status == ReceiptStatus.FAILED.value
    assert call_receipt.status == ReceiptStatus.FAILED.value


def test_turn_idempotency_replays_and_rejects_conflicting_input(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'idempotency.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)
    idempotency = SQLIdempotencyRepository(engine=engine)
    assessor = FakeRelevanceAssessor()
    first_model = FakeConversationModel(
        [ConversationModelResponse(text="First answer", model="fake-model")]
    )
    _composition, service, context = _runtime(
        first_model,
        assessor,
        idempotency=idempotency,
    )
    first = _collect(service, _turn(), context)

    replay_model = FakeConversationModel([])
    _composition, replay_service, replay_context = _runtime(
        replay_model,
        assessor,
        idempotency=idempotency,
    )
    replay = _collect(replay_service, _turn(), replay_context)
    conflict = _collect(
        replay_service,
        _turn("Different input"),
        replay_context,
    )

    assert first[-1].outcome == "completed"
    assert replay[-1].outcome == "replay"
    assert replay[-1].content == "First answer"
    assert isinstance(conflict[-1], TurnFailed)
    assert conflict[-1].stop_reason == "idempotency_conflict"
    assert replay_model.requests == []
    assert len(assessor.requests) == 1
