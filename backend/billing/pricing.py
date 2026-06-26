"""Token pricing table and per-call cost calculation."""

import os

MARKUP_RATE = float(os.environ.get("BILLING_MARKUP_RATE", "0.0"))
USD_TO_GBP = float(os.environ.get("BILLING_USD_TO_GBP", "0.79"))

MODEL_PRICING_USD_PER_MTOK = {
    "claude-haiku-4-5": {
        "input": 1.0,
        "output": 5.0,
        "cache_write_5m": 1.25,
        "cache_read": 0.10,
    },
    "claude-sonnet-4-6": {
        "input": 3.0,
        "output": 15.0,
        "cache_write_5m": 3.75,
        "cache_read": 0.30,
    },
}

DEFAULT_BILLING_MODEL = os.environ.get("ANTHROPIC_DEFAULT_MODEL", "claude-haiku-4-5")


def _normalise_model_name(model: str | None) -> str:
    return (model or DEFAULT_BILLING_MODEL).strip()


def _pricing_for_model(model: str | None) -> dict[str, float]:
    model_name = _normalise_model_name(model)
    return MODEL_PRICING_USD_PER_MTOK.get(
        model_name, MODEL_PRICING_USD_PER_MTOK[DEFAULT_BILLING_MODEL]
    )


def calculate_cost_gbp(
    *,
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    pricing = _pricing_for_model(model)
    cost_usd = (
        input_tokens * pricing["input"] / 1_000_000
        + output_tokens * pricing["output"] / 1_000_000
        + cache_creation_input_tokens * pricing["cache_write_5m"] / 1_000_000
        + cache_read_input_tokens * pricing["cache_read"] / 1_000_000
    )
    return cost_usd * USD_TO_GBP * (1 + MARKUP_RATE)
