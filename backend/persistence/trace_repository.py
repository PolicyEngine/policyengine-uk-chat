"""SQL storage for allowlisted invocation records."""

from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy import func
from sqlmodel import Session, select

from capabilities.tracing import InvocationRecord
from conversations.models import get_engine
from persistence.rows import InvocationTraceRow
from tools.contracts import Visibility


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SQLInvocationTraceRepository:
    def __init__(self, *, engine=None) -> None:
        self._engine = engine or get_engine()

    def save(self, record: InvocationRecord) -> None:
        row = InvocationTraceRow(
            invocation_id=record.invocation_id,
            conversation_id=record.conversation_id,
            turn_id=record.turn_id,
            parent_invocation_id=record.parent_invocation_id,
            sequence=record.sequence,
            kind=record.kind.value,
            identifier=record.identifier,
            version=record.version,
            visibility=record.visibility.value,
            started_at=record.started_at,
            completed_at=record.completed_at,
            duration_ms=record.duration_ms,
            status=record.status.value,
            summary=record.summary,
            debug_input_json=(
                json.dumps(record.debug_input, separators=(",", ":"))
                if record.debug_input is not None
                else None
            ),
            debug_output_json=(
                json.dumps(record.debug_output, separators=(",", ":"))
                if record.debug_output is not None
                else None
            ),
        )
        with Session(self._engine) as session:
            session.merge(row)
            session.commit()

    def last_sequence(self, conversation_id: str) -> int:
        statement = select(func.max(InvocationTraceRow.sequence)).where(
            InvocationTraceRow.conversation_id == conversation_id
        )
        with Session(self._engine) as session:
            value = session.exec(statement).one()
        return int(value or 0)

    def list_for_conversation(
        self,
        conversation_id: str,
        *,
        include_private: bool,
    ) -> tuple[InvocationRecord, ...]:
        statement = select(InvocationTraceRow).where(
            InvocationTraceRow.conversation_id == conversation_id
        )
        if not include_private:
            statement = statement.where(
                InvocationTraceRow.visibility == Visibility.PUBLIC.value
            )
        statement = statement.order_by(InvocationTraceRow.sequence)
        with Session(self._engine) as session:
            rows = session.exec(statement).all()
        return tuple(
            InvocationRecord.model_validate(
                {
                    "conversation_id": row.conversation_id,
                    "turn_id": row.turn_id,
                    "invocation_id": row.invocation_id,
                    "parent_invocation_id": row.parent_invocation_id,
                    "sequence": row.sequence,
                    "kind": row.kind,
                    "identifier": row.identifier,
                    "version": row.version,
                    "visibility": row.visibility,
                    "started_at": _utc(row.started_at),
                    "completed_at": _utc(row.completed_at),
                    "duration_ms": row.duration_ms,
                    "status": row.status,
                    "summary": row.summary,
                    "debug_input": (
                        json.loads(row.debug_input_json)
                        if row.debug_input_json is not None
                        else None
                    ),
                    "debug_output": (
                        json.loads(row.debug_output_json)
                        if row.debug_output_json is not None
                        else None
                    ),
                }
            )
            for row in rows
        )
