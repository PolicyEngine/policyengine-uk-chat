from chat.events import ChatUsage, ToolCompleted, TurnCompleted


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


def test_tool_completed_retains_the_complete_structured_result():
    output = {
        "status": "success",
        "rows": [{"income": 25_000, "nested": {"values": list(range(30))}}],
    }

    event = ToolCompleted(
        tool_name="run_society_simulation",
        tool_id="tool-1",
        status="success",
        output=output,
    )

    assert event.output is output
    assert event.output["rows"][0]["nested"]["values"][-1] == 29


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
