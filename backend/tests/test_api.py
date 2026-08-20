"""HTTP contract tests for chat and conversation routes."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from analysis.persistence import SqlAnalysisStore, AnalysisWorkflowRow
from analysis.store import CreateSessionCommand
from api.main import app


client = TestClient(app)


def _parse_sse(response_text: str) -> list[dict]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in response_text.splitlines()
        if line.startswith("data: ")
    ]


def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize(
    "path,request_method",
    [("/conversations", "POST"), ("/chat/message", "POST")],
)
def test_cors_preflight_uses_real_middleware(path, request_method):
    response = client.options(
        path,
        headers={
            "Origin": "https://policyengine-uk-chat.vercel.app",
            "Access-Control-Request-Method": request_method,
        },
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" in response.headers


@pytest.mark.usefixtures("isolated_conversations_table")
class TestConversations:
    def _save(
        self,
        *,
        session_id="conversation-session",
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
                "messages": messages
                or [{"role": "user", "content": "hello"}],
                "user_id": user_id,
                "user_email": user_email,
            },
        )

    def test_save_list_get_update_and_delete(self):
        created = self._save(
            session_id="crud-session",
            title="Original",
            user_id="user-1",
        )
        assert created.status_code == 200
        conversation_id = created.json()["id"]

        updated = self._save(
            session_id="crud-session",
            title="Updated",
            user_id="user-1",
        )
        assert updated.json()["id"] == conversation_id
        assert updated.json()["title"] == "Updated"

        listed = client.get("/conversations", params={"user_id": "user-1"})
        assert [item["session_id"] for item in listed.json()] == [
            "crud-session"
        ]
        loaded = client.get(f"/conversations/{conversation_id}")
        assert loaded.status_code == 200
        assert loaded.json()["messages"][0]["content"] == "hello"

        deleted = client.delete(f"/conversations/{conversation_id}")
        assert deleted.status_code == 204
        assert client.get(f"/conversations/{conversation_id}").status_code == 404

    def test_concurrent_saves_create_one_row(self):
        barrier = threading.Barrier(2)

        def save(title):
            barrier.wait()
            return self._save(
                session_id="concurrent-session",
                title=title,
                user_id="user-1",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            responses = list(executor.map(save, ["First", "Second"]))

        assert all(response.status_code == 200 for response in responses)
        assert len({response.json()["id"] for response in responses}) == 1

    def test_search_is_scoped_and_matches_message_content(self):
        self._save(
            session_id="owned-session",
            title="General",
            user_id="user-1",
            messages=[
                {"role": "user", "content": "How does child benefit work?"}
            ],
        )
        self._save(
            session_id="other-session",
            title="Child benefit",
            user_id="user-2",
        )

        response = client.get(
            "/conversations/search",
            params={"user_id": "user-1", "query": "child benefit"},
        )

        assert response.status_code == 200
        assert [item["session_id"] for item in response.json()] == [
            "owned-session"
        ]

    def test_share_and_report_omit_account_and_analysis_state(self):
        email = "private@example.com"
        created = self._save(
            session_id="private-session",
            user_id="user-1",
            user_email=email,
        )
        conversation_id = created.json()["id"]
        SqlAnalysisStore().create_session(
            CreateSessionCommand(session_id="private-session")
        )

        shared = client.post(
            f"/conversations/{conversation_id}/share",
            params={"user_id": "user-1"},
        )
        public = client.get(
            f"/conversations/shared/{shared.json()['share_token']}"
        )
        report = client.post(
            f"/conversations/{conversation_id}/report",
            json={"user_id": "user-1", "app_url": "https://example.com"},
        )

        assert public.status_code == 200
        assert set(public.json()) == {"title", "messages", "created_at"}
        assert email not in public.text
        assert "analysis" not in public.text.lower()
        assert report.status_code == 200
        assert email not in report.text
        assert "analysis_workflows" not in report.text

    def test_delete_removes_internal_workflow_records(self):
        created = self._save(session_id="delete-state-session")
        conversation_id = created.json()["id"]
        store = SqlAnalysisStore()
        store.create_session(CreateSessionCommand(session_id="delete-state-session"))

        response = client.delete(f"/conversations/{conversation_id}")

        assert response.status_code == 204
        with Session(store.engine) as session:
            assert session.get(AnalysisWorkflowRow, "delete-state-session") is None


def test_chat_route_forwards_stable_turn_identifier(monkeypatch):
    from chat import routes

    captured = {}

    async def fake_start(chat_request, *, is_cancelled):
        captured["request"] = chat_request

        async def stream():
            yield 'data: {"type": "done", "turn_id": "turn-123"}\n\n'

        return stream()

    monkeypatch.setattr(routes, "start_public_chat", fake_start)

    with client.stream(
        "POST",
        "/chat/message",
        json={
            "messages": [{"role": "user", "content": "Explain income tax."}],
            "session_id": "chat-session",
            "turn_id": "turn-123",
        },
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert captured["request"].turn_id == "turn-123"
    assert _parse_sse(body)[0]["turn_id"] == "turn-123"


def test_chat_route_returns_sanitized_invalid_request(monkeypatch):
    from chat import routes
    from chat.turn_input import InvalidChatRequest

    async def reject(*_args, **_kwargs):
        raise InvalidChatRequest("A user message is required")

    monkeypatch.setattr(routes, "start_public_chat", reject)

    response = client.post(
        "/chat/message",
        json={"messages": [{"role": "assistant", "content": "hello"}]},
    )

    assert response.status_code == 400
    assert response.json() == {"error": "A user message is required"}


class TestRateLimitConfig:
    def _request(self, headers=None, client_host="203.0.113.1"):
        from starlette.requests import Request

        return Request(
            {
                "type": "http",
                "headers": [
                    (key.lower().encode(), value.encode())
                    for key, value in (headers or {}).items()
                ],
                "client": (client_host, 12345),
            }
        )

    def test_key_uses_user_identifier_then_client_address(self):
        from api.rate_limit import chat_key_func

        assert chat_key_func(
            self._request(headers={"x-user-id": "user-1"})
        ) == "user:user-1"
        assert chat_key_func(
            self._request(client_host="198.51.100.7")
        ) == "ip:198.51.100.7"

    def test_forwarded_address_takes_precedence(self):
        from api.rate_limit import client_ip

        request = self._request(
            headers={"x-forwarded-for": "1.2.3.4, 10.0.0.1"},
            client_host="10.0.0.2",
        )
        assert client_ip(request) == "1.2.3.4"

    def test_limits_use_test_environment_overrides(self):
        from api.rate_limit import CHAT_IP_PER_MIN, CHAT_PER_HOUR, CHAT_PER_MIN

        assert CHAT_PER_MIN == int(os.environ["RATE_LIMIT_CHAT_PER_MIN"])
        assert CHAT_PER_HOUR == int(os.environ["RATE_LIMIT_CHAT_PER_HOUR"])
        assert CHAT_IP_PER_MIN == int(os.environ["RATE_LIMIT_CHAT_IP_PER_MIN"])
