"""Turn-local tool result storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass
class StoredResult:
    execution_id: str
    source_step_id: str
    kind: str
    payload: Any
    summary: dict[str, Any]


@dataclass
class TurnResultStore:
    _items: dict[str, StoredResult] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)
    default_execution_id: str = "legacy"
    default_source_step_id: str = "legacy"

    def put(
        self,
        kind: str,
        payload: Any,
        summary: dict[str, Any],
        *,
        execution_id: str | None = None,
        source_step_id: str | None = None,
    ) -> str:
        result_id = f"{kind}_{uuid4().hex[:12]}"
        with self._lock:
            self._items[result_id] = StoredResult(
                execution_id=execution_id or self.default_execution_id,
                source_step_id=source_step_id or self.default_source_step_id,
                kind=kind,
                payload=payload,
                summary=summary,
            )
        return result_id

    def get(
        self,
        result_id: str,
        expected_kind: str | tuple[str, ...] | None = None,
        *,
        execution_id: str | None = None,
    ) -> StoredResult:
        with self._lock:
            item = self._items.get(result_id)
        if item is None:
            raise KeyError(f"Unknown result_id: {result_id}")
        if execution_id is not None and item.execution_id != execution_id:
            raise KeyError("Result reference belongs to a different execution.")
        if expected_kind is not None:
            kinds = (expected_kind,) if isinstance(expected_kind, str) else expected_kind
            if item.kind not in kinds:
                raise TypeError(
                    f"Result {result_id} has kind '{item.kind}', expected one of {list(kinds)}."
                )
        return item


@dataclass
class ToolExecutionContext:
    turn_id: str
    result_store: TurnResultStore
    execution_id: str = "legacy"
    active_step_id: str = "legacy"
    approved_reform: dict[str, Any] | None = None
    require_approved_reform: bool = False


def new_tool_context(
    turn_id: str | None = None,
    *,
    execution_id: str | None = None,
) -> ToolExecutionContext:
    resolved_turn_id = turn_id or uuid4().hex
    return ToolExecutionContext(
        turn_id=resolved_turn_id,
        execution_id=execution_id or resolved_turn_id,
        result_store=TurnResultStore(
            default_execution_id=execution_id or resolved_turn_id,
        ),
    )
