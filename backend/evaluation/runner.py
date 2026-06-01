"""Manual runner for UK chat AI evaluation suites."""

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from agent_tools import execute_tool
from prompts import SYSTEM_PROMPT
from tool_definitions import TOOL_DEFINITIONS

from evaluation.graders import grade_output, grade_text, grade_tool_calls
from evaluation.loaders import load_cases
from evaluation.providers import AnthropicModelClient, FakeModelClient, ModelClient
from evaluation.reporting import write_report
from evaluation.schemas import (
    AnswerCase,
    CaseResult,
    EvalCase,
    EvalReport,
    ModelTurn,
    ToolContractCase,
    TrajectoryCase,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = REPO_ROOT / "evals"
CASE_ROOT = EVAL_ROOT / "cases"
FIXTURE_ROOT = EVAL_ROOT / "fixtures" / "tool_outputs"

SUITE_DIRS = {
    "tool_contract": CASE_ROOT / "tool_contract",
    "trajectory": CASE_ROOT / "trajectory",
    "answer": CASE_ROOT / "answers",
}


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


def _case_paths(suites: Iterable[str]) -> List[Path]:
    paths: List[Path] = []
    for suite in suites:
        paths.extend(SUITE_DIRS[suite].glob("*.yaml"))
        paths.extend(SUITE_DIRS[suite].glob("*.yml"))
    return paths


def _requirements_skip_reason(requirements: List[str], mode: str) -> str | None:
    if "compiled" in requirements and importlib.util.find_spec("policyengine_uk_compiled") is None:
        return "policyengine_uk_compiled is not installed"
    if "data" in requirements and os.environ.get("RUN_DATA_EVALS") != "1":
        return "set RUN_DATA_EVALS=1 to run data-backed evals"
    if "live_model" in requirements and mode != "live":
        return "requires live model mode"
    return None


def _skip_reason(case: EvalCase, mode: str) -> str | None:
    if case.skip is not None:
        return (
            f"{case.skip.code}: {case.skip.reason} "
            f"Remove when: {case.skip.remove_when}"
        )
    return _requirements_skip_reason(case.requirements, mode)


def _result(
    case: EvalCase,
    status: str,
    score: float,
    errors: List[str] | None = None,
    details: Dict[str, Any] | None = None,
) -> CaseResult:
    return CaseResult(
        id=case.id,
        suite=case.suite,
        status=status,
        score=score,
        errors=errors or [],
        details=details or {},
    )


def _load_fixture(name: str) -> Dict[str, Any]:
    path = FIXTURE_ROOT / name
    return json.loads(path.read_text())


def _build_offline_client(cases: List[EvalCase]) -> FakeModelClient:
    turns: Dict[str, ModelTurn] = {}
    for case in cases:
        offline_response = getattr(case, "offline_response", None)
        if offline_response is not None:
            turns[case.id] = offline_response
    return FakeModelClient(turns)


def _tool_specs_for_model() -> List[Dict[str, Any]]:
    return [
        {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["input_schema"],
        }
        for tool in TOOL_DEFINITIONS
    ]


def _run_tool_contract(case: ToolContractCase) -> CaseResult:
    try:
        output = execute_tool(case.tool_name, case.input)
    except Exception as exc:
        return _result(case, "failed", 0.0, [f"{type(exc).__name__}: {exc}"])
    errors = grade_output(output, case.expect)
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        {"output": output},
    )


def _run_trajectory(case: TrajectoryCase, client: ModelClient) -> CaseResult:
    try:
        turn = client.generate(
            case_id=case.id,
            messages=[{"role": "user", "content": case.prompt}],
            system=SYSTEM_PROMPT,
            tools=_tool_specs_for_model(),
        )
    except Exception as exc:
        return _result(case, "failed", 0.0, [f"{type(exc).__name__}: {exc}"])
    errors = grade_tool_calls(turn.tool_calls, case.expected_tools, case.forbidden_tools)
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        {"text": turn.text, "tool_calls": [call.model_dump() for call in turn.tool_calls]},
    )


def _tool_result_text(case: AnswerCase) -> str:
    chunks = ["Previously computed tool results:"]
    for index, call in enumerate(case.tool_calls, start=1):
        output = call.output
        if call.output_fixture:
            output = _load_fixture(call.output_fixture)
        chunks.append(
            "\n".join(
                [
                    f"{index}. {call.name}",
                    f"Input: {json.dumps(call.input, sort_keys=True)}",
                    f"Output: {json.dumps(output or {}, sort_keys=True)}",
                ]
            )
        )
    chunks.append("Answer using only these computed results.")
    return "\n\n".join(chunks)


def _run_answer(case: AnswerCase, client: ModelClient) -> CaseResult:
    try:
        turn = client.generate(
            case_id=case.id,
            messages=[
                {"role": "user", "content": case.prompt},
                {"role": "user", "content": _tool_result_text(case)},
            ],
            system=SYSTEM_PROMPT,
            tools=None,
        )
    except Exception as exc:
        return _result(case, "failed", 0.0, [f"{type(exc).__name__}: {exc}"])
    errors = grade_text(turn.text, case.expect)
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        {"text": turn.text},
    )


def run_eval(
    *,
    suites: List[str] | None = None,
    mode: str = "offline",
    provider: str | None = None,
    model: str | None = None,
    report_dir: Path | None = None,
    write_reports: bool = True,
) -> EvalReport:
    selected_suites = suites or list(SUITE_DIRS)
    cases = load_cases(_case_paths(selected_suites))
    started_at = _utc_now()

    if mode == "live":
        if provider not in (None, "anthropic"):
            raise ValueError(f"Unsupported live provider: {provider}")
        client: ModelClient = AnthropicModelClient(model=model)
        provider_name = "anthropic"
        model_name = getattr(client, "model", model)
    else:
        client = _build_offline_client(cases)
        provider_name = "fake"
        model_name = None

    results: List[CaseResult] = []
    for case in cases:
        skip_reason = _skip_reason(case, mode)
        if skip_reason:
            results.append(_result(case, "skipped", 0.0, [skip_reason]))
            continue
        if case.suite in {"trajectory", "answer"} and mode == "offline" and getattr(case, "offline_response", None) is None:
            results.append(_result(case, "skipped", 0.0, ["offline_response is required for offline model evals"]))
            continue

        if isinstance(case, ToolContractCase):
            results.append(_run_tool_contract(case))
        elif isinstance(case, TrajectoryCase):
            results.append(_run_trajectory(case, client))
        elif isinstance(case, AnswerCase):
            results.append(_run_answer(case, client))

    report = EvalReport(
        mode=mode,
        suites=selected_suites,
        provider=provider_name,
        model=model_name,
        git_sha=_git_sha(),
        started_at=started_at,
        finished_at=_utc_now(),
        results=results,
    )
    if write_reports:
        write_report(report, report_dir or EVAL_ROOT / "reports")
    return report
