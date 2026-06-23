"""Timing helpers for the manual eval harness."""

from contextlib import contextmanager
from time import perf_counter_ns
from typing import Any, Iterator

from eval.schemas import CaseResult, SuiteTimingSummary, TimingEvent


def elapsed_ms(start_ns: int) -> float:
    return (perf_counter_ns() - start_ns) / 1_000_000


class TimingRecorder:
    def __init__(self) -> None:
        self.events: list[TimingEvent] = []

    @contextmanager
    def record(
        self,
        name: str,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[None]:
        start_ns = perf_counter_ns()
        try:
            yield
        finally:
            self.events.append(
                TimingEvent(
                    name=name,
                    duration_ms=elapsed_ms(start_ns),
                    attributes=attributes or {},
                )
            )


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def summarize_suite_timings(results: list[CaseResult]) -> dict[str, SuiteTimingSummary]:
    by_suite: dict[str, list[float]] = {}
    for result in results:
        by_suite.setdefault(result.suite, []).append(result.duration_ms)

    summaries: dict[str, SuiteTimingSummary] = {}
    for suite, durations in by_suite.items():
        total = sum(durations)
        summaries[suite] = SuiteTimingSummary(
            count=len(durations),
            total_ms=total,
            avg_ms=total / len(durations) if durations else 0.0,
            p50_ms=percentile(durations, 0.50),
            p95_ms=percentile(durations, 0.95),
            max_ms=max(durations) if durations else 0.0,
        )
    return summaries
