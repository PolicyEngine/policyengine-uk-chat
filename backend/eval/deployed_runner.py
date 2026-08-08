"""Run tool-loop cases through a deployed UK Chat backend."""

import asyncio
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from eval.deployed_client import DeployedEvalClient, DeployedEvalError
from eval.loaders import load_case_file
from eval.reporting import write_report
from eval.schemas import (
    CaseResult,
    EvalChatResponse,
    EvalReport,
    ModelToolCall,
    ToolLoopCase,
)
from eval.tool_loop_grading import (
    aggregate_tool_loop_trials,
    grade_tool_loop_case,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return None


def _messages_for_case(case: ToolLoopCase) -> list[dict]:
    return case.messages or [{"role": "user", "content": case.prompt}]


def _failed_trial(
    case: ToolLoopCase,
    error: str,
    *,
    details: dict | None = None,
) -> CaseResult:
    return CaseResult(
        id=case.id,
        suite=case.suite,
        status="failed",
        score=0.0,
        errors=[error],
        details=details or {},
    )


def grade_gateway_expectation(
    case: ToolLoopCase,
    response: EvalChatResponse,
) -> list[str]:
    """Grade routing and reform authorization before tool/answer quality."""

    expectation = case.gateway_expect
    if expectation is None:
        return []
    errors: list[str] = []
    if response.route != expectation.route:
        errors.append(
            f"gateway route was {response.route!r}, expected {expectation.route!r}"
        )
    if response.outcome != expectation.outcome:
        errors.append(
            f"gateway outcome was {response.outcome!r}, expected {expectation.outcome!r}"
        )
    trace = response.gateway_trace
    if trace is None:
        errors.append("gateway trace was missing")
        return errors
    for name, expected in expectation.defaults_contains.items():
        if name not in trace.defaults_applied:
            errors.append(
                f"gateway default {name!r} was missing; expected {expected!r}"
            )
        elif trace.defaults_applied[name] != expected:
            errors.append(
                f"gateway default {name!r} was {trace.defaults_applied[name]!r}, "
                f"expected {expected!r}"
            )
    minimum = expectation.min_reform_confidence
    if minimum is not None:
        if trace.reform_confidence is None:
            errors.append(
                "gateway reform confidence was missing; "
                f"expected at least {minimum}"
            )
        elif trace.reform_confidence < minimum:
            errors.append(
                f"gateway reform confidence was {trace.reform_confidence}, "
                f"expected at least {minimum}"
            )
    if expectation.require_parameter_binding and not trace.parameter_bindings:
        errors.append("gateway produced no validated parameter binding")
    return errors


def _grade_response(case: ToolLoopCase, response: EvalChatResponse) -> CaseResult:
    gateway_errors = grade_gateway_expectation(case, response)
    response_details = {
        "session_id": response.session_id,
        "model": response.model,
        "route": response.route,
        "outcome": response.outcome,
        "stop_reason": response.stop_reason,
        "usage": response.usage.model_dump(),
        "gateway_trace": (
            response.gateway_trace.model_dump()
            if response.gateway_trace is not None
            else None
        ),
    }
    if response.status != "completed":
        failed = _failed_trial(
            case,
            f"deployed chat failed with stop_reason={response.stop_reason!r}",
            details={
                **response_details,
                "text": response.content,
                "tool_trace": [trace.model_dump() for trace in response.tool_trace],
            },
        )
        return failed.model_copy(
            update={"errors": [*failed.errors, *gateway_errors]}
        )

    tool_calls = [
        ModelToolCall(id=trace.tool_id, name=trace.name, input=trace.input)
        for trace in response.tool_trace
    ]
    result = grade_tool_loop_case(
        case,
        text=response.content,
        tool_calls=tool_calls,
        tool_outputs=[trace.output for trace in response.tool_trace],
        errors=gateway_errors,
    )
    return result.model_copy(
        update={"details": {**result.details, "deployed": response_details}}
    )


async def run_deployed_eval(
    *,
    case_file: Path,
    backend_url: str,
    token: str,
    timeout_seconds: float = 600,
    concurrency: int = 4,
    case_id: str | None = None,
    report_dir: Path | None = None,
    write_reports: bool = True,
    client: DeployedEvalClient | None = None,
) -> EvalReport:
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    loaded = load_case_file(case_file)
    cases = [case for case in loaded if isinstance(case, ToolLoopCase)]
    if len(cases) != len(loaded):
        raise ValueError("deployed eval case files may contain only tool_loop cases")
    if case_id is not None:
        cases = [case for case in cases if case.id == case_id]
        if not cases:
            raise ValueError(f"case id not found: {case_id}")

    started_at = _utc_now()
    owned_client = client is None
    deployed_client = client or DeployedEvalClient(
        backend_url=backend_url,
        token=token,
        timeout_seconds=timeout_seconds,
    )
    semaphore = asyncio.Semaphore(concurrency)

    async def run_trial(case: ToolLoopCase, trial: int):
        session_id = (
            f"{case.id}-trial-{trial}-{uuid.uuid4().hex[:8]}"
        )
        async with semaphore:
            try:
                response = await deployed_client.run_turn(
                    messages=_messages_for_case(case),
                    session_id=session_id,
                    charts_mode=case.charts_mode,
                )
            except DeployedEvalError as exc:
                result = _failed_trial(case, str(exc), details={"session_id": session_id})
            except Exception as exc:
                result = _failed_trial(
                    case,
                    f"{type(exc).__name__}: {exc}",
                    details={"session_id": session_id},
                )
            else:
                result = _grade_response(case, response)
        return case.id, trial, result

    tasks = [
        asyncio.create_task(run_trial(case, trial))
        for case in cases
        if case.skip is None
        for trial in range(1, case.trials + 1)
    ]
    completed = await asyncio.gather(*tasks) if tasks else []
    by_case: dict[str, list[tuple[int, CaseResult]]] = {}
    for completed_case_id, trial, result in completed:
        by_case.setdefault(completed_case_id, []).append((trial, result))

    results: list[CaseResult] = []
    for case in cases:
        if case.skip is not None:
            results.append(
                CaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="skipped",
                    score=0.0,
                    errors=[
                        f"{case.skip.code}: {case.skip.reason} "
                        f"Remove when: {case.skip.remove_when}"
                    ],
                )
            )
            continue
        trial_results = [
            result for _, result in sorted(by_case.get(case.id, []))
        ]
        results.append(
            aggregate_tool_loop_trials(case, trial_results)
            if case.trials > 1
            else trial_results[0]
        )

    if owned_client:
        await deployed_client.aclose()

    report = EvalReport(
        mode="deployed",
        suites=["tool_loop"],
        provider="uk-chat-backend",
        model=None,
        git_sha=_git_sha(),
        started_at=started_at,
        finished_at=_utc_now(),
        results=results,
    )
    if write_reports:
        write_report(report, report_dir or REPO_ROOT / "evals" / "reports")
    return report
