"""
Integration tests for the FastAPI endpoints.
Tests the HTTP layer — chat streaming, conversations CRUD, title generation.
Run inside the backend container: pytest tests/
"""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
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
# CORS preflight
# ---------------------------------------------------------------------------


class TestCorsPreflight:
    """Regression guard for #167 against the real app's middleware stack.

    Every browser request from the Vercel frontend is preceded by a CORS
    preflight OPTIONS. opentelemetry-instrumentation-fastapi < 0.64b0 raised
    AttributeError on FastAPI >= 0.137 include_router routing (`_IncludedRouter`
    has no `.path`), so every preflight to a router-mounted route 500'd and the
    app was unusable. This exercises the actual wired app (CORS + observability
    middleware), which a synthetic stand-in app would not.
    """

    @pytest.mark.parametrize(
        "path, request_method",
        [
            ("/conversations", "POST"),
            ("/chat/message", "POST"),
        ],
    )
    def test_preflight_does_not_500(self, path, request_method):
        r = client.options(
            path,
            headers={
                "Origin": "https://policyengine-uk-chat.vercel.app",
                "Access-Control-Request-Method": request_method,
            },
        )
        assert r.status_code != 500
        assert r.status_code == 200
        assert "access-control-allow-origin" in r.headers


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("isolated_conversations_table")
class TestConversations:
    def _save(
        self,
        session_id="test-session-1",
        title="Test",
        messages=None,
        user_id=None,
        user_email=None,
    ):
        return client.post(
            "/conversations",
            json={
                "session_id": session_id,
                "title": title,
                "messages": messages or [{"role": "user", "content": "hello"}],
                "user_id": user_id,
                "user_email": user_email,
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

    def test_concurrent_saves_create_one_conversation(self):
        barrier = threading.Barrier(2)

        def save(title):
            barrier.wait()
            return self._save(
                session_id="concurrent-upsert-1",
                title=title,
                user_id="user-1",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(save, ["First", "Second"]))

        assert all(response.status_code == 200 for response in responses)
        ids = {response.json()["id"] for response in responses}
        assert len(ids) == 1

        listed = client.get("/conversations", params={"user_id": "user-1"})
        matching = [
            conversation
            for conversation in listed.json()
            if conversation["session_id"] == "concurrent-upsert-1"
        ]
        assert len(matching) == 1

    def test_search_matches_message_content_and_scopes_results(self):
        self._save(
            session_id="search-owned-1",
            title="A generic question",
            user_id="user-1",
            messages=[
                {"role": "user", "content": "How does child benefit work?"},
                {"role": "assistant", "content": "It is paid every four weeks."},
            ],
        )
        self._save(
            session_id="search-other-1",
            title="Child benefit for another user",
            user_id="user-2",
        )

        response = client.get(
            "/conversations/search",
            params={"user_id": "user-1", "query": "child benefit"},
        )

        assert response.status_code == 200
        assert [result["session_id"] for result in response.json()] == [
            "search-owned-1"
        ]
        assert "child benefit" in response.json()[0]["snippet"].lower()

    def test_search_matches_titles(self):
        self._save(
            session_id="search-title-1",
            title="Scottish income tax",
            user_id="user-1",
        )

        response = client.get(
            "/conversations/search",
            params={"user_id": "user-1", "query": "SCOTTISH"},
        )

        assert response.status_code == 200
        assert response.json()[0]["session_id"] == "search-title-1"

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
                            "tool_name": "generate_chart",
                            "tool_id": "tool-1",
                            "status": "success",
                            "input": {"chart_kind": "budget_waterfall"},
                            "result_summary": '{"status": "success"}',
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
        assert "budget_waterfall" in issue_body
        assert '"status": "success"' in issue_body

    def test_shared_conversation_does_not_expose_author_email(self):
        email = "private-author@example.com"
        save_r = self._save(
            session_id="share-privacy-1", user_id="user-1", user_email=email
        )
        conv_id = save_r.json()["id"]
        share_r = client.post(
            f"/conversations/{conv_id}/share", params={"user_id": "user-1"}
        )
        assert share_r.status_code == 200
        token = share_r.json()["share_token"]
        shared_r = client.get(f"/conversations/shared/{token}")
        assert shared_r.status_code == 200
        payload = shared_r.json()
        assert "author" not in payload
        assert email not in shared_r.text
        # The shared payload should carry only content, not account identity.
        assert set(payload) == {"title", "messages", "created_at"}

    def test_report_body_does_not_expose_reporter_email(self):
        email = "private-reporter@example.com"
        save_r = self._save(
            session_id="report-privacy-1", user_id="user-1", user_email=email
        )
        conv_id = save_r.json()["id"]
        report_r = client.post(
            f"/conversations/{conv_id}/report",
            json={"user_id": "user-1", "app_url": "https://example.com"},
        )
        assert report_r.status_code == 200
        data = report_r.json()
        assert email not in data["issue_body"]
        assert email not in data["issue_title"]
        assert email not in data["issue_url"]


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

    def test_invocation_activity_for_policy_question(self):
        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": "How is the current personal allowance determined?",
                    }
                ],
            },
        ) as r:
            text = r.read().decode()
        events = parse_sse(text)
        types = [e["type"] for e in events]
        assert "invocation_activity" in types

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

    def test_chat_message_does_not_500_from_rate_limit_decorator(self, monkeypatch):
        """A single call must stream normally, not 500.

        conftest.py raises the limits far above test workload, so the only
        thing that can fail here is the rate-limit decorator itself — which
        is exactly the regression this guards.
        """
        async def fake_start(*_args, **_kwargs):
            async def stream():
                yield 'data: {"type":"done","content":"hi"}\n\n'

            return stream()

        monkeypatch.setattr("chat.routes.start_public_chat", fake_start)

        with client.stream(
            "POST",
            "/chat/message",
            json={
                "messages": [{"role": "user", "content": "Say exactly: hi"}],
            },
        ) as r:
            assert r.status_code != 500
