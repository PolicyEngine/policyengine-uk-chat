from pathlib import Path

import pytest

from eval.modal_batch import (
    aggregate_case_reports,
    case_report_filename,
    load_deployed_case_ids,
)
from eval.schemas import CaseResult, EvalReport


REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_FILE = REPO_ROOT / "evals/cases/tool_loop/uk_population_live.yaml"


def _report(
    case_id: str,
    *,
    status: str = "passed",
    started_at: str = "2026-08-05T12:00:00+00:00",
    finished_at: str = "2026-08-05T12:01:00+00:00",
) -> EvalReport:
    return EvalReport(
        mode="deployed",
        suites=["tool_loop"],
        provider="uk-chat-backend",
        git_sha="abc123",
        started_at=started_at,
        finished_at=finished_at,
        results=[
            CaseResult(
                id=case_id,
                suite="tool_loop",
                status=status,
                score=1.0 if status == "passed" else 0.0,
            )
        ],
    )


def test_modal_batch_loads_exactly_twenty_unique_population_cases():
    case_ids = load_deployed_case_ids(CASE_FILE)

    assert len(case_ids) == 20
    assert len(set(case_ids)) == 20
    assert case_ids[0] == "uk_population_personal_allowance_500_cost"
    assert case_ids[-1] == "uk_population_basic_rate_plus_1pp_people"


@pytest.mark.parametrize("unsafe_id", ["../escape", "nested/case", "", "."])
def test_case_report_filename_rejects_unsafe_ids(unsafe_id):
    with pytest.raises(ValueError, match="safe identifier"):
        case_report_filename(unsafe_id)


def test_aggregate_case_reports_preserves_case_order_and_time_bounds():
    reports = [
        _report(
            "case_a",
            started_at="2026-08-05T12:02:00+00:00",
            finished_at="2026-08-05T12:04:00+00:00",
        ),
        _report(
            "case_b",
            status="failed",
            started_at="2026-08-05T12:00:00+00:00",
            finished_at="2026-08-05T12:03:00+00:00",
        ),
    ]

    aggregate = aggregate_case_reports(reports, ["case_b", "case_a"])

    assert [result.id for result in aggregate.results] == ["case_b", "case_a"]
    assert aggregate.started_at == "2026-08-05T12:00:00+00:00"
    assert aggregate.finished_at == "2026-08-05T12:04:00+00:00"
    assert aggregate.passed == 1
    assert aggregate.failed == 1
    assert aggregate.git_sha == "abc123"


def test_aggregate_case_reports_rejects_missing_duplicate_or_wrong_case_results():
    with pytest.raises(ValueError, match="missing case reports: case_b"):
        aggregate_case_reports([_report("case_a")], ["case_a", "case_b"])

    with pytest.raises(ValueError, match="duplicate case report: case_a"):
        aggregate_case_reports(
            [_report("case_a"), _report("case_a")],
            ["case_a"],
        )

    empty_report = _report("case_a").model_copy(update={"results": []})
    with pytest.raises(ValueError, match="exactly one result"):
        aggregate_case_reports([empty_report], ["case_a"])
