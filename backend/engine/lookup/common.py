"""Shared lookup helpers that are independent of a lookup target."""

from typing import Any, Dict, List

from engine.lookup.config import DEFAULT_LOOKUP_LIMIT, MAX_LOOKUP_LIMIT


def _limited(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LOOKUP_LIMIT
    return max(1, min(int(limit), MAX_LOOKUP_LIMIT))


def _json_safe_default(value: Any) -> Any:
    if isinstance(value, type):
        return value.__name__
    if hasattr(value, "value"):
        return value.value
    if callable(value):
        return getattr(value, "__name__", str(value))
    return value


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, list):
            if not part.isdigit():
                return None
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current


def _flatten(value: Any, prefix: str = "") -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        rows: List[Dict[str, Any]] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten(child, child_prefix))
        return rows
    if isinstance(value, list):
        rows = []
        for index, child in enumerate(value):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            rows.extend(_flatten(child, child_prefix))
        return rows
    return [{"path": prefix, "value": value}]
