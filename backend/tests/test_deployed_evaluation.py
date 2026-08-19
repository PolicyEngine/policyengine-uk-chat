import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from eval.schemas import (
    AnalysisTraceExpectation,
    EvalAnalysisTrace,
    EvalChatResponse,
    EvalToolTrace,
    ToolLoopCase,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def completed_response(content="Completed annual result £100."):
    return EvalChatResponse(
        status="completed",
        content=content,
        session_id="remote-session",
        model="claude",
        route="standard",
        outcome="completed",
        stop_reason="end_turn",
        analysis_trace=EvalAnalysisTrace(
            workflow_version=5,
            update_kind="start_analysis",
            binding_outcome="ready",
            execution_mode="standard",
            permitted_operations=[
                "run_society_simulation",
                "compute_budgetary_impact",
            ],
        ),
        tool_trace=[
            EvalToolTrace(
                tool_id="tool-1",
                name="run_society_simulation",
                input={"year": 2026, "reform": {"rate": 0.19}},
                status="success",
                output={"budgetary_impact": -100},
            )
        ],
    )


def test_deployed_client_sends_chat_request_and_eval_token():
    from eval.deployed_client import DeployedEvalClient

    requests = []

    def handler(request: httpx.Request):
        requests.append(request)
        return httpx.Response(200, json=completed_response().model_dump(mode="json"))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = DeployedEvalClient(
                backend_url="https://backend.example/",
                token="eval-secret",
                timeout_seconds=600,
                http_client=http,
            )
            return await client.run_turn(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="case-1-trial-1",
                charts_mode=True,
            )

    response = asyncio.run(run())

    assert response.status == "completed"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://backend.example/eval/chat/message"
    assert request.headers["X-Eval-Token"] == "eval-secret"
    assert json.loads(request.content) == {
        "messages": [{"role": "user", "content": "Calculate"}],
        "session_id": "case-1-trial-1",
        "charts_mode": True,
    }


def test_deployed_client_reports_http_errors_without_echoing_token():
    from eval.deployed_client import DeployedEvalClient, DeployedEvalError

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(503, json={"detail": "Unavailable"})
            )
        ) as http:
            client = DeployedEvalClient(
                backend_url="https://backend.example",
                token="never-echo-this-token",
                http_client=http,
            )
            return await client.run_turn(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="session",
            )

    with pytest.raises(DeployedEvalError) as error:
        asyncio.run(run())

    assert "503" in str(error.value)
    assert "never-echo-this-token" not in str(error.value)


def test_deployed_client_reports_timeouts_and_invalid_responses():
    from eval.deployed_client import DeployedEvalClient, DeployedEvalError

    def timeout_handler(request):
        raise httpx.ReadTimeout("slow backend", request=request)

    async def call(handler):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as http:
            client = DeployedEvalClient(
                backend_url="https://backend.example",
                token="eval-secret",
                timeout_seconds=600,
                http_client=http,
            )
            return await client.run_turn(
                messages=[{"role": "user", "content": "Calculate"}],
                session_id="session",
            )

    with pytest.raises(DeployedEvalError, match="timed out after 600s"):
        asyncio.run(call(timeout_handler))
    with pytest.raises(DeployedEvalError, match="invalid response"):
        asyncio.run(call(lambda _request: httpx.Response(200, json={})))


def test_deployed_runner_executes_remote_requirements_and_aggregates_trials(
    tmp_path,
):
    from eval.deployed_runner import run_deployed_eval

    case_file = tmp_path / "cases.yaml"
    case_file.write_text(
        yaml.safe_dump(
            {
                "cases": [
                    {
                        "id": "remote_case",
                        "suite": "tool_loop",
                        "description": "Runs against the deployed backend",
                        "prompt": "Calculate",
                        "requirements": ["live_model", "policyengine_py", "data"],
                        "trials": 3,
                        "pass_threshold": 0.66,
                        "expected_tools": [{"name": "run_society_simulation"}],
                        "expect": {
                            "required": ["annual"],
                            "grounded_numbers": True,
                        },
                    }
                ]
            }
        )
    )

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def run_turn(self, **kwargs):
            trial = len(self.calls) + 1
            self.calls.append(kwargs)
            await asyncio.sleep(0.01 * (4 - trial))
            if trial == 3:
                return completed_response("Missing required wording")
            return completed_response()

        async def aclose(self):
            raise AssertionError("injected clients are not owned by the runner")

    client = FakeClient()
    report = asyncio.run(
        run_deployed_eval(
            case_file=case_file,
            backend_url="https://backend.example",
            token="eval-secret",
            concurrency=3,
            client=client,
            write_reports=False,
        )
    )

    assert len(client.calls) == 3
    assert all(call["messages"] == [{"role": "user", "content": "Calculate"}] for call in client.calls)
    assert report.mode == "deployed"
    assert report.passed == 1
    result = report.results[0]
    assert result.score == pytest.approx(2 / 3)
    assert [trial["trial"] for trial in result.details["trials"]] == [1, 2, 3]
    assert all("remote_case-trial-" in call["session_id"] for call in client.calls)


