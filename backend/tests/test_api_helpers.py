import asyncio
from types import SimpleNamespace

import pytest

from api.errors import NaNSafeJSONResponse, rate_limit_handler
from api import rate_limit


def _request(headers=None):
    return SimpleNamespace(headers=headers or {}, client=SimpleNamespace(host="127.0.0.1"))


def test_nan_safe_response_converts_non_finite_values_recursively():
    response = NaNSafeJSONResponse(
        {
            "nan": float("nan"),
            "nested": [float("inf"), float("-inf"), 1.5],
            "label": "ok",
        }
    )

    assert response.body == b'{"nan": null, "nested": [null, null, 1.5], "label": "ok"}'


def test_rate_limit_handler_uses_limit_granularity():
    exc = SimpleNamespace(
        limit=SimpleNamespace(limit=SimpleNamespace(GRANULARITY=SimpleNamespace(seconds=3600))),
        detail="hourly limit",
    )

    response = asyncio.run(rate_limit_handler(_request(), exc))

    assert response.status_code == 429
    assert response.headers["retry-after"] == "3600"
    assert b'"retry_after_seconds":3600' in response.body
    assert b'"hourly limit"' in response.body


@pytest.mark.parametrize(
    "limit",
    [SimpleNamespace(), SimpleNamespace(limit=SimpleNamespace(GRANULARITY=None))],
)
def test_rate_limit_handler_falls_back_to_one_minute(limit):
    response = asyncio.run(
        rate_limit_handler(_request(), SimpleNamespace(limit=limit, detail="limited"))
    )

    assert response.headers["retry-after"] == "60"


def test_client_ip_prefers_first_forwarded_address(monkeypatch):
    monkeypatch.setattr(rate_limit, "get_remote_address", lambda _request: "socket-ip")

    assert rate_limit.client_ip(
        _request({"x-forwarded-for": " 203.0.113.10, 10.0.0.1"})
    ) == "203.0.113.10"
    assert rate_limit.client_ip(_request({"x-forwarded-for": " , 10.0.0.1"})) == "socket-ip"
    assert rate_limit.client_ip(_request()) == "socket-ip"


def test_chat_key_uses_user_id_then_ip(monkeypatch):
    monkeypatch.setattr(rate_limit, "client_ip", lambda _request: "203.0.113.10")

    assert rate_limit.chat_key_func(_request({"x-user-id": "user-1"})) == "user:user-1"
    assert rate_limit.chat_key_func(_request({"x-user-id": ""})) == "ip:203.0.113.10"
