from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from billing.pricing import calculate_cost_gbp
from billing import credits
from billing import routes
from billing import stripe_integration


class FakeQuery:
    def __init__(self, database, table):
        self.database = database
        self.table = table
        self.operation = None
        self.payload = None
        self.filters = []
        self.ordering = None
        self.row_limit = None

    def select(self, value):
        self.operation = "select"
        self.payload = value
        return self

    def insert(self, value):
        self.operation = "insert"
        self.payload = value
        return self

    def update(self, value):
        self.operation = "update"
        self.payload = value
        return self

    def eq(self, field, value):
        self.filters.append((field, value))
        return self

    def order(self, field, *, desc=False):
        self.ordering = (field, desc)
        return self

    def limit(self, value):
        self.row_limit = value
        return self

    def execute(self):
        self.database.calls.append(self)
        return self.database.execute(self)


class FakeSupabase:
    def __init__(self, execute=None):
        self.calls = []
        self._execute = execute or (lambda _query: SimpleNamespace(data=[]))

    def table(self, name):
        return FakeQuery(self, name)

    def execute(self, query):
        return self._execute(query)


def test_haiku_is_cheaper_than_sonnet_for_same_usage():
    usage = {
        "input_tokens": 50_000,
        "output_tokens": 2_000,
        "cache_creation_input_tokens": 10_000,
        "cache_read_input_tokens": 10_000,
    }
    haiku_cost = calculate_cost_gbp(model="claude-haiku-4-5", **usage)
    sonnet_cost = calculate_cost_gbp(model="claude-sonnet-4-6", **usage)
    assert haiku_cost < sonnet_cost


def test_cache_tokens_contribute_to_cost():
    baseline = calculate_cost_gbp(
        model="claude-haiku-4-5",
        input_tokens=10_000,
        output_tokens=500,
    )
    with_cache = calculate_cost_gbp(
        model="claude-haiku-4-5",
        input_tokens=10_000,
        output_tokens=500,
        cache_creation_input_tokens=5_000,
        cache_read_input_tokens=5_000,
    )
    assert with_cache > baseline


def test_get_supabase_requires_configuration_and_caches_client(monkeypatch):
    monkeypatch.setattr(credits, "_supabase", None)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

    with pytest.raises(RuntimeError, match="SUPABASE_URL"):
        credits.get_supabase()

    client = object()
    monkeypatch.setenv("SUPABASE_URL", "https://example.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "secret")
    monkeypatch.setattr(credits, "create_client", lambda url, key: client)

    assert credits.get_supabase() is client
    assert credits.get_supabase() is client


def test_get_or_create_credits_creates_missing_row(monkeypatch):
    def execute(query):
        if query.operation == "select":
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[query.payload])

    database = FakeSupabase(execute)
    monkeypatch.setattr(credits, "get_supabase", lambda: database)

    result = credits.get_or_create_credits("user-1")

    assert result["user_id"] == "user-1"
    assert result["free_tier_used_gbp"] == 0
    assert [query.operation for query in database.calls] == ["select", "insert"]


def test_get_or_create_credits_returns_default_when_insert_fails(monkeypatch):
    def execute(query):
        if query.operation == "insert":
            raise RuntimeError("auth row not ready")
        return SimpleNamespace(data=[])

    monkeypatch.setattr(credits, "get_supabase", lambda: FakeSupabase(execute))

    result = credits.get_or_create_credits("new-user")

    assert result["user_id"] == "new-user"
    assert result["balance_gbp"] == 0


def test_get_or_create_credits_resets_expired_free_tier(monkeypatch):
    existing = {
        "user_id": "user-1",
        "balance_gbp": 3,
        "free_tier_used_gbp": 4,
        "free_tier_reset_at": "2020-01-01T00:00:00+00:00",
    }

    def execute(query):
        return SimpleNamespace(data=[existing])

    database = FakeSupabase(execute)
    monkeypatch.setattr(credits, "get_supabase", lambda: database)

    result = credits.get_or_create_credits("user-1")

    assert result["free_tier_used_gbp"] == 0
    update = next(query for query in database.calls if query.operation == "update")
    assert update.payload["free_tier_used_gbp"] == 0
    assert update.filters == [("user_id", "user-1")]


def test_balance_helpers_include_free_tier_and_paid_balance(monkeypatch):
    row = {"balance_gbp": "2.50", "free_tier_used_gbp": "1.25"}
    monkeypatch.setattr(credits, "get_or_create_credits", lambda _user_id: row)

    has_credit, returned_row = credits.check_balance("user-1")
    summary = credits.get_balance_summary("user-1")

    assert has_credit is True
    assert returned_row is row
    assert summary == {
        "balance_gbp": 2.5,
        "free_tier_used_gbp": 1.25,
        "free_tier_remaining_gbp": 3.75,
        "total_available_gbp": 6.25,
    }


