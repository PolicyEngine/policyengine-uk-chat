"""Shared grading and trial aggregation for local and deployed tool loops."""

from typing import Any, Iterable

from eval.graders import grade_text, grade_tool_calls
from eval.schemas import (
    CaseResult,
    ModelToolCall,
    TextExpectation,
    ToolLoopCase,
)


def numeric_grounding_variants(value: float) -> list[float]:
    variants: list[float] = []
    for signed_value in {value, abs(value)}:
        magnitude = abs(signed_value)
        variants.append(signed_value)
        if 0 < magnitude <= 1:
            variants.append(signed_value * 100)
        if magnitude >= 1_000:
            variants.append(signed_value / 1_000)
        if magnitude >= 1_000_000:
            variants.append(signed_value / 1_000_000)
        if magnitude >= 1_000_000_000:
            variants.append(signed_value / 1_000_000_000)
    return variants


def numbers_from_value(value: Any) -> list[float]:
    if isinstance(value, bool):
        return []
    if isinstance(value, int | float):
        return numeric_grounding_variants(float(value))
    if isinstance(value, dict):
        numbers: list[float] = []
        for item in value.values():
            numbers.extend(numbers_from_value(item))
        return numbers
    if isinstance(value, list | tuple):
        numbers = []
        for item in value:
            numbers.extend(numbers_from_value(item))
        return numbers
    return []


def expectation_with_trace_numbers(
    expectation: TextExpectation,
    *,
    tool_calls: Iterable[ModelToolCall],
    tool_outputs: Iterable[Any],
) -> TextExpectation:
    if not expectation.grounded_numbers:
        return expectation

    allowed_numbers = list(expectation.allowed_numbers)
    for call in tool_calls:
        allowed_numbers.extend(numbers_from_value(call.input))
    for output in tool_outputs:
        allowed_numbers.extend(numbers_from_value(output))
    return expectation.model_copy(update={"allowed_numbers": allowed_numbers})


def grade_tool_loop_case(
    case: ToolLoopCase,
    *,
    text: str,
    tool_calls: list[ModelToolCall],
    tool_outputs: list[Any],
    errors: list[str] | None = None,
) -> CaseResult:
    grading_errors = list(errors or [])
    grading_errors.extend(
        grade_tool_calls(tool_calls, case.expected_tools, case.forbidden_tools)
    )
    expectation = expectation_with_trace_numbers(
        case.expect,
        tool_calls=tool_calls,
        tool_outputs=tool_outputs,
    )
    grading_errors.extend(grade_text(text, expectation))
    return CaseResult(
        id=case.id,
        suite=case.suite,
        status="failed" if grading_errors else "passed",
        score=0.0 if grading_errors else 1.0,
        errors=grading_errors,
        details={
            "text": text,
            "tool_calls": [call.model_dump() for call in tool_calls],
            "tool_outputs": tool_outputs,
        },
    )


def aggregate_tool_loop_trials(
    case: ToolLoopCase,
    trial_results: list[CaseResult],
) -> CaseResult:
    passed_trials = sum(result.status == "passed" for result in trial_results)
    failed_trials = len(trial_results) - passed_trials
    score = passed_trials / len(trial_results) if trial_results else 0.0
    errors: list[str] = []

    if score < case.pass_threshold:
        errors.append(
            f"pass rate {score:.2f} below threshold {case.pass_threshold:.2f} "
            f"({passed_trials}/{len(trial_results)} trials passed)"
        )
    for index, result in enumerate(trial_results, start=1):
        errors.extend(f"trial {index}: {error}" for error in result.errors)

    return CaseResult(
        id=case.id,
        suite=case.suite,
        status="passed" if score >= case.pass_threshold else "failed",
        score=score,
        errors=errors,
        details={
            "trials_run": len(trial_results),
            "passed_trials": passed_trials,
            "failed_trials": failed_trials,
            "pass_threshold": case.pass_threshold,
            "trials": [
                {
                    "trial": index,
                    "status": result.status,
                    "score": result.score,
                    "errors": result.errors,
                    "details": result.details,
                }
                for index, result in enumerate(trial_results, start=1)
            ],
        },
    )
