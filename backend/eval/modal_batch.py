"""Batch helpers shared by the detached Modal eval app and local collection."""

import re
from collections.abc import Iterable, Sequence
from pathlib import Path

from eval.loaders import load_case_file
from eval.schemas import EvalReport, ToolLoopCase


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def safe_identifier(value: str) -> str:
    if not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError(f"expected a safe identifier, got {value!r}")
    return value


def case_report_filename(case_id: str) -> str:
    return f"{safe_identifier(case_id)}.json"


def load_deployed_case_ids(case_file: Path) -> list[str]:
    cases = load_case_file(case_file)
    if not all(isinstance(case, ToolLoopCase) for case in cases):
        raise ValueError("Modal deployed eval batches may contain only tool_loop cases")

    case_ids = [case.id for case in cases]
    duplicates = sorted(
        case_id for case_id in set(case_ids) if case_ids.count(case_id) > 1
    )
    if duplicates:
        raise ValueError(f"duplicate deployed eval case IDs: {', '.join(duplicates)}")
    for case_id in case_ids:
        safe_identifier(case_id)
    return case_ids


def aggregate_case_reports(
    reports: Iterable[EvalReport],
    expected_case_ids: Sequence[str],
) -> EvalReport:
    expected = list(expected_case_ids)
    if len(expected) != len(set(expected)):
        raise ValueError("expected case IDs must be unique")

    by_case = {}
    source_reports = list(reports)
    for report in source_reports:
        if report.mode != "deployed":
            raise ValueError(f"expected deployed report, got {report.mode!r}")
        if len(report.results) != 1:
            raise ValueError("each Modal case report must contain exactly one result")
        result = report.results[0]
        if result.id in by_case:
            raise ValueError(f"duplicate case report: {result.id}")
        by_case[result.id] = result

    missing = [case_id for case_id in expected if case_id not in by_case]
    if missing:
        raise ValueError(f"missing case reports: {', '.join(missing)}")
    unexpected = sorted(set(by_case) - set(expected))
    if unexpected:
        raise ValueError(f"unexpected case reports: {', '.join(unexpected)}")

    git_shas = {report.git_sha for report in source_reports if report.git_sha}
    if len(git_shas) > 1:
        raise ValueError("case reports came from different git revisions")
    if not source_reports:
        raise ValueError("at least one case report is required")

    return EvalReport(
        mode="deployed",
        suites=["tool_loop"],
        provider="uk-chat-backend",
        model=None,
        git_sha=next(iter(git_shas), None),
        started_at=min(report.started_at for report in source_reports),
        finished_at=max(report.finished_at for report in source_reports),
        results=[by_case[case_id] for case_id in expected],
    )
