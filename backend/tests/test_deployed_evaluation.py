import asyncio
import json
from pathlib import Path

import httpx
import pytest
import yaml

from eval.schemas import (
    EvalChatResponse,
    EvalGatewayBinding,
    EvalGatewayTrace,
    EvalToolTrace,
    GatewayTraceExpectation,
    ToolLoopCase,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def completed_response(content="Completed annual result £100."):
    return EvalChatResponse(
        status="completed",
        content=content,
        session_id="remote-session",
        model="claude",
        route="compute",
        outcome="ready",
        stop_reason="end_turn",
        gateway_trace=EvalGatewayTrace(
            selected_tool="run_society_simulation",
            target_tool="compute_budgetary_impact",
            defaults_applied={"year": 2026},
            reform_confidence=92,
            catalogue_recovery_used=False,
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


def test_deployed_result_details_preserve_gateway_trace():
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

    trace = result.details["deployed"]["gateway_trace"]
    assert trace["target_tool"] == "compute_budgetary_impact"
    assert trace["defaults_applied"] == {"year": 2026}
    assert trace["reform_confidence"] == 92


def test_gateway_grading_rejects_lightweight_route_even_when_answer_passes():
    from eval.deployed_runner import _grade_response

    case = ToolLoopCase(
        id="gateway-route-case",
        description="Requires compute routing",
        prompt="Calculate",
        expected_tools=[{"name": "run_society_simulation"}],
        expect={"required": ["annual"]},
        gateway_expect={"route": "compute", "outcome": "ready"},
    )
    response = completed_response().model_copy(
        update={"route": "lightweight", "outcome": "needs_plan"}
    )

    result = _grade_response(case, response)

    assert result.status == "failed"
    assert result.errors[:2] == [
        "gateway route was 'lightweight', expected 'compute'",
        "gateway outcome was 'needs_plan', expected 'ready'",
    ]


def test_gateway_grading_rejects_missing_default():
    from eval.deployed_runner import grade_gateway_expectation

    case = ToolLoopCase(
        id="gateway-default-case",
        description="Requires current year",
        prompt="Calculate",
        gateway_expect={
            "route": "compute",
            "outcome": "ready",
            "defaults_contains": {"year": 2026},
        },
    )
    response = completed_response().model_copy(
        update={
            "gateway_trace": completed_response().gateway_trace.model_copy(
                update={"defaults_applied": {}}
            )
        }
    )

    assert grade_gateway_expectation(case, response) == [
        "gateway default 'year' was missing; expected 2026"
    ]


def test_gateway_grading_accepts_ready_compute_default_and_confident_binding():
    from eval.deployed_runner import grade_gateway_expectation

    case = ToolLoopCase(
        id="gateway-pass-case",
        description="Requires an executable reform",
        prompt="Calculate",
        gateway_expect=GatewayTraceExpectation(
            route="compute",
            outcome="ready",
            defaults_contains={"year": 2026},
            min_reform_confidence=80,
            require_parameter_binding=True,
        ),
    )
    response = completed_response().model_copy(
        update={
            "gateway_trace": completed_response().gateway_trace.model_copy(
                update={
                    "parameter_bindings": [
                        EvalGatewayBinding(
                            parameter_path="gov.example.rate",
                            label="Example rate",
                            catalogue_evidence="example rate",
                        )
                    ]
                }
            )
        }
    )

    assert grade_gateway_expectation(case, response) == []


def test_gateway_grading_rejects_low_confidence_and_missing_binding():
    from eval.deployed_runner import grade_gateway_expectation

    case = ToolLoopCase(
        id="gateway-authorization-case",
        description="Requires a safe exact construction",
        prompt="Calculate",
        gateway_expect={
            "route": "compute",
            "outcome": "ready",
            "min_reform_confidence": 80,
            "require_parameter_binding": True,
        },
    )
    response = completed_response().model_copy(
        update={
            "gateway_trace": completed_response().gateway_trace.model_copy(
                update={"reform_confidence": 79, "parameter_bindings": []}
            )
        }
    )

    assert grade_gateway_expectation(case, response) == [
        "gateway reform confidence was 79, expected at least 80",
        "gateway produced no validated parameter binding",
    ]


def test_deployed_make_target_does_not_require_local_generated_cases():
    makefile = (REPO_ROOT / "Makefile").read_text()

    assert "eval-ai-deployed-uk-population: check-policyengine-uk-evals" not in makefile
    assert "eval-ai-deployed-uk-population:\n" in makefile
