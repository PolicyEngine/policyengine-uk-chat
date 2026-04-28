"""
Integration tests for the FastAPI endpoints.
Tests the HTTP layer — chat streaming, conversations CRUD, title generation.
Run inside the backend container: pytest tests/
"""

import json
import pytest
from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health(self):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestChatBackends:
    def test_lists_backends(self):
        r = client.get("/chat/backends")
        assert r.status_code == 200
        data = r.json()
        assert data["default"] == "uk_compiled"
        assert "uk_compiled" in data["backends"]
        assert "uk_python" in data["backends"]


# ---------------------------------------------------------------------------
# Conversations CRUD
# ---------------------------------------------------------------------------

class TestConversations:
    def _save(self, session_id="test-session-1", title="Test", messages=None, user_id=None):
        return client.post("/conversations", json={
            "session_id": session_id,
            "title": title,
            "messages": messages or [{"role": "user", "content": "hello"}],
            "user_id": user_id,
        })

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
            {"role": "assistant", "content": "hi there", "events": [{"type": "text", "content": "hi there"}]},
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
                            "result_summary": "{\"result\": 2, \"output\": \"done\"}",
                        },
                    }
                ],
            },
        ]
        save_r = self._save(session_id="report-test-1", messages=messages, user_id="user-1")
        conv_id = save_r.json()["id"]
        report_r = client.post(
            f"/conversations/{conv_id}/report",
            json={"user_id": "user-1", "app_url": "https://example.com"},
        )
        assert report_r.status_code == 200
        issue_body = report_r.json()["issue_body"]
        assert "result = 1 + 1" in issue_body
        assert "\"result\": 2" in issue_body


# ---------------------------------------------------------------------------
# Title generation
# ---------------------------------------------------------------------------

class TestTitle:
    def test_generates_title(self):
        r = client.post("/chat/title", json={
            "first_user_message": "What is the personal allowance for 2025?",
        })
        assert r.status_code == 200
        title = r.json()["title"]
        assert isinstance(title, str)
        assert len(title) > 0
        assert len(title) < 100

    def test_title_with_assistant_message(self):
        r = client.post("/chat/title", json={
            "first_user_message": "How much child benefit do I get for two children?",
            "first_assistant_message": "Child benefit for two children is £XXX per week.",
        })
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


class TestChatMessage:
    def test_simple_chat_returns_sse(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "Say exactly: hello"}],
        }) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            text = r.read().decode()
        events = parse_sse(text)
        assert len(events) > 0

    def test_done_event_present(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "Reply with one word: yes"}],
        }) as r:
            text = r.read().decode()
        events = parse_sse(text)
        types = [e["type"] for e in events]
        assert "done" in types

    def test_session_id_returned(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "Reply with one word: yes"}],
        }) as r:
            text = r.read().decode()
        events = parse_sse(text)
        done = next(e for e in events if e["type"] == "done")
        assert "session_id" in done
        assert len(done["session_id"]) > 0

    def test_provided_session_id_echoed(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "Reply with one word: yes"}],
            "session_id": "my-fixed-session",
        }) as r:
            text = r.read().decode()
        events = parse_sse(text)
        done = next(e for e in events if e["type"] == "done")
        assert done["session_id"] == "my-fixed-session"

    def test_chunk_events_contain_text(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "Write a single sentence about the UK."}],
        }) as r:
            text = r.read().decode()
        events = parse_sse(text)
        chunks = [e for e in events if e["type"] == "chunk"]
        assert len(chunks) > 0
        full_text = "".join(e["content"] for e in chunks)
        assert len(full_text) > 5

    def test_tool_use_for_simulation_query(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "What is the current personal allowance? Use get_baseline_parameters."}],
        }) as r:
            text = r.read().decode()
        events = parse_sse(text)
        types = [e["type"] for e in events]
        assert "tool_start" in types or "tool_use" in types

    def test_no_error_event_on_simple_query(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "Reply with one word: yes"}],
        }) as r:
            text = r.read().decode()
        events = parse_sse(text)
        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 0

    def test_usage_in_done_event(self):
        with client.stream("POST", "/chat/message", json={
            "messages": [{"role": "user", "content": "Reply with one word: yes"}],
        }) as r:
            text = r.read().decode()
        events = parse_sse(text)
        done = next(e for e in events if e["type"] == "done")
        assert "usage" in done
        assert done["usage"]["input_tokens"] > 0
