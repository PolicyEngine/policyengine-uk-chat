from datetime import datetime, timezone

from capabilities.tracing import InvocationKind, InvocationRecord, InvocationStatus
from chat.events import ChatUsage, InvocationActivity, TurnCompleted
from tools.contracts import Visibility


def test_chat_usage_exposes_the_existing_public_shape():
    usage = ChatUsage(
        input_tokens=10,
        output_tokens=5,
        cache_creation_input_tokens=3,
        cache_read_input_tokens=7,
    )

    assert usage.as_dict() == {
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_creation_input_tokens": 3,
        "cache_read_input_tokens": 7,
    }


def test_invocation_activity_retains_the_sanitized_structured_projection():
    output = {
        "status": "success",
        "rows": [{"income": 25_000, "nested": {"values": list(range(30))}}],
    }

    record = InvocationRecord(
        conversation_id="session-1",
        turn_id="turn-1",
        invocation_id="invocation-1",
        sequence=1,
        kind=InvocationKind.TOOL,
        identifier="run_society_simulation",
        version="1",
        visibility=Visibility.PRIVATE,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        duration_ms=1,
        status=InvocationStatus.COMPLETED,
        summary="Society simulation completed.",
        debug_output=output,
    )
    event = InvocationActivity("finished", record)

    assert event.record.debug_output == output
    assert event.record.debug_output["rows"][0]["nested"]["values"][-1] == 29


def test_turn_completed_carries_execution_metadata_without_http_fields():
    event = TurnCompleted(
        content="Answer",
        session_id="session-1",
        model="claude",
        route="compute",
        outcome="ready",
        stop_reason="end_turn",
        usage=ChatUsage(input_tokens=1),
    )

    assert event.content == "Answer"
    assert event.usage.input_tokens == 1
    assert not hasattr(event, "cost_gbp")
    assert not hasattr(event, "balance")
