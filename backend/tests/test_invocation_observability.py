from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict
from sqlmodel import Session, SQLModel, create_engine

from capabilities.composition import compose_runtime
from capabilities.contracts import Capability, CapabilitySpec, Completed
from capabilities.executor import InvocationCancelled
from capabilities.tracing import InvocationStatus, InvocationTracer
from chat.activity import get_conversation_activity
from conversations.models import ChatConversation
from persistence.trace_repository import SQLInvocationTraceRepository
from tools.contracts import CallerType, Tool, ToolCallContext, ToolSpec, Visibility


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SecretInput(StrictModel):
    api_key: str
    reform: dict[str, object]
    household_record: dict[str, object]
    row_data: dict[str, object]
    result_handle: str


class SecretOutput(StrictModel):
    accepted: bool


class PrivateSecretTool(Tool[SecretInput, SecretOutput]):
    spec = ToolSpec(
        identifier="private_secret_check",
        version="1",
        description="Validate a test input.",
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.CAPABILITY}),
        input_model=SecretInput,
        output_model=SecretOutput,
    )

    async def run(self, tool_input, context: ToolCallContext):
        del tool_input, context
        return SecretOutput(accepted=True)

    def trace_summary(self, status: str) -> str:
        return f"SHOULD-NOT-PERSIST sk-secret raw-household {status}"


class RepeatedCapability(Capability[SecretInput, SecretOutput]):
    spec = CapabilitySpec(
        identifier="repeated_capability",
        version="1",
        description="Invoke a private validation operation twice.",
        required_use="Only in this test.",
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.MODEL}),
        input_model=SecretInput,
        output_model=SecretOutput,
        tool_dependencies=("private_secret_check",),
    )

    async def run(self, capability_input, context):
        await context.invoke_tool("private_secret_check", capability_input)
        value = await context.invoke_tool("private_secret_check", capability_input)
        return Completed(value=value)


def _engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'trace.sqlite'}")
    SQLModel.metadata.create_all(engine)
    return engine


async def _not_cancelled() -> bool:
    return False


def _secret_input() -> SecretInput:
    return SecretInput(
        api_key="sk-secret",
        reform={"parameter_payload": "reform-secret-shape"},
        household_record={"person": {"income": 12345}},
        row_data={"person-1": {"income": 12345}},
        result_handle="request-local-result-93",
    )


def test_tracer_persists_every_nested_attempt_with_only_allowlisted_metadata(tmp_path):
    repository = SQLInvocationTraceRepository(engine=_engine(tmp_path))
    tracer = InvocationTracer(sink=repository)
    composition = compose_runtime(
        tools=[PrivateSecretTool()],
        capabilities=[RepeatedCapability()],
        tracer=tracer,
    )
    context = composition.executor.context(
        request_id="request-local-request-17",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=_not_cancelled,
    )

    outcome = asyncio.run(
        composition.executor.invoke_capability(
            "repeated_capability",
            _secret_input(),
            caller=CallerType.MODEL,
            context=context,
        )
    )

    assert isinstance(outcome, Completed)
    records = repository.list_for_conversation(
        "conversation-1",
        include_private=True,
    )
    assert [record.identifier for record in records] == [
        "repeated_capability",
        "private_secret_check",
        "private_secret_check",
    ]
    assert records[1].parent_invocation_id == records[0].invocation_id
    assert records[2].parent_invocation_id == records[0].invocation_id
    assert [record.sequence for record in records] == [1, 2, 3]
    assert all(record.status is InvocationStatus.COMPLETED for record in records)
    assert records[0].debug_input == {
        "api_key": "[redacted secret]",
        "reform": {"parameter_payload": "reform-secret-shape"},
        "household_record": {"person": {"income": 12345}},
        "row_data": "[record-level data omitted]",
        "result_handle": "[request-local identifier omitted]",
    }
    assert records[0].debug_output == {
        "status": "completed",
        "value": {"accepted": True},
    }
    assert records[1].debug_output == {"accepted": True}
    retained = " ".join(record.model_dump_json() for record in records)
    for forbidden in (
        "sk-secret",
        "raw-household",
        "request-local-result-93",
        "request-local-request-17",
        "SHOULD-NOT-PERSIST",
    ):
        assert forbidden not in retained
    assert "12345" in retained
    assert "reform-secret-shape" in retained


