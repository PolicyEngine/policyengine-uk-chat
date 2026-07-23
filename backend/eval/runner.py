"""Manual runner for UK chat AI evaluation suites."""

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from prompts import CHARTS_MODE_DIRECTIVE, SYSTEM_PROMPT
from tools.context import new_tool_context
from tools.definitions import TOOL_DEFINITIONS
from tools.dispatch import execute_tool

from eval.graders import grade_output, grade_text, grade_tool_calls
from eval.loaders import load_cases
from eval.providers import AnthropicModelClient, FakeModelClient, ModelClient
from eval.reporting import write_report
from eval.schemas import (
    AnswerCase,
    CaseResult,
    EvalCase,
    EvalReport,
    GatewayCase,
    ModelTurn,
    ToolContractCase,
    ToolLoopCase,
    TrajectoryCase,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = REPO_ROOT / "evals"
CASE_ROOT = EVAL_ROOT / "cases"
FIXTURE_ROOT = EVAL_ROOT / "fixtures" / "tool_outputs"

SUITE_DIRS = {
    "tool_contract": CASE_ROOT / "tool_contract",
    "trajectory": CASE_ROOT / "trajectory",
    "answer": CASE_ROOT / "answer",
    "tool_loop": CASE_ROOT / "tool_loop",
    "gateway": CASE_ROOT / "gateway",
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
    if "policyengine_py" in requirements and (
        importlib.util.find_spec("policyengine") is None
        or importlib.util.find_spec("policyengine_uk") is None
    ):
        return "policyengine.py UK packages are not installed"
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
    turns: Dict[str, ModelTurn | List[ModelTurn]] = {}
    for case in cases:
        offline_responses = getattr(case, "offline_responses", None)
        if offline_responses:
            turns[case.id] = offline_responses
            continue
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


def _messages_for_case(case: TrajectoryCase | ToolLoopCase) -> List[Dict[str, Any]]:
    if case.messages:
        return case.messages
    return [{"role": "user", "content": case.prompt}]


def _system_for_case(case: TrajectoryCase | ToolLoopCase) -> str:
    sections = [SYSTEM_PROMPT]
    if case.charts_mode:
        sections.append(CHARTS_MODE_DIRECTIVE)
    return "\n\n".join(sections)


def _run_tool_contract(case: ToolContractCase) -> CaseResult:
    try:
        output = execute_tool(
            case.tool_name,
            case.input,
            context=new_tool_context(turn_id=case.id),
        )
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
            messages=_messages_for_case(case),
            system=_system_for_case(case),
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


def _tool_use_id(case: ToolLoopCase, iteration: int, index: int, call_id: str) -> str:
    return call_id or f"{case.id}-{iteration}-{index}"


def _run_tool_loop(case: ToolLoopCase, client: ModelClient) -> CaseResult:
    messages: List[Dict[str, Any]] = _messages_for_case(case)
    tool_context = new_tool_context(turn_id=case.id)
    tool_calls = []
    final_text = ""
    errors: List[str] = []

    for iteration in range(1, case.max_iterations + 1):
        try:
            turn = client.generate(
                case_id=case.id,
                messages=messages,
                system=_system_for_case(case),
                tools=_tool_specs_for_model(),
            )
        except Exception as exc:
            return _result(case, "failed", 0.0, [f"{type(exc).__name__}: {exc}"])

        if turn.text:
            final_text += turn.text
        if not turn.tool_calls:
            break

        assistant_content: List[Dict[str, Any]] = []
        if turn.text:
            assistant_content.append({"type": "text", "text": turn.text})

        tool_results: List[Dict[str, Any]] = []
        for index, call in enumerate(turn.tool_calls, start=1):
            tool_calls.append(call)
            tool_use_id = _tool_use_id(case, iteration, index, call.id)
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": call.name,
                    "input": call.input,
                }
            )
            output = execute_tool(call.name, call.input, context=tool_context)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(output, ensure_ascii=False, default=str),
                }
            )

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})
    else:
        errors.append("max iterations reached before final answer")

    errors.extend(grade_tool_calls(tool_calls, case.expected_tools, case.forbidden_tools))
    errors.extend(grade_text(final_text, case.expect))
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        {"text": final_text, "tool_calls": [call.model_dump() for call in tool_calls]},
    )


def _run_gateway(case: GatewayCase) -> CaseResult:
    """Live-only: run the gateway pre-pass and grade the verdict. Outcome is the
    primary assertion; tool/forbidden_tool and per-slot expectations are
    secondary (graded only when the case declares them). Binary 0/1 score to
    match the other suites, with the full plan stashed in details for tuning."""
    from gateway import run_gateway

    try:
        verdict = run_gateway(case.prompt)
    except Exception as exc:
        return _result(case, "failed", 0.0, [f"{type(exc).__name__}: {exc}"])

    errors: List[str] = []
    if verdict.outcome != case.expected_outcome:
        errors.append(f"expected outcome {case.expected_outcome!r}, got {verdict.outcome!r}")
    if case.expected_tool and verdict.tool != case.expected_tool:
        errors.append(f"expected tool {case.expected_tool!r}, got {verdict.tool!r}")
    if case.forbidden_tool and verdict.tool == case.forbidden_tool:
        errors.append(f"forbidden tool {case.forbidden_tool!r} was selected")
    if case.expected_gating_slots:
        got = set(verdict.gating_slots)
        want = set(case.expected_gating_slots)
        if got != want:
            errors.append(f"gating slots: expected {sorted(want)}, got {sorted(got)}")

    by_name = {s.name: s for s in verdict.slots}
    for exp in case.expected_slots:
        got_slot = by_name.get(exp.slot)
        if got_slot is None:
            errors.append(f"slot {exp.slot!r} missing from plan")
            continue
        if exp.source is not None and got_slot.source != exp.source:
            errors.append(f"slot {exp.slot!r} source: expected {exp.source!r}, got {got_slot.source!r}")
        if exp.gates is not None and (exp.slot in verdict.gating_slots) != exp.gates:
            errors.append(f"slot {exp.slot!r} gates: expected {exp.gates}, got {exp.slot in verdict.gating_slots}")

    details = {
        "outcome": verdict.outcome,
        "tool": verdict.tool,
        "gating_slots": verdict.gating_slots,
        "unmodellable_outputs": verdict.unmodellable_outputs,
        "slots": [
            {"name": s.name, "kind": s.kind, "source": s.source, "value": s.value}
            for s in verdict.slots
        ],
    }
    return _result(case, "failed" if errors else "passed", 0.0 if errors else 1.0, errors, details)


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
        if isinstance(case, ToolLoopCase) and mode == "offline" and not case.offline_responses:
            results.append(_result(case, "skipped", 0.0, ["offline_responses are required for offline tool-loop evals"]))
            continue

        if isinstance(case, ToolContractCase):
            results.append(_run_tool_contract(case))
        elif isinstance(case, TrajectoryCase):
            results.append(_run_trajectory(case, client))
        elif isinstance(case, AnswerCase):
            results.append(_run_answer(case, client))
        elif isinstance(case, ToolLoopCase):
            results.append(_run_tool_loop(case, client))
        elif isinstance(case, GatewayCase):
            results.append(_run_gateway(case))

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
