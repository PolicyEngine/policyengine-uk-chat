"""Deterministic graders shared by the manual AI evaluation suites."""

import math
import re
from typing import Any, Dict, Iterable, List

from evaluation.schemas import (
    ModelToolCall,
    OutputExpectation,
    TextExpectation,
    ToolCallExpectation,
)


MISSING = object()


def value_at_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, list):
            if not part.isdigit():
                return MISSING
            index = int(part)
            if index >= len(current):
                return MISSING
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                return MISSING
            current = current[part]
        else:
            return MISSING
    return current


def numeric_value_at_path(value: Any, path: str) -> Any:
    if "[]" not in path:
        return value_at_path(value, path)
    if path.count("[]") != 1:
        return MISSING

    collection_path, item_path = path.split("[]", 1)
    item_path = item_path.removeprefix(".")
    collection = value_at_path(value, collection_path)
    if collection is MISSING or not isinstance(collection, list):
        return MISSING

    total = 0.0
    for item in collection:
        item_value = value_at_path(item, item_path) if item_path else item
        if item_value is MISSING:
            return MISSING
        if not isinstance(item_value, int | float) or isinstance(item_value, bool):
            return item_value
        total += float(item_value)
    return total


def _compare_partial(actual: Any, expected: Any, path: str, errors: List[str]) -> None:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            errors.append(f"{path}: expected object, got {type(actual).__name__}")
            return
        for key, expected_value in expected.items():
            if key not in actual:
                errors.append(f"{path}.{key}: missing")
                continue
            _compare_partial(actual[key], expected_value, f"{path}.{key}", errors)
        return

    if isinstance(expected, list):
        if not isinstance(actual, list):
            errors.append(f"{path}: expected list, got {type(actual).__name__}")
            return
        if len(actual) < len(expected):
            errors.append(f"{path}: expected at least {len(expected)} items, got {len(actual)}")
            return
        for index, expected_value in enumerate(expected):
            _compare_partial(actual[index], expected_value, f"{path}.{index}", errors)
        return

    if actual != expected:
        errors.append(f"{path}: expected {expected!r}, got {actual!r}")


def grade_output(actual: Dict[str, Any], expectation: OutputExpectation) -> List[str]:
    errors: List[str] = []
    if expectation.contains:
        _compare_partial(actual, expectation.contains, "$", errors)

    for path in expectation.required_paths:
        if value_at_path(actual, path) is MISSING:
            errors.append(f"{path}: missing")

    for path in expectation.absent_paths:
        if value_at_path(actual, path) is not MISSING:
            errors.append(f"{path}: should be absent")

    if expectation.error_contains:
        error_text = str(actual.get("error", ""))
        if expectation.error_contains not in error_text:
            errors.append(
                f"error: expected substring {expectation.error_contains!r}, got {error_text!r}"
            )

    for numeric in expectation.numeric:
        actual_value = numeric_value_at_path(actual, numeric.path)
        if actual_value is MISSING:
            errors.append(f"{numeric.path}: missing")
            continue
        if not isinstance(actual_value, int | float) or isinstance(actual_value, bool):
            errors.append(f"{numeric.path}: expected number, got {actual_value!r}")
            continue
        value = float(actual_value)
        if numeric.equals is not None and not math.isclose(
            value,
            numeric.equals,
            abs_tol=numeric.tolerance,
        ):
            errors.append(f"{numeric.path}: expected {numeric.equals}, got {value}")
        if numeric.min is not None and value < numeric.min - numeric.tolerance:
            errors.append(f"{numeric.path}: expected >= {numeric.min}, got {value}")
        if numeric.max is not None and value > numeric.max + numeric.tolerance:
            errors.append(f"{numeric.path}: expected <= {numeric.max}, got {value}")

    return errors


def grade_tool_calls(
    actual_calls: List[ModelToolCall],
    expected_calls: List[ToolCallExpectation],
    forbidden_tools: Iterable[str],
) -> List[str]:
    errors: List[str] = []
    forbidden = set(forbidden_tools)
    forbidden_seen = [call.name for call in actual_calls if call.name in forbidden]
    if forbidden_seen:
        errors.append(f"forbidden tool call(s): {forbidden_seen}")

    start = 0
    for expected in expected_calls:
        match_index = None
        for index in range(start, len(actual_calls)):
            if actual_calls[index].name == expected.name:
                match_index = index
                break
        if match_index is None:
            errors.append(f"expected tool {expected.name!r} after position {start}")
            continue

        call = actual_calls[match_index]
        _compare_partial(call.input, expected.input_contains, f"{expected.name}.input", errors)
        for path in expected.required_input_paths:
            if value_at_path(call.input, path) is MISSING:
                errors.append(f"{expected.name}.input.{path}: missing")
        for path in expected.absent_input_paths:
            if value_at_path(call.input, path) is not MISSING:
                errors.append(f"{expected.name}.input.{path}: should be absent")
        start = match_index + 1

    return errors


NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])-?£?\d[\d,]*(?:\.\d+)?%?")


def extract_numbers(text: str) -> List[float]:
    values: List[float] = []
    for match in NUMBER_RE.findall(text):
        cleaned = match.replace("£", "").replace(",", "").replace("%", "")
        try:
            values.append(float(cleaned))
        except ValueError:
            continue
    return values


def _number_allowed(value: float, allowed: List[float], tolerance: float) -> bool:
    return any(math.isclose(value, expected, abs_tol=tolerance) for expected in allowed)


def grade_text(text: str, expectation: TextExpectation) -> List[str]:
    errors: List[str] = []
    lowered = text.lower()

    for required in expectation.required:
        if required.lower() not in lowered:
            errors.append(f"missing required text: {required!r}")

    for forbidden in expectation.forbidden:
        if forbidden.lower() in lowered:
            errors.append(f"forbidden text present: {forbidden!r}")

    for pattern in expectation.forbidden_regex:
        if re.search(pattern, text, flags=re.IGNORECASE):
            errors.append(f"forbidden regex matched: {pattern!r}")

    if expectation.grounded_numbers:
        unexpected = [
            value
            for value in extract_numbers(text)
            if not _number_allowed(value, expectation.allowed_numbers, expectation.number_tolerance)
        ]
        if unexpected:
            errors.append(f"ungrounded number(s): {unexpected}")

    return errors
