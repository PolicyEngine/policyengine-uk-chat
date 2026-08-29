"""Sanitized invocation records shared by execution and observability."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from threading import Lock
from time import monotonic
from typing import Literal, Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from tools.contracts import Visibility


class InvocationKind(str, Enum):
    TOOL = "tool"
    CAPABILITY = "capability"


class InvocationStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    NEEDS_INPUT = "needs_input"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvocationRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    conversation_id: str
    turn_id: str
    invocation_id: str
    parent_invocation_id: str | None = None
    sequence: int
    kind: InvocationKind
    identifier: str
    version: str
    visibility: Visibility
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    status: InvocationStatus
    summary: str
    debug_input: JsonValue | None = None
    debug_output: JsonValue | None = None


class InvocationTraceSink(Protocol):
    """Persistence operations needed by the request-independent tracer."""

    def save(self, record: InvocationRecord) -> None: ...

    def last_sequence(self, conversation_id: str) -> int: ...


class InvocationTraceEvent(BaseModel):
    """One allowlisted start or finish update suitable for public projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_index: int
    phase: Literal["started", "finished"]
    record: InvocationRecord


_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_VALUE = re.compile(
    r"(?i)(?:bearer\s+|sk-(?:ant-)?|hf_)[a-z0-9_\-]{8,}"
)
_MAX_SUMMARY_LENGTH = 240
_SECRET_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
)
_SECRET_KEY_SUFFIXES = (
    "_access_token",
    "_api_key",
    "_auth_token",
    "_credential",
    "_password",
    "_secret",
    "_service_role_key",
)
_REQUEST_LOCAL_KEYS = frozenset(
    {
        "request_id",
        "result_handle",
        "result_id",
        "simulation_id",
    }
)
_ROW_LEVEL_KEYS = frozenset(
    {
        "microdata",
        "records",
        "row_data",
        "row_level_data",
        "survey_records",
    }
)
def sanitize_trace_summary(summary: str) -> str:
    """Restrict retained summaries to short, single-line metadata text."""

    normalized = " ".join(_CONTROL_CHARACTERS.sub(" ", summary).split())
    if not normalized:
        return "Invocation status updated."
    return normalized[:_MAX_SUMMARY_LENGTH]


def _safe_string(value: str) -> str:
    return _SECRET_VALUE.sub("[redacted secret]", value)


def _is_secret_key(key: str) -> bool:
    return key in _SECRET_KEYS or key.endswith(_SECRET_KEY_SUFFIXES)


def _project_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _safe_string(value)
    if isinstance(value, dict):
        projected: dict[str, JsonValue] = {}
        for raw_key, item in value.items():
            key = str(raw_key)
            normalized = key.casefold()
            if _is_secret_key(normalized):
                projected[key] = "[redacted secret]"
            elif normalized in _REQUEST_LOCAL_KEYS:
                projected[key] = "[request-local identifier omitted]"
            elif normalized in _ROW_LEVEL_KEYS:
                projected[key] = "[record-level data omitted]"
            else:
                projected[key] = _project_value(item)
        return projected
    if isinstance(value, (list, tuple)):
        return [_project_value(item) for item in value]
    return f"[{type(value).__name__} omitted]"


def debug_projection(value: BaseModel | object) -> JsonValue:
    """Preserve validated structured values while removing prohibited trace data."""

    serialized = (
        value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    )
    return _project_value(serialized)


class InvocationTracer:
    """Record every invocation using a fixed metadata-only schema."""

    def __init__(self, *, sink: InvocationTraceSink | None = None) -> None:
        self._sink = sink
        self._records: dict[str, InvocationRecord] = {}
        self._started: dict[str, float] = {}
        self._sequence: dict[str, int] = {}
        self._events: list[InvocationTraceEvent] = []
        self._lock = Lock()

    def start(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        parent_invocation_id: str | None,
        kind: InvocationKind,
        identifier: str,
        version: str,
        visibility: Visibility,
        summary: str,
        debug_input: JsonValue | None = None,
    ) -> InvocationRecord:
        with self._lock:
            if conversation_id not in self._sequence:
                self._sequence[conversation_id] = (
                    self._sink.last_sequence(conversation_id)
                    if self._sink is not None
                    else 0
                )
            sequence = self._sequence[conversation_id] + 1
            self._sequence[conversation_id] = sequence
            invocation_id = uuid4().hex
            record = InvocationRecord(
                conversation_id=conversation_id,
                turn_id=turn_id,
                invocation_id=invocation_id,
                parent_invocation_id=parent_invocation_id,
                sequence=sequence,
                kind=kind,
                identifier=identifier,
                version=version,
                visibility=visibility,
                started_at=datetime.now(timezone.utc),
                status=InvocationStatus.RUNNING,
                summary=sanitize_trace_summary(summary),
                debug_input=debug_input,
            )
            self._records[invocation_id] = record
            self._started[invocation_id] = monotonic()
            self._append_event("started", record)
            if self._sink is not None:
                self._sink.save(record)
        return record

    def finish(
        self,
        invocation_id: str,
        *,
        status: InvocationStatus,
        summary: str,
        debug_output: JsonValue | None = None,
    ) -> InvocationRecord:
        completed_at = datetime.now(timezone.utc)
        with self._lock:
            current = self._records[invocation_id]
            started = self._started.pop(invocation_id)
            updated = current.model_copy(
                update={
                    "completed_at": completed_at,
                    "duration_ms": max(0, round((monotonic() - started) * 1000)),
                    "status": status,
                    "summary": sanitize_trace_summary(summary),
                    "debug_output": debug_output,
                }
            )
            self._records[invocation_id] = updated
            self._append_event("finished", updated)
            if self._sink is not None:
                self._sink.save(updated)
        return updated

    def records(
        self,
        conversation_id: str,
        *,
        include_private: bool,
    ) -> tuple[InvocationRecord, ...]:
        with self._lock:
            records = tuple(
                record
                for record in self._records.values()
                if record.conversation_id == conversation_id
                and (include_private or record.visibility is Visibility.PUBLIC)
            )
        return tuple(sorted(records, key=lambda record: record.sequence))

    def events_for_turn(
        self,
        *,
        conversation_id: str,
        turn_id: str,
        after_event_index: int = 0,
        include_private: bool,
    ) -> tuple[InvocationTraceEvent, ...]:
        """Return ordered trace updates created after a caller-owned cursor."""

        with self._lock:
            return tuple(
                event
                for event in self._events
                if event.event_index > after_event_index
                and event.record.conversation_id == conversation_id
                and event.record.turn_id == turn_id
                and (
                    include_private
                    or event.record.visibility is Visibility.PUBLIC
                )
            )

    def _append_event(
        self,
        phase: Literal["started", "finished"],
        record: InvocationRecord,
    ) -> None:
        self._events.append(
            InvocationTraceEvent(
                event_index=len(self._events) + 1,
                phase=phase,
                record=record,
            )
        )
