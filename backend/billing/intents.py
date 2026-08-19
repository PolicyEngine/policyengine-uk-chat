"""Pure construction of immutable billing intents from recorded model calls."""

from __future__ import annotations

from typing import Protocol

from analysis.common import stable_identifier
from analysis.models import BillingChargeInput, BillingIntent, ModelUsageEntry
from billing.pricing import calculate_cost_gbp


class PricingFunction(Protocol):
    def __call__(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_creation_input_tokens: int,
        cache_read_input_tokens: int,
    ) -> float: ...


def build_billing_intent(
    *,
    session_id: str,
    turn_id: str,
    user_id: str | None,
    usage_entries: tuple[ModelUsageEntry, ...],
    pricing: PricingFunction = calculate_cost_gbp,
) -> BillingIntent | None:
    """Price each actual model call once and store those immutable inputs."""

    if not usage_entries:
        return None
    return BillingIntent(
        billing_intent_id=stable_identifier(
            "billing_intent",
            session_id,
            turn_id,
        ),
        session_id=session_id,
        turn_id=turn_id,
        user_id=user_id,
        usage_entry_ids=tuple(item.usage_entry_id for item in usage_entries),
        charge_inputs=tuple(
            BillingChargeInput(
                usage_entry_id=item.usage_entry_id,
                operation=item.operation,
                model=item.model,
                input_tokens=item.input_tokens,
                output_tokens=item.output_tokens,
                cache_creation_input_tokens=item.cache_creation_input_tokens,
                cache_read_input_tokens=item.cache_read_input_tokens,
                cost_gbp=pricing(
                    model=item.model,
                    input_tokens=item.input_tokens,
                    output_tokens=item.output_tokens,
                    cache_creation_input_tokens=(
                        item.cache_creation_input_tokens
                    ),
                    cache_read_input_tokens=item.cache_read_input_tokens,
                ),
            )
            for item in usage_entries
        ),
    )
