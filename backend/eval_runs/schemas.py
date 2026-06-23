"""Request/response models for eval report browsing."""

from typing import Any

from pydantic import BaseModel, Field

from eval.schemas import EvalReport


class EvalRunSummary(BaseModel):
    run_id: str
    mode: str
    provider: str
    model: str | None = None
    git_sha: str | None = None
    started_at: str
    finished_at: str
    duration_ms: float
    suites: list[str]
    passed: int
    failed: int
    skipped: int
    total_cases: int
    run_label: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    slowest_case_id: str | None = None
    slowest_case_duration_ms: float = 0.0
    p95_case_duration_ms: float = 0.0


class EvalRunDetail(BaseModel):
    run_id: str
    summary: EvalRunSummary
    report: EvalReport


class SuiteTimingDelta(BaseModel):
    base: dict[str, Any] | None = None
    head: dict[str, Any] | None = None
    total_ms_delta: float
    p95_ms_delta: float


class CaseDelta(BaseModel):
    suite: str
    id: str
    change: str
    base_status: str | None = None
    head_status: str | None = None
    base_score: float | None = None
    head_score: float | None = None
    score_delta: float | None = None
    base_duration_ms: float | None = None
    head_duration_ms: float | None = None
    duration_delta_ms: float | None = None


class EvalRunComparison(BaseModel):
    base: EvalRunSummary
    head: EvalRunSummary
    counts_delta: dict[str, int]
    suite_timing_delta: dict[str, SuiteTimingDelta]
    case_deltas: list[CaseDelta]
