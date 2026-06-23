"""Read eval report JSON files from disk."""

import os
from pathlib import Path

from fastapi import HTTPException

from eval.schemas import CaseResult, EvalReport, SuiteTimingSummary
from eval.timing import percentile
from eval_runs.schemas import (
    CaseDelta,
    EvalRunComparison,
    EvalRunDetail,
    EvalRunSummary,
    SuiteTimingDelta,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = REPO_ROOT / "evals" / "reports"


def report_dir() -> Path:
    return Path(os.environ.get("EVAL_REPORT_DIR", DEFAULT_REPORT_DIR))


def safe_run_id(run_id: str) -> str:
    if not run_id or Path(run_id).name != run_id or ".." in run_id:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run_id


def load_report(path: Path) -> EvalReport:
    try:
        report = EvalReport.model_validate_json(path.read_text())
    except Exception as exc:
        raise ValueError(f"{path.name}: {type(exc).__name__}: {exc}") from exc
    if report.run_id is None:
        report = report.model_copy(update={"run_id": path.stem})
    return report


def iter_reports() -> list[tuple[str, EvalReport]]:
    directory = report_dir()
    if not directory.exists():
        return []

    reports: list[tuple[str, EvalReport]] = []
    for path in directory.glob("*.json"):
        try:
            reports.append((path.stem, load_report(path)))
        except ValueError:
            continue
    return reports


def get_report(run_id: str) -> tuple[str, EvalReport]:
    run_id = safe_run_id(run_id)
    path = report_dir() / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Eval run not found")
    try:
        return path.stem, load_report(path)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def summarize_report(path_run_id: str, report: EvalReport) -> EvalRunSummary:
    run_id = report.run_id or path_run_id
    durations = [result.duration_ms for result in report.results]
    slowest = max(report.results, key=lambda result: result.duration_ms, default=None)
    return EvalRunSummary(
        run_id=run_id,
        mode=report.mode,
        provider=report.provider,
        model=report.model,
        git_sha=report.git_sha,
        started_at=report.started_at,
        finished_at=report.finished_at,
        duration_ms=report.duration_ms,
        suites=report.suites,
        passed=report.passed,
        failed=report.failed,
        skipped=report.skipped,
        total_cases=len(report.results),
        run_label=report.metadata.get("run_label"),
        metadata=report.metadata,
        slowest_case_id=slowest.id if slowest else None,
        slowest_case_duration_ms=slowest.duration_ms if slowest else 0.0,
        p95_case_duration_ms=percentile(durations, 0.95),
    )


def list_summaries(
    *,
    limit: int = 50,
    offset: int = 0,
    mode: str | None = None,
    provider: str | None = None,
    suite: str | None = None,
    status: str | None = None,
    q: str | None = None,
) -> list[EvalRunSummary]:
    records = iter_reports()
    summaries: list[EvalRunSummary] = []
    query = q.lower() if q else None

    for path_run_id, report in records:
        if mode and report.mode != mode:
            continue
        if provider and report.provider != provider:
            continue
        if suite and suite not in report.suites and not any(result.suite == suite for result in report.results):
            continue
        if status and not any(result.status == status for result in report.results):
            continue
        summary = summarize_report(path_run_id, report)
        if query:
            haystack = " ".join(
                value
                for value in [
                    summary.run_id,
                    summary.git_sha or "",
                    summary.provider,
                    summary.model or "",
                    summary.run_label or "",
                    " ".join(summary.suites),
                ]
                if value
            ).lower()
            if query not in haystack:
                continue
        summaries.append(summary)

    summaries.sort(key=lambda summary: summary.started_at, reverse=True)
    return summaries[offset : offset + limit]


def get_detail(run_id: str) -> EvalRunDetail:
    path_run_id, report = get_report(run_id)
    return EvalRunDetail(
        run_id=report.run_id or path_run_id,
        summary=summarize_report(path_run_id, report),
        report=report,
    )


def _case_key(result: CaseResult) -> tuple[str, str]:
    return (result.suite, result.id)


def _suite_delta(
    base: SuiteTimingSummary | None,
    head: SuiteTimingSummary | None,
) -> SuiteTimingDelta:
    base_total = base.total_ms if base else 0.0
    head_total = head.total_ms if head else 0.0
    base_p95 = base.p95_ms if base else 0.0
    head_p95 = head.p95_ms if head else 0.0
    return SuiteTimingDelta(
        base=base.model_dump() if base else None,
        head=head.model_dump() if head else None,
        total_ms_delta=head_total - base_total,
        p95_ms_delta=head_p95 - base_p95,
    )


def compare_runs(base_id: str, head_id: str) -> EvalRunComparison:
    base_path_id, base_report = get_report(base_id)
    head_path_id, head_report = get_report(head_id)
    base_summary = summarize_report(base_path_id, base_report)
    head_summary = summarize_report(head_path_id, head_report)

    counts_delta = {
        "passed": head_report.passed - base_report.passed,
        "failed": head_report.failed - base_report.failed,
        "skipped": head_report.skipped - base_report.skipped,
        "total_cases": len(head_report.results) - len(base_report.results),
    }

    suites = set(base_report.timing_summary) | set(head_report.timing_summary)
    suite_timing_delta = {
        suite: _suite_delta(
            base_report.timing_summary.get(suite),
            head_report.timing_summary.get(suite),
        )
        for suite in sorted(suites)
    }

    base_cases = {_case_key(result): result for result in base_report.results}
    head_cases = {_case_key(result): result for result in head_report.results}
    case_deltas: list[CaseDelta] = []
    for suite, case_id in sorted(set(base_cases) | set(head_cases)):
        base = base_cases.get((suite, case_id))
        head = head_cases.get((suite, case_id))
        if base is None:
            case_deltas.append(
                CaseDelta(
                    suite=suite,
                    id=case_id,
                    change="added",
                    head_status=head.status if head else None,
                    head_score=head.score if head else None,
                    head_duration_ms=head.duration_ms if head else None,
                )
            )
            continue
        if head is None:
            case_deltas.append(
                CaseDelta(
                    suite=suite,
                    id=case_id,
                    change="removed",
                    base_status=base.status,
                    base_score=base.score,
                    base_duration_ms=base.duration_ms,
                )
            )
            continue

        status_changed = base.status != head.status
        score_delta = head.score - base.score
        duration_delta = head.duration_ms - base.duration_ms
        if status_changed:
            change = "status_changed"
        elif abs(score_delta) >= 0.0001:
            change = "score_changed"
        elif abs(duration_delta) >= 0.01:
            change = "duration_changed"
        else:
            continue

        case_deltas.append(
            CaseDelta(
                suite=suite,
                id=case_id,
                change=change,
                base_status=base.status,
                head_status=head.status,
                base_score=base.score,
                head_score=head.score,
                score_delta=score_delta,
                base_duration_ms=base.duration_ms,
                head_duration_ms=head.duration_ms,
                duration_delta_ms=duration_delta,
            )
        )

    case_deltas.sort(
        key=lambda delta: (
            {"status_changed": 0, "added": 1, "removed": 1, "score_changed": 2}.get(delta.change, 3),
            -(delta.duration_delta_ms or 0),
            delta.suite,
            delta.id,
        )
    )

    return EvalRunComparison(
        base=base_summary,
        head=head_summary,
        counts_delta=counts_delta,
        suite_timing_delta=suite_timing_delta,
        case_deltas=case_deltas,
    )
