"""Manual runner for UK chat AI evaluation suites."""

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from capabilities.chart import SocietyChartCapability
from capabilities.contracts import Completed, Failed, NeedsInput, Unsupported
from capabilities.follow_up import AnalysisFollowUpCapability
from capabilities.household import HouseholdAnalysisCapability
from capabilities.policy_information import PolicyInformationCapability
from capabilities.policy_reform import PolicyReformCapability
from capabilities.society import SocietyAnalysisCapability
from chat.capability_service import (
    MANDATORY_CAPABILITY_CONTRACT,
    capability_result_for_model,
)
from tools.context import new_tool_context
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
    ModelTurn,
    ToolContractCase,
    ToolLoopCase,
    TrajectoryCase,
)
from eval.tool_loop_grading import (
    aggregate_tool_loop_trials,
    grade_tool_loop_case,
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
}

CAPABILITY_CHARTS_MODE_DIRECTIVE = (
    "The user enabled chart presentation. Use society_chart when a chart is "
    "requested and its typed prerequisites can be satisfied."
)


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


def _public_capabilities():
    return (
        PolicyInformationCapability(),
        PolicyReformCapability(),
        HouseholdAnalysisCapability(),
        SocietyAnalysisCapability(),
        AnalysisFollowUpCapability(),
        SocietyChartCapability(),
    )


def _capability_specs_for_model() -> List[Dict[str, Any]]:
    return [
        {
            "name": capability.spec.identifier,
            "description": (
                f"{capability.spec.description} Required-use rule: "
                f"{capability.spec.required_use}"
            ),
            "input_schema": capability.spec.input_model.model_json_schema(),
        }
        for capability in _public_capabilities()
    ]


def _operations_for_case(case: TrajectoryCase | ToolLoopCase) -> List[Dict[str, Any]]:
    del case
    return _capability_specs_for_model()


def _messages_for_case(case: TrajectoryCase | ToolLoopCase) -> List[Dict[str, Any]]:
    if case.messages:
        return case.messages
    return [{"role": "user", "content": case.prompt}]


def _system_for_case(case: TrajectoryCase | ToolLoopCase) -> str:
    sections = [
        "You are PolicyEngine UK Chat. Continue naturally using the supplied "
        "conversation history.\n\n" + MANDATORY_CAPABILITY_CONTRACT
    ]
    if case.charts_mode:
        sections.append(CAPABILITY_CHARTS_MODE_DIRECTIVE)
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
            tools=_operations_for_case(case),
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
        _validate_capability_output(call.name, output or {})
        model_output = capability_result_for_model(output or {})
        chunks.append(
            "\n".join(
                [
                    f"{index}. {call.name}",
                    f"Input: {json.dumps(call.input, sort_keys=True)}",
                    f"Output: {json.dumps(model_output, sort_keys=True)}",
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
            system=(
                "You are PolicyEngine UK Chat. Use validated capability output as "
                "authoritative facts while writing natural prose.\n\n"
                + MANDATORY_CAPABILITY_CONTRACT
            ),
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


def _load_frozen_output(call) -> Dict[str, Any]:
    if call.output_fixture:
        return _load_fixture(call.output_fixture)
    return call.output or {}


def _validate_capability_output(name: str, output: Dict[str, Any]) -> None:
    capability = next(
        (
            item
            for item in _public_capabilities()
            if item.spec.identifier == name
        ),
        None,
    )
    if capability is None:
        raise ValueError(f"Unknown capability output fixture: {name}")
    status = output.get("status")
    if status == "completed":
        Completed[capability.spec.output_model].model_validate(output)
    elif status == "needs_input":
        NeedsInput.model_validate(output)
    elif status == "unsupported":
        Unsupported.model_validate(output)
    elif status == "failed":
        Failed.model_validate(output)
    else:
        raise ValueError(f"Invalid capability outcome status for {name}: {status!r}")


def _run_tool_loop(case: ToolLoopCase, client: ModelClient) -> CaseResult:
    messages: List[Dict[str, Any]] = _messages_for_case(case)
    tool_calls = []
    tool_outputs: List[Dict[str, Any]] = []
    final_text = ""
    errors: List[str] = []
    frozen_capability_outputs = list(case.capability_outputs)

    for iteration in range(1, case.max_iterations + 1):
        try:
            turn = client.generate(
                case_id=case.id,
                messages=messages,
                system=_system_for_case(case),
                tools=_operations_for_case(case),
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
            try:
                if not frozen_capability_outputs:
                    raise ValueError(
                        f"No frozen capability output remains for {call.name}."
                    )
                frozen = frozen_capability_outputs.pop(0)
                if frozen.name != call.name:
                    raise ValueError(
                        f"Expected frozen output for {frozen.name}, got {call.name}."
                    )
                output = _load_frozen_output(frozen)
                _validate_capability_output(call.name, output)
            except Exception as exc:
                return _result(
                    case,
                    "failed",
                    0.0,
                    [f"{call.name}: {type(exc).__name__}: {exc}"],
                    {"text": final_text, "tool_calls": [call.model_dump() for call in tool_calls]},
                )
            tool_outputs.append(output)
            model_output = capability_result_for_model(output)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(
                        model_output,
                        ensure_ascii=False,
                        default=str,
                    ),
                }
            )

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})
    else:
        errors.append("max iterations reached before final answer")

    return grade_tool_loop_case(
        case,
        text=final_text,
        tool_calls=tool_calls,
        tool_outputs=tool_outputs,
        errors=errors,
    )


def _run_tool_loop_trials(case: ToolLoopCase, client: ModelClient) -> CaseResult:
    trial_results = [_run_tool_loop(case, client) for _ in range(case.trials)]
    return aggregate_tool_loop_trials(case, trial_results)


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
            if case.trials > 1:
                results.append(_run_tool_loop_trials(case, client))
            else:
                results.append(_run_tool_loop(case, client))
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
