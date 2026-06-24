"""
Integration tests for the FastAPI endpoints.
Tests the HTTP layer — chat streaming, conversations CRUD, title generation.
Run inside the backend container: pytest tests/
"""

import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from policyengine_observability.runtime import EVENT_LOGGER
from policyengine_observability.runtime import OPERATION_LOGGER
from api.main import app

client = TestClient(app)

requires_live_anthropic = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_ANTHROPIC_TESTS") != "1"
    or not os.environ.get("ANTHROPIC_API_KEY"),
    reason="set RUN_LIVE_ANTHROPIC_TESTS=1 and ANTHROPIC_API_KEY to run live Anthropic tests",
)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("isolated_conversations_table")
class TestConversations:
    def _save(
        self, session_id="test-session-1", title="Test", messages=None, user_id=None
    ):
        return client.post(
            "/conversations",
            json={
                "session_id": session_id,
                "title": title,
                "messages": messages or [{"role": "user", "content": "hello"}],
                "user_id": user_id,
            },
        )

    def test_save_conversation(self):
        r = self._save(session_id="crud-test-1")
        assert r.status_code == 200
        data = r.json()
        assert data["session_id"] == "crud-test-1"
        assert data["title"] == "Test"
        assert "id" in data

    def test_list_conversations(self):
        self._save(session_id="list-test-1", user_id="user@example.com")
        r = client.get("/conversations", params={"user_id": "user@example.com"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)
        session_ids = [c["session_id"] for c in r.json()]
        assert "list-test-1" in session_ids

    def test_get_conversation(self):
        save_r = self._save(session_id="get-test-1")
        conv_id = save_r.json()["id"]
        r = client.get(f"/conversations/{conv_id}")
        assert r.status_code == 200
        assert r.json()["id"] == conv_id
        assert "messages" in r.json()

    def test_get_nonexistent_returns_404(self):
        r = client.get("/conversations/999999")
        assert r.status_code == 404

    def test_delete_conversation(self):
        save_r = self._save(session_id="delete-test-1")
        conv_id = save_r.json()["id"]
        r = client.delete(f"/conversations/{conv_id}")
        assert r.status_code == 204
        r2 = client.get(f"/conversations/{conv_id}")
        assert r2.status_code == 404

    def test_delete_nonexistent_returns_404(self):
        r = client.delete("/conversations/999999")
        assert r.status_code == 404

    def test_update_existing_session(self):
        self._save(session_id="upsert-test-1", title="Original")
        r = self._save(session_id="upsert-test-1", title="Updated")
        assert r.status_code == 200
        assert r.json()["title"] == "Updated"

    def test_messages_roundtrip(self):
        messages = [
            {"role": "user", "content": "hello"},
            {
                "role": "assistant",
                "content": "hi there",
                "events": [{"type": "text", "content": "hi there"}],
            },
        ]
        save_r = self._save(session_id="msg-roundtrip-1", messages=messages)
        conv_id = save_r.json()["id"]
        r = client.get(f"/conversations/{conv_id}")
        loaded = r.json()["messages"]
        assert len(loaded) == 2
        assert loaded[1]["events"][0]["content"] == "hi there"

    def test_list_without_user_id_returns_anonymous(self):
        self._save(session_id="anon-test-1", user_id=None)
        r = client.get("/conversations")
        assert r.status_code == 200

    def test_report_includes_tool_inputs_and_outputs(self):
        messages = [
            {"role": "user", "content": "how much does child benefit cost"},
            {
                "role": "assistant",
                "content": "I'll find out.",
                "events": [
                    {
                        "type": "tool",
                        "data": {
                            "tool_name": "run_python",
                            "tool_id": "tool-1",
                            "status": "success",
                            "input": {"code": "result = 1 + 1"},
                            "result_summary": '{"result": 2, "output": "done"}',
                        },
                    }
                ],
            },
        ]
        save_r = self._save(
            session_id="report-test-1", messages=messages, user_id="user-1"
        )
        conv_id = save_r.json()["id"]
        report_r = client.post(
            f"/conversations/{conv_id}/report",
            json={"user_id": "user-1", "app_url": "https://example.com"},
        )
        assert report_r.status_code == 200
        issue_body = report_r.json()["issue_body"]
        assert "result = 1 + 1" in issue_body
        assert '"result": 2' in issue_body


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------


@requires_live_anthropic
class TestTitle:
    def test_generates_title(self):
        r = client.post(
            "/chat/title",
            json={
                "first_user_message": "What is the personal allowance for 2025?",
            },
        )
        assert r.status_code == 200
        title = r.json()["title"]
        assert isinstance(title, str)
        assert len(title) > 0
        assert len(title) < 100

    def test_title_with_assistant_message(self):
        r = client.post(
            "/chat/title",
            json={
                "first_user_message": "How much child benefit do I get for two children?",
                "first_assistant_message": "Child benefit for two children is £XXX per week.",
            },
        )
        assert r.status_code == 200
        assert "title" in r.json()


# ---------------------------------------------------------------------------
# Chat streaming
# ---------------------------------------------------------------------------


def parse_sse(response_text: str) -> list[dict]:
    """Parse SSE response into list of event dicts."""
    events = []
    for line in response_text.splitlines():
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


def _anthropic_event(name: str, **attrs):
    event = type(name, (), {})()
    for key, value in attrs.items():
        setattr(event, key, value)
    return event


class _FakeAnthropicStream:
    def __init__(self, *, chunks=None, final_content=None, stop_reason="end_turn"):
        self._events = [
            _anthropic_event(
                "RawContentBlockDeltaEvent",
                delta=SimpleNamespace(type="text_delta", text=chunk),
            )
            for chunk in (chunks or [])
        ]
        self._final = SimpleNamespace(
            content=final_content or [],
            stop_reason=stop_reason,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def __aiter__(self):
        return self._iter_events()

    async def _iter_events(self):
        for event in self._events:
            yield event

    async def get_final_message(self):
        return self._final


class _FakeAnthropicMessages:
    def __init__(self, streams):
        self._streams = list(streams)
        self.calls = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self._streams.pop(0)


class _FakeAnthropicClient:
    def __init__(self, streams):
        self.messages = _FakeAnthropicMessages(streams)


def _tool_use_block(name: str, tool_input: dict, tool_id: str = "tool-1"):
    return SimpleNamespace(
        type="tool_use",
        id=tool_id,
        name=name,
        input=tool_input,
    )


@requires_live_anthropic
class TestChatMessage:
    def test_simple_chat_returns_sse(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Say exactly: hello"}],
            },
        ) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            text = r.read().decode()
        events = parse_sse(text)
        assert len(events) > 0

    def test_done_event_present(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Reply with one word: yes"}],
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        types = [e["type"] for e in events]
        assert "done" in types

    def test_session_id_returned(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Reply with one word: yes"}],
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        done = next(e for e in events if e["type"] == "done")
        assert "session_id" in done
        assert len(done["session_id"]) > 0

    def test_provided_session_id_echoed(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Reply with one word: yes"}],
                "session_id": "my-fixed-session",
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        done = next(e for e in events if e["type"] == "done")
        assert done["session_id"] == "my-fixed-session"

    def test_chunk_events_contain_text(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [
                    {"role": "user", "content": "Write a single sentence about the UK."}
                ],
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        chunks = [e for e in events if e["type"] == "chunk"]
        assert len(chunks) > 0
        full_text = "".join(e["content"] for e in chunks)
        assert len(full_text) > 5

    def test_tool_use_for_simulation_query(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "What is the current personal allowance? Use get_baseline_parameters.",
                    }
                ],
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        types = [e["type"] for e in events]
        assert "tool_start" in types or "tool_use" in types

    def test_no_error_event_on_simple_query(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Reply with one word: yes"}],
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 0

    def test_usage_in_done_event(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Reply with one word: yes"}],
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        done = next(e for e in events if e["type"] == "done")
        assert "usage" in done
        assert done["usage"]["input_tokens"] > 0


class TestChatRouteWithMockedAnthropic:
    def test_chat_route_executes_tool_loop_and_returns_final_answer(self, monkeypatch):
        import chat.orchestrator as chatbot

        operation_records = []

        async def no_suggestions(*_args, **_kwargs):
            return []

        tool_input = {
            "year": 2025,
            "person": [
                {
                    "person_id": 0,
                    "benunit_id": 0,
                    "household_id": 0,
                    "age": 35,
                    "employment_income": 30000,
                }
            ],
            "benunit": [{"benunit_id": 0, "household_id": 0}],
            "household": [{"household_id": 0}],
        }
        fake_client = _FakeAnthropicClient(
            [
                _FakeAnthropicStream(
                    final_content=[
                        _tool_use_block("calculate_household", tool_input),
                    ],
                ),
                _FakeAnthropicStream(
                    chunks=[
                        "For this illustrative household, net income is £25119.60."
                    ],
                    final_content=[],
                ),
            ]
        )
        executed = []

        def fake_execute_tool(tool_name, received_input):
            executed.append((tool_name, received_input))
            return {
                "status": "success",
                "household": [{"net_income": 25119.60}],
            }

        monkeypatch.setattr(chatbot, "get_async_client", lambda: fake_client)
        monkeypatch.setattr(chatbot, "_generate_followup_suggestions", no_suggestions)
        monkeypatch.setattr(chatbot, "execute_tool", fake_execute_tool)
        monkeypatch.setattr(OPERATION_LOGGER, "info", operation_records.append)
        usage_calls = []

        def fake_record_usage(**kwargs):
            usage_calls.append(kwargs)
            return {"cost_gbp": 0.0, "balance": 10.0}

        monkeypatch.setattr("billing.record_usage", fake_record_usage)

        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Calculate this household."}]
            },
        ) as response:
            assert response.status_code == 200
            text = response.read().decode()

        events = parse_sse(text)
        assert [
            event["type"]
            for event in events
            if event["type"] in {"tool_use", "tool_result", "done"}
        ] == [
            "tool_use",
            "tool_result",
            "done",
        ]
        done = next(event for event in events if event["type"] == "done")
        assert "£25119.60" in done["content"]
        assert "timings" not in done
        assert usage_calls
        assert "timings" not in usage_calls[0]
        assert "timings_ms" not in usage_calls[0]
        assert "timing_counts" not in usage_calls[0]
        assert executed == [("calculate_household", tool_input)]
        assert "tools" in fake_client.messages.calls[0]
        second_messages = fake_client.messages.calls[1]["messages"]
        assert second_messages[-1]["content"][0]["type"] == "tool_result"
        turn_log = next(
            payload
            for payload in map(json.loads, operation_records)
            if payload.get("operation") == "chat.turn"
        )
        assert turn_log["event"] == "operation_completed"
        assert turn_log["gateway_route"] == "compute"
        assert turn_log["gateway_outcome"] == "ready"
        assert turn_log["model"]
        assert turn_log["ttft_ms"] >= 0
        assert turn_log["timings_ms"]["gateway.classify"] >= 0
        assert turn_log["timings_ms"]["gateway.plan_serialize"] >= 0
        assert turn_log["timings_ms"]["model.select"] >= 0
        assert turn_log["timings_ms"]["system.build"] >= 0
        assert turn_log["timings_ms"]["tool_schema.build"] >= 0
        assert turn_log["timings_ms"]["model.iteration"] >= 0
        assert turn_log["timings_ms"]["model.stream"] >= 0
        assert turn_log["timings_ms"]["tool.execute"] >= 0
        assert turn_log["timings_ms"]["billing.record_usage"] >= 0
        assert turn_log["timing_counts"]["gateway.classify"] == 1
        assert turn_log["timing_counts"]["gateway.plan_serialize"] == 1
        assert turn_log["timing_counts"]["model.select"] == 1
        assert turn_log["timing_counts"]["system.build"] == 1
        assert turn_log["timing_counts"]["tool_schema.build"] == 1
        assert turn_log["timing_counts"]["model.iteration"] == 2
        assert turn_log["timing_counts"]["model.stream"] == 2
        assert turn_log["timing_counts"]["tool.execute"] == 1
        assert turn_log["timing_counts"]["billing.record_usage"] == 1

    def test_chat_route_logs_client_disconnect(self, monkeypatch):
        import chat.orchestrator as chatbot
        from chat.schemas import ChatRequest

        class DisconnectingRequest:
            async def is_disconnected(self):
                return True

        operation_records = []
        event_records = []
        monkeypatch.setattr(chatbot, "get_async_client", lambda: object())
        monkeypatch.setattr(chatbot, "_is_followup", lambda _conversation: True)
        monkeypatch.setattr(OPERATION_LOGGER, "info", operation_records.append)
        monkeypatch.setattr(EVENT_LOGGER, "info", event_records.append)

        async def consume_stream():
            response = chatbot.stream_chat(
                DisconnectingRequest(),
                ChatRequest(
                    messages=[
                        {
                            "role": "user",
                            "content": "Calculate this household.",
                        }
                    ],
                    session_id="disconnect-session",
                ),
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            return chunks

        assert asyncio.run(consume_stream()) == []

        turn_log = next(
            payload
            for payload in map(json.loads, operation_records)
            if payload.get("operation") == "chat.turn"
        )
        assert turn_log["stop_reason"] == "client_disconnected"
        assert turn_log["session_id"] == "disconnect-session"
        assert turn_log["iterations"] == 0
        assert turn_log["timing_counts"]["model.select"] == 1
        assert turn_log["timing_counts"]["system.build"] == 1
        assert turn_log["timing_counts"]["tool_schema.build"] == 1

        disconnect_event = next(
            payload
            for payload in map(json.loads, event_records)
            if payload.get("event") == "chat.client_disconnected"
        )
        assert disconnect_event["session_id"] == "disconnect-session"
        assert disconnect_event["iterations"] == 0
        assert disconnect_event["tool_calls"] == 0

    def test_chat_route_logs_error_on_chat_turn(self, monkeypatch):
        import chat.orchestrator as chatbot
        from chat.schemas import ChatRequest

        class ConnectedRequest:
            async def is_disconnected(self):
                return False

        operation_records = []

        def raise_model_selection(*_args, **_kwargs):
            raise RuntimeError("model selection failed")

        monkeypatch.setattr(chatbot, "get_async_client", lambda: object())
        monkeypatch.setattr(chatbot, "_is_followup", lambda _conversation: True)
        monkeypatch.setattr(chatbot, "_select_chat_model", raise_model_selection)
        monkeypatch.setattr(OPERATION_LOGGER, "info", operation_records.append)

        async def consume_stream():
            response = chatbot.stream_chat(
                ConnectedRequest(),
                ChatRequest(
                    messages=[
                        {
                            "role": "user",
                            "content": "Calculate this household.",
                        }
                    ],
                    session_id="error-session",
                ),
            )
            chunks = []
            async for chunk in response.body_iterator:
                chunks.append(chunk)
            return "".join(
                chunk.decode() if isinstance(chunk, bytes) else chunk
                for chunk in chunks
            )

        events = parse_sse(asyncio.run(consume_stream()))

        assert events == [
            {"type": "error", "content": "model selection failed"},
        ]
        turn_log = next(
            payload
            for payload in map(json.loads, operation_records)
            if payload.get("operation") == "chat.turn"
        )
        assert turn_log["event"] == "operation_failed"
        assert turn_log["stop_reason"] == "error"
        assert turn_log["session_id"] == "error-session"
        assert turn_log["iterations"] == 0
        assert turn_log["tool_calls"] == 0
        assert turn_log["timing_counts"]["model.select"] == 1


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimitConfig:
    """Unit checks on the rate-limit key function and configuration.

    End-to-end limit triggering is not tested here — it requires precise
    timing, fresh limiter state per test, and would couple to the in-memory
    storage backend. The integration tests in conftest.py raise the limits
    far above test workload so the limiter never fires during this suite.
    """

    def _request(self, headers=None, client_host="203.0.113.1"):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
            ],
            "client": (client_host, 12345),
        }
        return Request(scope)

    def test_key_func_uses_user_id_header(self):
        from api.rate_limit import chat_key_func

        req = self._request(headers={"x-user-id": "abc-123"})
        assert chat_key_func(req) == "user:abc-123"

    def test_key_func_falls_back_to_ip(self):
        from api.rate_limit import chat_key_func

        req = self._request(client_host="198.51.100.7")
        assert chat_key_func(req) == "ip:198.51.100.7"

    def test_key_func_user_id_takes_precedence_over_ip(self):
        from api.rate_limit import chat_key_func

        req = self._request(headers={"x-user-id": "u1"}, client_host="198.51.100.7")
        assert chat_key_func(req) == "user:u1"

    def test_empty_user_id_header_falls_back_to_ip(self):
        from api.rate_limit import chat_key_func

        req = self._request(headers={"x-user-id": ""}, client_host="198.51.100.7")
        assert chat_key_func(req) == "ip:198.51.100.7"

    def test_limit_strings_compose_per_min_and_per_hour(self):
        from api.rate_limit import CHAT_USER_LIMIT

        assert "/minute" in CHAT_USER_LIMIT and "/hour" in CHAT_USER_LIMIT

    def test_env_overrides_take_effect_at_import(self):
        # conftest.py sets these to test values; verify rate_limit picked them up.
        import os
        from api.rate_limit import CHAT_PER_MIN, CHAT_PER_HOUR, CHAT_IP_PER_MIN

        assert CHAT_PER_MIN == int(os.environ["RATE_LIMIT_CHAT_PER_MIN"])
        assert CHAT_PER_HOUR == int(os.environ["RATE_LIMIT_CHAT_PER_HOUR"])
        assert CHAT_IP_PER_MIN == int(os.environ["RATE_LIMIT_CHAT_IP_PER_MIN"])

    def test_client_ip_prefers_x_forwarded_for(self):
        # Behind a proxy, request.client.host is the proxy — X-Forwarded-For
        # carries the real client. The limiter must key on the latter.
        from api.rate_limit import client_ip

        req = self._request(
            headers={"x-forwarded-for": "1.2.3.4"}, client_host="10.0.0.1"
        )
        assert client_ip(req) == "1.2.3.4"

    def test_client_ip_takes_first_forwarded_entry(self):
        from api.rate_limit import client_ip

        req = self._request(headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1, 10.0.0.2"})
        assert client_ip(req) == "1.2.3.4"

    def test_client_ip_falls_back_to_socket_peer(self):
        from api.rate_limit import client_ip

        req = self._request(client_host="198.51.100.7")
        assert client_ip(req) == "198.51.100.7"

    def test_chat_key_func_uses_forwarded_ip_for_anonymous(self):
        from api.rate_limit import chat_key_func

        req = self._request(
            headers={"x-forwarded-for": "1.2.3.4"}, client_host="10.0.0.1"
        )
        assert chat_key_func(req) == "ip:1.2.3.4"

    def test_chat_endpoint_exposes_starlette_request_to_slowapi(self):
        """Regression guard for the slowapi wiring.

        slowapi's @limiter.limit grabs the endpoint parameter named
        `request` and requires it to be a starlette Request. If that name
        is given to the Pydantic body instead, slowapi raises and every
        call to /chat/message 500s before the handler runs.
        """
        import inspect
        from starlette.requests import Request as StarletteRequest
        from chat.routes import chat_message

        params = inspect.signature(chat_message).parameters
        assert "request" in params, "endpoint needs a `request` parameter for slowapi"
        assert params["request"].annotation is StarletteRequest, (
            "the `request` parameter must be a starlette Request, not the body model"
        )

    def test_chat_message_does_not_500_from_rate_limit_decorator(self):
        """A single call must stream normally, not 500.

        conftest.py raises the limits far above test workload, so the only
        thing that can fail here is the rate-limit decorator itself — which
        is exactly the regression this guards.
        """
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Say exactly: hi"}],
            },
        ) as r:
            assert r.status_code != 500
