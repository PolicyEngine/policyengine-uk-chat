"""Idempotent turn and externally significant capability-call receipts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from conversations.models import get_engine
from persistence.rows import CapabilityCallReceiptRow, TurnReceiptRow


class ReceiptStatus(str, Enum):
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class IdempotencyDecision(str, Enum):
    STARTED = "started"
    IN_PROGRESS = "in_progress"
    REPLAY = "replay"
    CONFLICT = "conflict"


class IdempotencyResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decision: IdempotencyDecision
    status: ReceiptStatus
    outcome: dict[str, object] | None = None


def request_fingerprint(payload: object) -> str:
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class SQLIdempotencyRepository:
    def __init__(self, *, engine=None) -> None:
        self._engine = engine or get_engine()

    def begin_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        fingerprint: str,
    ) -> IdempotencyResult:
        with Session(self._engine) as session:
            row = session.get(TurnReceiptRow, turn_id)
            if row is None:
                row = TurnReceiptRow(
                    turn_id=turn_id,
                    conversation_id=conversation_id,
                    request_fingerprint=fingerprint,
                    status=ReceiptStatus.PROCESSING.value,
                )
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    row = session.get(TurnReceiptRow, turn_id)
                    if row is None:
                        raise
                else:
                    return IdempotencyResult(
                        decision=IdempotencyDecision.STARTED,
                        status=ReceiptStatus.PROCESSING,
                    )
            return self._turn_result(row, conversation_id, fingerprint)

    def complete_turn(
        self,
        *,
        turn_id: str,
        fingerprint: str,
        outcome: dict[str, object],
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(TurnReceiptRow, turn_id)
            if row is None:
                raise KeyError(f"Unknown turn receipt: {turn_id}")
            if row.request_fingerprint != fingerprint:
                raise ValueError("Turn fingerprint conflict.")
            row.status = ReceiptStatus.COMPLETED.value
            row.public_outcome_json = json.dumps(outcome, sort_keys=True)
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()

    def fail_turn(self, *, turn_id: str, fingerprint: str) -> None:
        with Session(self._engine) as session:
            row = session.get(TurnReceiptRow, turn_id)
            if row is None:
                raise KeyError(f"Unknown turn receipt: {turn_id}")
            if row.request_fingerprint != fingerprint:
                raise ValueError("Turn fingerprint conflict.")
            row.status = ReceiptStatus.FAILED.value
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()

    def claim_billing(self, turn_id: str) -> bool:
        with Session(self._engine) as session:
            result = session.exec(
                update(TurnReceiptRow)
                .where(TurnReceiptRow.turn_id == turn_id)
                .where(TurnReceiptRow.billing_claimed.is_(False))
                .values(billing_claimed=True, updated_at=datetime.now(timezone.utc))
            )
            session.commit()
            return result.rowcount == 1

    def begin_call(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        call_id: str,
        operation_id: str,
        fingerprint: str,
    ) -> IdempotencyResult:
        with Session(self._engine) as session:
            row = session.get(CapabilityCallReceiptRow, call_id)
            if row is None:
                row = CapabilityCallReceiptRow(
                    call_id=call_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    operation_id=operation_id,
                    request_fingerprint=fingerprint,
                    status=ReceiptStatus.PROCESSING.value,
                )
                session.add(row)
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    row = session.get(CapabilityCallReceiptRow, call_id)
                    if row is None:
                        raise
                else:
                    return IdempotencyResult(
                        decision=IdempotencyDecision.STARTED,
                        status=ReceiptStatus.PROCESSING,
                    )
            if (
                row.conversation_id != conversation_id
                or row.turn_id != turn_id
                or row.operation_id != operation_id
                or row.request_fingerprint != fingerprint
            ):
                return IdempotencyResult(
                    decision=IdempotencyDecision.CONFLICT,
                    status=ReceiptStatus(row.status),
                )
            return self._result(row.status, row.outcome_json)

    def complete_call(
        self,
        *,
        call_id: str,
        fingerprint: str,
        outcome: dict[str, object],
    ) -> None:
        with Session(self._engine) as session:
            row = session.get(CapabilityCallReceiptRow, call_id)
            if row is None:
                raise KeyError(f"Unknown capability call receipt: {call_id}")
            if row.request_fingerprint != fingerprint:
                raise ValueError("Capability call fingerprint conflict.")
            row.status = ReceiptStatus.COMPLETED.value
            row.outcome_json = json.dumps(outcome, sort_keys=True)
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()

    def fail_call(self, *, call_id: str, fingerprint: str) -> None:
        """Finish an interrupted call receipt without storing a replay result."""

        with Session(self._engine) as session:
            row = session.get(CapabilityCallReceiptRow, call_id)
            if row is None:
                raise KeyError(f"Unknown capability call receipt: {call_id}")
            if row.request_fingerprint != fingerprint:
                raise ValueError("Capability call fingerprint conflict.")
            row.status = ReceiptStatus.FAILED.value
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.commit()

    @staticmethod
    def _turn_result(
        row: TurnReceiptRow,
        conversation_id: str,
        fingerprint: str,
    ) -> IdempotencyResult:
        if (
            row.conversation_id != conversation_id
            or row.request_fingerprint != fingerprint
        ):
            return IdempotencyResult(
                decision=IdempotencyDecision.CONFLICT,
                status=ReceiptStatus(row.status),
            )
        return SQLIdempotencyRepository._result(
            row.status,
            row.public_outcome_json,
        )

    @staticmethod
    def _result(status_value: str, outcome_json: str | None) -> IdempotencyResult:
        status = ReceiptStatus(status_value)
        if status is ReceiptStatus.PROCESSING:
            decision = IdempotencyDecision.IN_PROGRESS
        else:
            decision = IdempotencyDecision.REPLAY
        outcome = json.loads(outcome_json) if outcome_json is not None else None
        return IdempotencyResult(
            decision=decision,
            status=status,
            outcome=outcome,
        )
