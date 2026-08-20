"""Idempotent processing of persisted immutable billing intents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from analysis.models import BillingIntent
from analysis.store import MarkBillingRecordedCommand


@dataclass(frozen=True)
class BillingRecordResult:
    recorded: bool
    duplicate: bool = False
    cost_gbp: float = 0
    response: Mapping[str, Any] = field(default_factory=dict)


class BillingRecorder(Protocol):
    def __call__(self, intent: BillingIntent) -> BillingRecordResult: ...


class BillingIntentStore(Protocol):
    def pending_billing_intents(self, *, user_id: str) -> tuple[BillingIntent, ...]: ...

    def mark_billing_recorded(self, command: MarkBillingRecordedCommand) -> bool: ...


class BillingIntentProcessor:
    """Record immutable charges and acknowledge only confirmed records."""

    def __init__(
        self,
        *,
        store: BillingIntentStore,
        recorder: BillingRecorder,
    ) -> None:
        self._store = store
        self._recorder = recorder

    def process(self, intent: BillingIntent) -> BillingRecordResult:
        result = self._recorder(intent)
        if result.recorded:
            self._store.mark_billing_recorded(
                MarkBillingRecordedCommand(
                    session_id=str(intent.session_id),
                    turn_id=str(intent.turn_id),
                )
            )
        return result

    def process_pending(self, *, user_id: str) -> tuple[BillingRecordResult, ...]:
        intents = self._store.pending_billing_intents(user_id=user_id)
        return tuple(self.process(intent) for intent in intents)