def test_failed_and_cancelled_calls_record_generic_status_without_exception_text(tmp_path):
    class FailingTool(PrivateSecretTool):
        spec = PrivateSecretTool.spec.model_copy(
            update={
                "identifier": "failing_tool",
                "allowed_callers": frozenset({CallerType.RUNTIME}),
            }
        )

        async def run(self, tool_input, context):
            del tool_input, context
            raise RuntimeError("sk-secret provider-payload")

    repository = SQLInvocationTraceRepository(engine=_engine(tmp_path))
    composition = compose_runtime(
        tools=[FailingTool()],
        capabilities=[],
        tracer=InvocationTracer(sink=repository),
    )
    context = composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=_not_cancelled,
    )
    with pytest.raises(RuntimeError, match="provider-payload"):
        asyncio.run(
            composition.executor.invoke_tool(
                "failing_tool",
                _secret_input(),
                caller=CallerType.RUNTIME,
                context=context,
            )
        )
    failed = repository.list_for_conversation(
        "conversation-1", include_private=True
    )[0]
    assert failed.status is InvocationStatus.FAILED
    assert "sk-secret" not in failed.summary

    async def cancelled() -> bool:
        return True

    cancelled_context = composition.executor.context(
        request_id="request-2",
        conversation_id="conversation-2",
        turn_id="turn-2",
        is_cancelled=cancelled,
    )
    with pytest.raises(InvocationCancelled):
        asyncio.run(
            composition.executor.invoke_tool(
                "failing_tool",
                _secret_input(),
                caller=CallerType.RUNTIME,
                context=cancelled_context,
            )
        )
    cancelled_record = repository.list_for_conversation(
        "conversation-2", include_private=True
    )[0]
    assert cancelled_record.status is InvocationStatus.CANCELLED


def test_activity_endpoint_authorizes_and_supports_late_debug_enablement(
    tmp_path,
    monkeypatch,
):
    engine = _engine(tmp_path)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(
            ChatConversation(
                session_id="conversation-1",
                title="Trace test",
                messages="[]",
                user_id="owner-1",
                created_at=now,
                updated_at=now,
            )
        )
        session.commit()
    import conversations.models as conversation_models

    monkeypatch.setattr(conversation_models, "_engine", engine)
    tracer = InvocationTracer(sink=SQLInvocationTraceRepository(engine=engine))
    public = tracer.start(
        conversation_id="conversation-1",
        turn_id="turn-1",
        parent_invocation_id=None,
        kind="capability",
        identifier="society_analysis",
        version="1",
        visibility=Visibility.PUBLIC,
        summary="started",
        debug_input={"question": "What changed?"},
    )
    tracer.finish(
        public.invocation_id,
        status=InvocationStatus.COMPLETED,
        summary="done",
        debug_output={"answer": "Validated output"},
    )
    private = tracer.start(
        conversation_id="conversation-1",
        turn_id="turn-1",
        parent_invocation_id=public.invocation_id,
        kind="tool",
        identifier="validate_reform",
        version="1",
        visibility=Visibility.PRIVATE,
        summary="started",
        debug_input={"year": 2026},
    )
    tracer.finish(
        private.invocation_id,
        status=InvocationStatus.COMPLETED,
        summary="done",
        debug_output={"valid": True},
    )

    normal = get_conversation_activity(
        "conversation-1",
        user_id="owner-1",
        debug=False,
    )
    debug = get_conversation_activity(
        "conversation-1",
        user_id="owner-1",
        debug=True,
    )

    assert normal.projection == "normal"
    assert [item.identifier for item in normal.invocations] == ["society_analysis"]
    assert normal.invocations[0].debug_input is None
    assert normal.invocations[0].debug_output is None
    assert debug.projection == "debug"
    assert [item.identifier for item in debug.invocations] == [
        "society_analysis",
        "validate_reform",
    ]
    assert debug.invocations[1].parent_invocation_id == public.invocation_id
    assert debug.invocations[0].debug_input == {"question": "What changed?"}
    assert debug.invocations[0].debug_output == {"answer": "Validated output"}
    assert debug.invocations[1].debug_input == {"year": 2026}
    assert debug.invocations[1].debug_output == {"valid": True}
    with pytest.raises(HTTPException) as error:
        get_conversation_activity(
            "conversation-1",
            user_id="different-user",
            debug=True,
        )
    assert error.value.status_code == 403