def test_record_usage_for_anonymous_user_tolerates_insert_failure(monkeypatch):
    database = FakeSupabase(lambda _query: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(credits, "get_supabase", lambda: database)
    monkeypatch.setattr(credits, "calculate_cost_gbp", lambda **_kwargs: 0.25)

    result = credits.record_usage(
        user_id=None,
        session_id="session-1",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=2,
    )

    assert result == {
        "cost_gbp": 0.25,
        "model": "claude-haiku-4-5",
        "balance": None,
    }


@pytest.mark.parametrize(
    ("cost", "expected_update"),
    [
        (1.0, {"free_tier_used_gbp": 5.0}),
        (3.0, {"free_tier_used_gbp": 5.0, "balance_gbp": 8.0}),
    ],
)
def test_record_usage_deducts_free_credit_before_balance(
    monkeypatch, cost, expected_update
):
    database = FakeSupabase(lambda _query: SimpleNamespace(data=[]))
    credit_row = {"balance_gbp": 10.0, "free_tier_used_gbp": 4.0}
    monkeypatch.setattr(credits, "get_supabase", lambda: database)
    monkeypatch.setattr(credits, "calculate_cost_gbp", lambda **_kwargs: cost)
    monkeypatch.setattr(credits, "get_or_create_credits", lambda _user_id: credit_row)
    monkeypatch.setattr(
        credits,
        "get_balance_summary",
        lambda _user_id: {"total_available_gbp": 12.0 - cost},
    )

    result = credits.record_usage(
        user_id="user-1",
        session_id="session-1",
        model=None,
        input_tokens=10,
        output_tokens=2,
    )

    update = next(query for query in database.calls if query.operation == "update")
    assert update.payload == expected_update
    assert result["balance"]["total_available_gbp"] == 12.0 - cost


def test_public_base_url_prefers_explicit_url_and_falls_back_to_first_hostname(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://chat.example/")
    monkeypatch.setenv("HOSTNAMES", "https://ignored.example,https://second.example")
    assert stripe_integration._get_public_base_url() == "https://chat.example"

    monkeypatch.setenv("PUBLIC_BASE_URL", "  ")
    assert stripe_integration._get_public_base_url() == "https://ignored.example"


def test_checkout_requires_stripe_configuration(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)

    with pytest.raises(stripe_integration.HTTPException) as exc_info:
        stripe_integration.create_checkout_session(
            stripe_integration.CheckoutRequest(user_id="user-1")
        )

    assert exc_info.value.status_code == 500


def test_checkout_builds_stripe_session(monkeypatch):
    calls = []
    monkeypatch.setenv("STRIPE_SECRET_KEY", "stripe-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://chat.example/")
    monkeypatch.setattr(
        stripe_integration.stripe.checkout.Session,
        "create",
        lambda **kwargs: calls.append(kwargs) or SimpleNamespace(url="https://checkout.test"),
    )

    url = stripe_integration.create_checkout_session(
        stripe_integration.CheckoutRequest(user_id="user-1", amount_gbp=7.5)
    )

    assert url == "https://checkout.test"
    assert calls[0]["line_items"][0]["price_data"]["unit_amount"] == 750
    assert calls[0]["metadata"] == {"user_id": "user-1", "amount_gbp": "7.5"}
    assert calls[0]["success_url"] == "https://chat.example?topup=success"


def test_webhook_rejects_invalid_signature(monkeypatch):
    monkeypatch.setattr(
        stripe_integration.stripe.Webhook,
        "construct_event",
        lambda *_args: (_ for _ in ()).throw(ValueError("bad signature")),
    )

    with pytest.raises(stripe_integration.HTTPException) as exc_info:
        stripe_integration.apply_webhook(b"payload", "signature")

    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "event",
    [
        {"type": "payment_intent.created", "data": {"object": {}}},
        {
            "type": "checkout.session.completed",
            "data": {"object": {"metadata": {"amount_gbp": "5"}}},
        },
    ],
)
def test_webhook_ignores_irrelevant_or_incomplete_events(monkeypatch, event):
    monkeypatch.setattr(
        stripe_integration.stripe.Webhook,
        "construct_event",
        lambda *_args: event,
    )
    monkeypatch.setattr(
        stripe_integration,
        "get_or_create_credits",
        lambda _user_id: (_ for _ in ()).throw(AssertionError("must not credit")),
    )

    assert stripe_integration.apply_webhook(b"payload", "signature") is None


def test_webhook_adds_checkout_amount_to_balance(monkeypatch):
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "metadata": {"user_id": "user-1", "amount_gbp": "5.5"}
            }
        },
    }
    database = FakeSupabase(lambda _query: SimpleNamespace(data=[]))
    monkeypatch.setattr(
        stripe_integration.stripe.Webhook,
        "construct_event",
        lambda *_args: event,
    )
    monkeypatch.setattr(
        stripe_integration,
        "get_or_create_credits",
        lambda _user_id: {"balance_gbp": 2.0},
    )
    monkeypatch.setattr(stripe_integration, "get_supabase", lambda: database)

    stripe_integration.apply_webhook(b"payload", "signature")

    update = database.calls[0]
    assert update.payload == {"balance_gbp": 7.5}
    assert update.filters == [("user_id", "user-1")]


def test_billing_route_helpers_delegate_to_services(monkeypatch):
    database = FakeSupabase(
        lambda query: SimpleNamespace(data=[{"id": 1, "table": query.table}])
    )
    monkeypatch.setattr(routes, "get_supabase", lambda: database)
    monkeypatch.setattr(routes, "get_balance_summary", lambda user_id: {"user": user_id})
    monkeypatch.setattr(routes, "create_checkout_session", lambda request: request.user_id)

    assert routes.get_balance("user-1") == {"user": "user-1"}
    assert routes.get_usage("user-1", limit=3) == [{"id": 1, "table": "token_usage"}]
    assert database.calls[0].ordering == ("created_at", True)
    assert database.calls[0].row_limit == 3
    assert routes.create_checkout(
        stripe_integration.CheckoutRequest(user_id="user-1")
    ) == {"url": "user-1"}
