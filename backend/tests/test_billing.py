from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from billing.pricing import calculate_cost_gbp
from billing.intents import build_billing_intent
from billing.processor import BillingIntentProcessor, BillingRecordResult
from billing import credits
from billing import config
from billing import routes
from billing import stripe_integration
from analysis.models import ModelUsageEntry


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

    def rpc(self, name, value):
        query = FakeQuery(self, name)
        query.operation = "rpc"
        query.payload = value
        return query

    def execute(self, query):
        return self._execute(query)


def test_billing_feature_flag_accepts_literal_true(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")

    assert config.billing_enabled() is True


@pytest.mark.parametrize(
    "value", [None, "", "0", "false", "TRUE", "yes", "on", "unexpected"]
)
def test_billing_feature_flag_defaults_to_disabled(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("BILLING_ENABLED", raising=False)
    else:
        monkeypatch.setenv("BILLING_ENABLED", value)

    assert config.billing_enabled() is False


def test_billing_routes_are_hidden_while_disabled(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "false")

    with pytest.raises(routes.HTTPException) as exc_info:
        routes.require_billing_enabled()

    assert exc_info.value.status_code == 404


def test_billing_routes_are_available_when_enabled(monkeypatch):
    monkeypatch.setenv("BILLING_ENABLED", "true")

    assert routes.require_billing_enabled() is None


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


def test_billing_intent_prices_each_actual_model_call_once():
    priced = []
    entries = (
        ModelUsageEntry(
            usage_entry_id="usage-one",
            session_id="session",
            turn_id="turn",
            operation="interpretation",
            model="model-one",
            input_tokens=3,
            output_tokens=1,
        ),
        ModelUsageEntry(
            usage_entry_id="usage-two",
            session_id="session",
            turn_id="turn",
            operation="narration",
            model="model-two",
            input_tokens=7,
            output_tokens=2,
            cache_read_input_tokens=4,
        ),
    )

    def price(**values):
        priced.append(values)
        return 0.1 if values["model"] == "model-one" else 0.25

    intent = build_billing_intent(
        session_id="session",
        turn_id="turn",
        user_id="user",
        usage_entries=entries,
        pricing=price,
    )

    assert intent is not None
    assert [item.model for item in intent.charge_inputs] == [
        "model-one",
        "model-two",
    ]
    assert [item.cost_gbp for item in intent.charge_inputs] == [0.1, 0.25]
    assert priced[1]["cache_read_input_tokens"] == 4


def test_billing_processor_reuses_stored_charge_after_pricing_changes():
    entry = ModelUsageEntry(
        usage_entry_id="usage",
        session_id="session",
        turn_id="turn",
        operation="narration",
        model="model-one",
        input_tokens=10,
        output_tokens=2,
    )
    intent = build_billing_intent(
        session_id="session",
        turn_id="turn",
        user_id="user",
        usage_entries=(entry,),
        pricing=lambda **_values: 0.125,
    )
    assert intent is not None
    recorded_costs = []

    class Store:
        marked = []

        def pending_billing_intents(self, *, user_id):
            return (intent,)

        def mark_billing_recorded(self, session_id, turn_id):
            self.marked.append((session_id, turn_id))
            return True

    def record(stored_intent):
        recorded_costs.append(
            sum(charge.cost_gbp for charge in stored_intent.charge_inputs)
        )
        return BillingRecordResult(recorded=True, duplicate=True)

    store = Store()
    results = BillingIntentProcessor(store=store, recorder=record).process_pending(
        user_id="user"
    )

    assert results[0].duplicate is True
    assert recorded_costs == [0.125]
    assert store.marked == [("session", "turn")]


def test_failed_billing_record_remains_pending():
    entry = ModelUsageEntry(
        usage_entry_id="usage",
        session_id="session",
        turn_id="turn",
        operation="interpretation",
        model="model-one",
    )
    intent = build_billing_intent(
        session_id="session",
        turn_id="turn",
        user_id="user",
        usage_entries=(entry,),
        pricing=lambda **_values: 0,
    )
    assert intent is not None

    class Store:
        marked = []

        def pending_billing_intents(self, *, user_id):
            return (intent,)

        def mark_billing_recorded(self, session_id, turn_id):
            self.marked.append((session_id, turn_id))
            return True

    store = Store()
    result = BillingIntentProcessor(
        store=store,
        recorder=lambda _intent: BillingRecordResult(recorded=False),
    ).process(intent)

    assert result.recorded is False
    assert store.marked == []


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


def test_record_usage_uses_atomic_turn_idempotency(monkeypatch):
    inserted = iter([True, False])

    def execute(query):
        assert query.operation == "rpc"
        return SimpleNamespace(data=next(inserted))

    database = FakeSupabase(execute)
    monkeypatch.setattr(credits, "get_supabase", lambda: database)
    monkeypatch.setattr(credits, "calculate_cost_gbp", lambda **_kwargs: 0.25)

    first = credits.record_usage(
        user_id=None,
        session_id="session-1",
        turn_id="turn-1",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=2,
    )
    duplicate = credits.record_usage(
        user_id=None,
        session_id="session-1",
        turn_id="turn-1",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=2,
    )

    assert first["cost_gbp"] == 0.25
    assert first["duplicate"] is False
    assert duplicate["cost_gbp"] == 0
    assert duplicate["duplicate"] is True
    assert all(
        call.payload["p_turn_id"] == "turn-1" for call in database.calls
    )


def test_record_usage_retry_after_unknown_rpc_result_does_not_charge_twice(
    monkeypatch,
):
    outcomes = iter([RuntimeError("response lost"), False])

    def execute(_query):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(data=outcome)

    database = FakeSupabase(execute)
    monkeypatch.setattr(credits, "get_supabase", lambda: database)
    monkeypatch.setattr(credits, "calculate_cost_gbp", lambda **_kwargs: 0.25)

    uncertain = credits.record_usage(
        user_id=None,
        session_id="session-1",
        turn_id="turn-retry",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=2,
    )
    duplicate = credits.record_usage(
        user_id=None,
        session_id="session-1",
        turn_id="turn-retry",
        model="claude-haiku-4-5",
        input_tokens=10,
        output_tokens=2,
    )

    assert uncertain["cost_gbp"] == 0
    assert duplicate["cost_gbp"] == 0
    assert duplicate["duplicate"] is True


def test_record_usage_prices_each_model_call_and_cache_tokens_separately(
    monkeypatch,
):
    database = FakeSupabase(
        lambda query: SimpleNamespace(data=True)
        if query.operation == "rpc"
        else SimpleNamespace(data=[])
    )
    priced = []

    def price(**usage):
        priced.append(usage)
        return 0.1 if usage["model"] == "model-one" else 0.25

    monkeypatch.setattr(credits, "get_supabase", lambda: database)
    monkeypatch.setattr(credits, "calculate_cost_gbp", price)

    result = credits.record_usage(
        user_id=None,
        session_id="session-mixed",
        turn_id="turn-mixed",
        model="ignored-aggregate-model",
        input_tokens=13,
        output_tokens=5,
        cache_creation_input_tokens=7,
        cache_read_input_tokens=11,
        usage_entries=[
            {
                "model": "model-one",
                "input_tokens": 3,
                "output_tokens": 1,
                "cache_creation_input_tokens": 2,
                "cache_read_input_tokens": 4,
            },
            {
                "model": "model-two",
                "input_tokens": 10,
                "output_tokens": 4,
                "cache_creation_input_tokens": 5,
                "cache_read_input_tokens": 7,
            },
        ],
    )

    assert [call["model"] for call in priced] == ["model-one", "model-two"]
    assert priced[0]["cache_read_input_tokens"] == 4
    assert result["cost_gbp"] == pytest.approx(0.35)
    rpc = database.calls[0]
    assert rpc.payload["p_model"] == "mixed"
    assert rpc.payload["p_cost_gbp"] == pytest.approx(0.35)
    assert result["recorded"] is True


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
