"""Turn-local tool result storage."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import uuid4


@dataclass
class StoredResult:
    kind: str
    payload: Any
    summary: dict[str, Any]


@dataclass
class TurnResultStore:
    _items: dict[str, StoredResult] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def put(self, kind: str, payload: Any, summary: dict[str, Any]) -> str:
        result_id = f"{kind}_{uuid4().hex[:12]}"
        with self._lock:
            self._items[result_id] = StoredResult(kind=kind, payload=payload, summary=summary)
        return result_id

    def get(
        self,
        result_id: str,
        expected_kind: str | tuple[str, ...] | None = None,
    ) -> StoredResult:
        with self._lock:
            item = self._items.get(result_id)
        if item is None:
            raise KeyError(f"Unknown result_id: {result_id}")
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


def new_tool_context(turn_id: str | None = None) -> ToolExecutionContext:
    return ToolExecutionContext(turn_id=turn_id or uuid4().hex, result_store=TurnResultStore())
