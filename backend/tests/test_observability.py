import json
import os
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from policyengine_observability import REQUEST_ID_HEADER, segment
from policyengine_observability.runtime import OPERATION_LOGGER, REQUEST_LOGGER

from observability.fastapi import UK_CHAT_METRIC_ATTRIBUTE_KEYS
from observability.fastapi import configure_process_observability
from observability.fastapi import init_observability
from observability.segments import SegmentName
from observability.segments import coerce_segment_name


@pytest.fixture(autouse=True)
def reset_process_metadata(monkeypatch):
    import observability.fastapi as uk_chat_observability

    monkeypatch.setattr(uk_chat_observability, "_PROCESS_METADATA", {})


def _observed_app() -> FastAPI:
    app = FastAPI()
    init_observability(app, service_role="test_api")

    @app.get("/ok")
    def ok():
        with segment(SegmentName.MODEL_STREAM, model="test-model"):
            return {"status": "ok"}

    return app


def test_request_log_contains_metadata_and_timings(monkeypatch):
    records = []
    monkeypatch.setattr(REQUEST_LOGGER, "info", records.append)

    response = TestClient(_observed_app()).get(
        "/ok?secret=value",
        headers={
            "X-Forwarded-For": "203.0.113.1, 10.0.0.2",
            "User-Agent": "test-agent",
        },
    )

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]
    assert len(records) == 1
    payload = json.loads(records[0])
    assert payload["event"] == "http_request_completed"
    assert payload["service_name"] == "policyengine-uk-chat"
    assert payload["service_role"] == "test_api"
    assert payload["route"] == "/ok"
    assert payload["query_keys"] == ["secret"]
    assert "value" not in records[0]
    assert payload["client_ip"] == "203.0.113.1"
    assert payload["timings_ms"]["model.stream"] >= 0
    assert payload["timing_counts"]["model.stream"] == 1
    assert payload["platform"] == "local"
    assert payload["runtime_role"] == "test_api"


def test_request_log_contains_modal_metadata(monkeypatch):
    monkeypatch.setenv("MODAL_ENVIRONMENT", "preview")
    configure_process_observability(
        platform="modal",
        service_role="api",
        runtime_role="modal_web",
        modal_app_name="peukchat-preview",
        modal_function_name="web",
    )

    records = []
    monkeypatch.setattr(REQUEST_LOGGER, "info", records.append)

    response = TestClient(_observed_app()).get("/ok")

    assert response.status_code == 200
    payload = json.loads(records[0])
    assert payload["platform"] == "modal"
    assert payload["runtime_role"] == "modal_web"
    assert payload["modal_environment"] == "preview"
    assert payload["modal_app_name"] == "peukchat-preview"
    assert payload["modal_function_name"] == "web"


def test_fastapi_otel_instrumentation_is_disabled(monkeypatch):
    # Regression for #167: opentelemetry-instrumentation-fastapi < 0.64b0 raises
    # AttributeError on FastAPI >= 0.137 include_router routing (`_IncludedRouter`
    # has no `.path`), which 500'd every CORS preflight in production. We keep the
    # OTel FastAPI auto-instrumentation off (overridable via the env var) until
    # the pinned upstream fix ships. This guarantee is version-independent.
    monkeypatch.delenv("OBSERVABILITY_INSTRUMENT_FASTAPI", raising=False)
    app = FastAPI()

    runtime = init_observability(app, service_role="test_api")

    assert runtime.config.instrument_fastapi is False
    assert getattr(app, "_is_instrumented_by_opentelemetry", False) is False


def test_init_observability_is_idempotent():
    app = FastAPI()

    first = init_observability(app, service_role="test_api")
    second = init_observability(app, service_role="test_api")

    assert second is first


def test_segment_registry_falls_back_for_unknown_values():
    assert coerce_segment_name(SegmentName.TOOL_EXECUTE) == (
        "tool.execute",
        True,
    )
    assert coerce_segment_name("custom.segment") == ("custom.segment", False)


def test_metric_attribute_keys_include_uk_chat_dimensions():
    app = FastAPI()
    runtime = init_observability(app, service_role="test_api")

    for key in UK_CHAT_METRIC_ATTRIBUTE_KEYS:
        assert key in runtime.config.metric_attribute_keys


def test_process_observability_does_not_mutate_env(monkeypatch):
    for key in (
        "OBSERVABILITY_PLATFORM",
        "OBSERVABILITY_SERVICE_ROLE",
        "OBSERVABILITY_RUNTIME_ROLE",
        "OBSERVABILITY_MODAL_APP_NAME",
        "OBSERVABILITY_MODAL_FUNCTION_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    configure_process_observability(
        platform="modal",
        service_role="api",
        runtime_role="modal_web",
        modal_app_name="peukchat-preview",
        modal_function_name="web",
    )

    for key in (
        "OBSERVABILITY_PLATFORM",
        "OBSERVABILITY_SERVICE_ROLE",
        "OBSERVABILITY_RUNTIME_ROLE",
        "OBSERVABILITY_MODAL_APP_NAME",
        "OBSERVABILITY_MODAL_FUNCTION_NAME",
    ):
        assert key not in os.environ


def test_title_generation_logs_standalone_segment(monkeypatch):
    import chat.titles as titles
    from chat.schemas import TitleRequest

    class FakeMessages:
        def create(self, **kwargs):
            assert kwargs["model"] == titles.TITLE_MODEL
            assert kwargs["max_tokens"] == 32
            return SimpleNamespace(content=[SimpleNamespace(text="Tax credits")])

    operation_records = []
    monkeypatch.setattr(OPERATION_LOGGER, "info", operation_records.append)
    monkeypatch.setattr(
        titles,
        "get_sync_client",
        lambda: SimpleNamespace(messages=FakeMessages()),
    )

    response = titles.make_title(TitleRequest(first_user_message="Can you title this?"))

    assert response == {"title": "Tax credits"}
    payload = next(
        payload
        for payload in map(json.loads, operation_records)
        if payload.get("operation") == "title.generate"
    )
    assert payload["event"] == "operation_completed"
    assert payload["model"] == titles.TITLE_MODEL
    assert payload["timings_ms"]["title.generate"] >= 0
    assert payload["timing_counts"]["title.generate"] == 1