def test_deployed_report_schema_accepts_deployed_mode():
    from eval.schemas import EvalReport

    report = EvalReport(
        mode="deployed",
        suites=["tool_loop"],
        provider="uk-chat-backend",
        started_at="2026-07-31T00:00:00+00:00",
        finished_at="2026-07-31T00:00:01+00:00",
        results=[],
    )

    assert report.mode == "deployed"


def test_deployed_result_details_preserve_analysis_trace():
    from eval.deployed_runner import _grade_response

    result = _grade_response(
        ToolLoopCase(
            id="trace-case",
            description="Preserves trace",
            prompt="Calculate",
            expected_tools=[{"name": "run_society_simulation"}],
            expect={"required": ["annual"]},
        ),
        completed_response(),
    )

    trace = result.details["deployed"]["analysis_trace"]
    assert trace["workflow_version"] == 5
    assert trace["execution_mode"] == "standard"
    assert trace["permitted_operations"] == [
        "run_society_simulation",
        "compute_budgetary_impact",
    ]


def test_analysis_grading_rejects_wrong_route_even_when_answer_passes():
    from eval.deployed_runner import _grade_response

    case = ToolLoopCase(
        id="analysis-route-case",
        description="Requires standard execution routing",
        prompt="Calculate",
        expected_tools=[{"name": "run_society_simulation"}],
        expect={"required": ["annual"]},
        analysis_expect={"route": "standard", "outcome": "completed"},
    )
    response = completed_response().model_copy(
        update={"route": "clarification", "outcome": "needs_clarification"}
    )

    result = _grade_response(case, response)

    assert result.status == "failed"
    assert result.errors[:2] == [
        "analysis route was 'clarification', expected 'standard'",
        "analysis outcome was 'needs_clarification', expected 'completed'",
    ]


def test_analysis_grading_rejects_binding_outcome_mismatch():
    from eval.deployed_runner import grade_analysis_expectation

    case = ToolLoopCase(
        id="analysis-binding-case",
        description="Requires ready binding",
        prompt="Calculate",
        analysis_expect={
            "route": "standard",
            "outcome": "completed",
            "binding_outcome": "ready",
        },
    )
    response = completed_response().model_copy(
        update={
            "analysis_trace": completed_response().analysis_trace.model_copy(
                update={"binding_outcome": "clarification"}
            )
        }
    )

    assert grade_analysis_expectation(case, response) == [
        "binding outcome was 'clarification', expected 'ready'"
    ]


def test_analysis_grading_accepts_mode_and_required_operations():
    from eval.deployed_runner import grade_analysis_expectation

    case = ToolLoopCase(
        id="analysis-pass-case",
        description="Requires a standard authorized plan",
        prompt="Calculate",
        analysis_expect=AnalysisTraceExpectation(
            route="standard",
            outcome="completed",
            binding_outcome="ready",
            execution_mode="standard",
            required_operations=[
                "run_society_simulation",
                "compute_budgetary_impact",
            ],
        ),
    )
    response = completed_response()

    assert grade_analysis_expectation(case, response) == []


def test_analysis_grading_rejects_wrong_mode_and_missing_operation():
    from eval.deployed_runner import grade_analysis_expectation

    case = ToolLoopCase(
        id="analysis-authorization-case",
        description="Requires compiler-authorized operations",
        prompt="Calculate",
        analysis_expect={
            "execution_mode": "standard",
            "required_operations": ["compute_budgetary_impact"],
        },
    )
    response = completed_response().model_copy(
        update={
            "analysis_trace": completed_response().analysis_trace.model_copy(
                update={
                    "execution_mode": "exploratory",
                    "permitted_operations": ["run_society_simulation"],
                }
            )
        }
    )

    assert grade_analysis_expectation(case, response) == [
        "execution mode was 'exploratory', expected 'standard'",
        "permitted operations were missing ['compute_budgetary_impact']",
    ]


def test_deployed_make_target_does_not_require_local_generated_cases():
    makefile = (REPO_ROOT / "Makefile").read_text()

    assert "eval-ai-deployed-uk-population: check-policyengine-uk-evals" not in makefile
    assert "eval-ai-deployed-uk-population:\n" in makefile
