from fastapi.testclient import TestClient

from api.main import app
from eval.schemas import CaseResult, EvalReport, SuiteTimingSummary, TimingEvent


client = TestClient(app)


def _write_report(
    report_dir,
    *,
    run_id: str,
    started_at: str,
    status: str = "passed",
    duration_ms: float = 100.0,
    case_duration_ms: float = 25.0,
    score: float = 1.0,
):
    report = EvalReport(
        run_id=run_id,
        mode="offline",
        suites=["trajectory"],
        provider="fake",
        model=None,
        git_sha=f"{run_id}abcdef",
        started_at=started_at,
        finished_at=started_at,
        duration_ms=duration_ms,
        timing_summary={
            "trajectory": SuiteTimingSummary(
                count=1,
                total_ms=case_duration_ms,
                avg_ms=case_duration_ms,
                p50_ms=case_duration_ms,
                p95_ms=case_duration_ms,
                max_ms=case_duration_ms,
            )
        },
        metadata={"run_label": run_id},
        results=[
            CaseResult(
                id="trajectory_case",
                suite="trajectory",
                status=status,
                score=score,
                duration_ms=case_duration_ms,
                timings=[
                    TimingEvent(
                        name="model.generate",
                        duration_ms=case_duration_ms,
                    )
                ],
            )
        ],
    )
    (report_dir / f"{run_id}.json").write_text(report.model_dump_json())


def test_eval_runs_disabled_without_token(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_REPORT_DIR", str(tmp_path))
    monkeypatch.delenv("EVAL_DASHBOARD_TOKEN", raising=False)

    response = client.get("/eval-runs")

    assert response.status_code == 404


def test_eval_runs_require_bearer_token(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("EVAL_DASHBOARD_TOKEN", "secret")

    response = client.get("/eval-runs")

    assert response.status_code == 401


def test_eval_runs_list_detail_and_compare(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("EVAL_DASHBOARD_TOKEN", "secret")
    _write_report(
        tmp_path,
        run_id="base-run",
        started_at="2026-06-18T00:00:00+00:00",
        status="passed",
        duration_ms=100.0,
        case_duration_ms=25.0,
        score=1.0,
    )
    _write_report(
        tmp_path,
        run_id="head-run",
        started_at="2026-06-19T00:00:00+00:00",
        status="failed",
        duration_ms=150.0,
        case_duration_ms=40.0,
        score=0.0,
    )
    headers = {"Authorization": "Bearer secret"}

    list_response = client.get("/eval-runs", headers=headers)
    assert list_response.status_code == 200
    runs = list_response.json()
    assert [run["run_id"] for run in runs] == ["head-run", "base-run"]
    assert runs[0]["p95_case_duration_ms"] == 40.0

    detail_response = client.get("/eval-runs/head-run", headers=headers)
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["run_id"] == "head-run"
    assert detail["report"]["results"][0]["timings"][0]["name"] == "model.generate"

    compare_response = client.get(
        "/eval-runs/compare",
        params={"base_id": "base-run", "head_id": "head-run"},
        headers=headers,
    )
    assert compare_response.status_code == 200
    comparison = compare_response.json()
    assert comparison["counts_delta"] == {
        "passed": -1,
        "failed": 1,
        "skipped": 0,
        "total_cases": 0,
    }
    assert comparison["suite_timing_delta"]["trajectory"]["p95_ms_delta"] == 15.0
    assert comparison["case_deltas"][0]["change"] == "status_changed"


def test_eval_run_detail_rejects_path_traversal(monkeypatch, tmp_path):
    monkeypatch.setenv("EVAL_REPORT_DIR", str(tmp_path))
    monkeypatch.setenv("EVAL_DASHBOARD_TOKEN", "secret")

    response = client.get(
        "/eval-runs/../secret",
        headers={"Authorization": "Bearer secret"},
    )

    assert response.status_code in {404, 405}
