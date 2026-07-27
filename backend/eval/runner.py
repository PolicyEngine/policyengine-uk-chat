"""Runner for UK chat AI evaluation suites."""

import importlib.util
import json
import os
import subprocess
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, cast

from chat.model_selection import select_chat_model
from engine.serialization import json_safe
from pydantic import JsonValue
from prompts import CHARTS_MODE_DIRECTIVE, SYSTEM_PROMPT
from tools.context import new_tool_context
from tools.definitions import TOOL_DEFINITIONS
from tools.dispatch import execute_tool

from eval.graders import grade_live_text, grade_output, grade_text, grade_tool_calls
from eval.loaders import load_cases
from eval.providers import AnthropicModelClient, FakeModelClient, ModelClient
from eval.reporting import write_report
from eval.schemas import (
    AnswerCase,
    AnswerDetails,
    CaseResult,
    CaseResultDetails,
    CaseStatus,
    EvalCase,
    EvalReport,
    ExecutedToolResult,
    GatewayDetails,
    GatewaySlotDetails,
    GatewayCase,
    LiveAnswerCase,
    LiveToolLoopCase,
    ModelTurn,
    ToolContractCase,
    ToolContractDetails,
    ToolLoopDetails,
    ToolLoopCase,
    TrajectoryDetails,
    TrajectoryCase,
)

JsonObject = dict[str, JsonValue]

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_ROOT = REPO_ROOT / "evals"
CASE_ROOT = EVAL_ROOT / "cases"
LIVE_CASE_ROOT = EVAL_ROOT / "live"
FIXTURE_ROOT = EVAL_ROOT / "fixtures" / "tool_outputs"

DETERMINISTIC_SUITE_DIRS = {
    "tool_contract": CASE_ROOT / "tool_contract",
    "trajectory": CASE_ROOT / "trajectory",
    "answer": CASE_ROOT / "answer",
    "tool_loop": CASE_ROOT / "tool_loop",
}
LIVE_SUITE_DIRS = {
    "gateway": LIVE_CASE_ROOT / "gateway",
    "trajectory": LIVE_CASE_ROOT / "trajectory",
    "answer": LIVE_CASE_ROOT / "answer",
    "tool_loop": LIVE_CASE_ROOT / "tool_loop",
}
SUITE_NAMES = tuple(
    dict.fromkeys([*DETERMINISTIC_SUITE_DIRS, *LIVE_SUITE_DIRS])
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


def _case_paths(suites: Iterable[str], *, live: bool = False) -> List[Path]:
    paths: List[Path] = []
    suite_dirs = LIVE_SUITE_DIRS if live else DETERMINISTIC_SUITE_DIRS
    for suite in suites:
        if suite not in suite_dirs:
            layer = "live" if live else "deterministic"
            raise ValueError(f"Suite {suite!r} is not a {layer} eval suite.")
        paths.extend(suite_dirs[suite].glob("*.yaml"))
        paths.extend(suite_dirs[suite].glob("*.yml"))
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
    status: CaseStatus,
    score: float,
    errors: List[str] | None = None,
    details: CaseResultDetails | None = None,
    *,
    trial: int = 1,
    model: str | None = None,
) -> CaseResult:
    return CaseResult(
        id=case.id,
        suite=case.suite,
        trial=trial,
        model=model,
        status=status,
        score=score,
        errors=errors or [],
        details=details,
    )


def _load_fixture(name: str) -> JsonObject:
    path = FIXTURE_ROOT / name
    return _json_object(json.loads(path.read_text()))


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


def _json_object(value: Any) -> JsonObject:
    serialized = json_safe(value)
    if not isinstance(serialized, dict):
        raise TypeError("Expected a JSON object.")
    return cast(JsonObject, serialized)


def _model_for_messages(
    client: ModelClient,
    messages: List[Dict[str, Any]],
    *,
    charts_mode: bool = False,
) -> str | None:
    if isinstance(client, FakeModelClient):
        return None
    override = getattr(client, "model_override", None)
    return override or select_chat_model(messages, charts_mode=charts_mode)


def _run_tool_contract(case: ToolContractCase) -> CaseResult:
    try:
        output = execute_tool(
            case.tool_name,
            case.input,
            context=new_tool_context(turn_id=case.id),
        )
        safe_output = _json_object(output)
    except Exception as exc:
        return _result(case, "failed", 0.0, [f"{type(exc).__name__}: {exc}"])
    errors = grade_output(safe_output, case.expect)
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        ToolContractDetails(output=safe_output),
    )


def _run_trajectory(
    case: TrajectoryCase,
    client: ModelClient,
    *,
    trial: int = 1,
) -> CaseResult:
    messages = _messages_for_case(case)
    model = _model_for_messages(
        client,
        messages,
        charts_mode=case.charts_mode,
    )
    try:
        turn = client.generate(
            case_id=case.id,
            messages=messages,
            system=_system_for_case(case),
            tools=_tool_specs_for_model(),
            model=model,
        )
    except Exception as exc:
        return _result(
            case,
            "failed",
            0.0,
            [f"{type(exc).__name__}: {exc}"],
            trial=trial,
            model=model,
        )
    errors = grade_tool_calls(turn.tool_calls, case.expected_tools, case.forbidden_tools)
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        TrajectoryDetails(text=turn.text, tool_calls=turn.tool_calls),
        trial=trial,
        model=model,
    )


def _tool_result_text(tool_results: List[ExecutedToolResult]) -> str:
    chunks = ["Previously computed tool results:"]
    for index, result in enumerate(tool_results, start=1):
        chunks.append(
            "\n".join(
                [
                    f"{index}. {result.name}",
                    f"Input: {json.dumps(result.input, sort_keys=True)}",
                    f"Output: {json.dumps(result.output, sort_keys=True)}",
                ]
            )
        )
    chunks.append("Answer using only these computed results.")
    return "\n\n".join(chunks)


def _answer_tool_results(case: AnswerCase) -> List[ExecutedToolResult]:
    results: List[ExecutedToolResult] = []
    for call in case.tool_calls:
        output = call.output
        if call.output_fixture:
            output = _load_fixture(call.output_fixture)
        results.append(
            ExecutedToolResult(
                name=call.name,
                input=_json_object(call.input),
                output=_json_object(output or {}),
            )
        )
    return results


def _run_answer(
    case: AnswerCase,
    client: ModelClient,
    *,
    trial: int = 1,
) -> CaseResult:
    try:
        tool_results = _answer_tool_results(case)
    except Exception as exc:
        return _result(
            case,
            "failed",
            0.0,
            [f"{type(exc).__name__}: {exc}"],
            trial=trial,
        )
    prompt_message = {"role": "user", "content": case.prompt}
    messages = [
        prompt_message,
        {"role": "user", "content": _tool_result_text(tool_results)},
    ]
    model = _model_for_messages(client, [prompt_message])
    try:
        turn = client.generate(
            case_id=case.id,
            messages=messages,
            system=SYSTEM_PROMPT,
            tools=None,
            model=model,
        )
    except Exception as exc:
        return _result(
            case,
            "failed",
            0.0,
            [f"{type(exc).__name__}: {exc}"],
            trial=trial,
            model=model,
        )
    if isinstance(case, LiveAnswerCase):
        errors = grade_live_text(turn.text, case.expect, tool_results)
    else:
        errors = grade_text(turn.text, case.expect)
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        AnswerDetails(text=turn.text),
        trial=trial,
        model=model,
    )


def _tool_use_id(case: ToolLoopCase, iteration: int, index: int, call_id: str) -> str:
    return call_id or f"{case.id}-{iteration}-{index}"


def _resolve_offline_tool_input(
    value: JsonValue,
    tool_outputs: Mapping[str, JsonObject],
) -> JsonValue:
    """Resolve deterministic references to prior tool-loop outputs."""

    if isinstance(value, dict):
        if set(value) == {"$tool_result"}:
            tool_name = value["$tool_result"]
            if not isinstance(tool_name, str) or not tool_name:
                raise ValueError(
                    "Whole tool-result references require a non-empty tool name."
                )
            if tool_name not in tool_outputs:
                raise ValueError(
                    "Offline tool-result reference has no prior output for "
                    f"{tool_name!r}."
                )
            return tool_outputs[tool_name]
        return {
            key: _resolve_offline_tool_input(item, tool_outputs)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_offline_tool_input(item, tool_outputs)
            for item in value
        ]
    if not isinstance(value, str) or not value.startswith("$tool_result."):
        return value

    reference = value.removeprefix("$tool_result.")
    tool_name, separator, path = reference.partition(".")
    if not separator or not tool_name or not path:
        raise ValueError(
            "Offline tool-result references must use "
            "'$tool_result.<tool_name>.<field>'."
        )
    if tool_name not in tool_outputs:
        raise ValueError(
            f"Offline tool-result reference has no prior output for {tool_name!r}."
        )

    resolved: JsonValue = tool_outputs[tool_name]
    for field in path.split("."):
        if not isinstance(resolved, dict) or field not in resolved:
            raise ValueError(
                f"Offline tool-result reference {value!r} was not found."
            )
        resolved = resolved[field]
    return resolved


def _run_tool_loop(
    case: ToolLoopCase,
    client: ModelClient,
    *,
    trial: int = 1,
) -> CaseResult:
    messages: List[Dict[str, Any]] = _messages_for_case(case)
    model = _model_for_messages(
        client,
        messages,
        charts_mode=case.charts_mode,
    )
    tool_context = new_tool_context(turn_id=f"{case.id}-trial-{trial}")
    tool_calls = []
    executions: List[ExecutedToolResult] = []
    tool_outputs: dict[str, JsonObject] = {}
    final_text = ""
    errors: List[str] = []

    for iteration in range(1, case.max_iterations + 1):
        try:
            turn = client.generate(
                case_id=case.id,
                messages=messages,
                system=_system_for_case(case),
                tools=_tool_specs_for_model(),
                model=model,
            )
        except Exception as exc:
            return _result(
                case,
                "failed",
                0.0,
                [f"{type(exc).__name__}: {exc}"],
                trial=trial,
                model=model,
            )

        if turn.text:
            final_text += turn.text
        if not turn.tool_calls:
            break

        assistant_content: List[Dict[str, Any]] = []
        if turn.text:
            assistant_content.append({"type": "text", "text": turn.text})

        tool_results: List[Dict[str, Any]] = []
        prior_tool_outputs = dict(tool_outputs)
        for index, call in enumerate(turn.tool_calls, start=1):
            try:
                if isinstance(client, FakeModelClient):
                    resolved_input = _resolve_offline_tool_input(
                        cast(JsonObject, call.input),
                        prior_tool_outputs,
                    )
                    if not isinstance(resolved_input, dict):
                        raise ValueError(
                            "A resolved tool input must remain an object."
                        )
                    tool_input = resolved_input
                else:
                    tool_input = cast(JsonObject, call.input)
            except ValueError as exc:
                return _result(
                    case,
                    "failed",
                    0.0,
                    [str(exc)],
                    trial=trial,
                    model=model,
                )
            resolved_call = call.model_copy(update={"input": tool_input})
            tool_calls.append(resolved_call)
            tool_use_id = _tool_use_id(case, iteration, index, call.id)
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": call.name,
                    "input": tool_input,
                }
            )
            try:
                output = cast(
                    JsonObject,
                    execute_tool(call.name, tool_input, context=tool_context),
                )
                safe_output = _json_object(output)
            except Exception as exc:
                errors.append(
                    f"{call.name}: {type(exc).__name__}: {exc}"
                )
                return _result(
                    case,
                    "failed",
                    0.0,
                    errors,
                    ToolLoopDetails(
                        text=final_text,
                        tool_calls=tool_calls,
                        tool_results=executions,
                    ),
                    trial=trial,
                    model=model,
                )
            tool_outputs[call.name] = safe_output
            executions.append(
                ExecutedToolResult(
                    name=call.name,
                    input=_json_object(tool_input),
                    output=safe_output,
                )
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": json.dumps(safe_output, ensure_ascii=False),
                }
            )

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})
    else:
        errors.append("max iterations reached before final answer")

    errors.extend(grade_tool_calls(tool_calls, case.expected_tools, case.forbidden_tools))
    if isinstance(case, LiveToolLoopCase):
        errors.extend(grade_live_text(final_text, case.expect, executions))
    else:
        errors.extend(grade_text(final_text, case.expect))
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        ToolLoopDetails(
            text=final_text,
            tool_calls=tool_calls,
            tool_results=executions,
        ),
        trial=trial,
        model=model,
    )


def _run_gateway(case: GatewayCase, *, trial: int = 1) -> CaseResult:
    """Live-only: run the gateway pre-pass and grade the verdict. Outcome is the
    primary assertion; tool/forbidden_tool and per-slot expectations are
    secondary (graded only when the case declares them). Binary 0/1 score to
    match the other suites, with the full plan stashed in details for tuning."""
    from gateway import run_gateway
    from gateway.runtime import GATEWAY_MODEL

    try:
        verdict = run_gateway(case.prompt)
    except Exception as exc:
        return _result(
            case,
            "failed",
            0.0,
            [f"{type(exc).__name__}: {exc}"],
            trial=trial,
            model=GATEWAY_MODEL,
        )

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

    details = GatewayDetails(
        outcome=verdict.outcome,
        tool=verdict.tool,
        gating_slots=verdict.gating_slots,
        unmodellable_outputs=verdict.unmodellable_outputs,
        slots=[
            GatewaySlotDetails(
                name=slot.name,
                kind=slot.kind,
                source=slot.source,
                value=json_safe(slot.value),
            )
            for slot in verdict.slots
        ],
    )
    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        details,
        trial=trial,
        model=GATEWAY_MODEL,
    )


def run_eval(
    *,
    suites: List[str] | None = None,
    mode: str = "offline",
    provider: str | None = None,
    model: str | None = None,
    trials: int = 1,
    case_ids: List[str] | None = None,
    tags: List[str] | None = None,
    strict_requirements: bool = False,
    report_dir: Path | None = None,
    write_reports: bool = True,
) -> EvalReport:
    if trials < 1:
        raise ValueError("trials must be at least 1")
    if mode != "live" and trials != 1:
        raise ValueError("multiple trials are only supported in live mode")

    live = mode == "live"
    suite_dirs = LIVE_SUITE_DIRS if live else DETERMINISTIC_SUITE_DIRS
    selected_suites = suites or list(suite_dirs)
    cases = load_cases(
        _case_paths(selected_suites, live=live),
        live=live,
    )
    if case_ids:
        requested_ids = set(case_ids)
        available_ids = {case.id for case in cases}
        missing_ids = requested_ids - available_ids
        if missing_ids:
            raise ValueError(f"Unknown eval case ID(s): {sorted(missing_ids)}")
        cases = [case for case in cases if case.id in requested_ids]
    if tags:
        selected_tags = set(tags)
        cases = [case for case in cases if selected_tags.intersection(case.tags)]
    report_suites = [
        suite
        for suite in selected_suites
        if any(case.suite == suite for case in cases)
    ]

    started_at = _utc_now()

    if mode == "live":
        if provider not in (None, "anthropic"):
            raise ValueError(f"Unsupported live provider: {provider}")
        client: ModelClient = AnthropicModelClient(model=model)
        provider_name = "anthropic"
        model_name = getattr(client, "model_override", model) or "production-routing"
    else:
        client = _build_offline_client(cases)
        provider_name = "fake"
        model_name = None

    results: List[CaseResult] = []
    for case in cases:
        explicit_skip_reason = (
            _skip_reason(case, mode) if case.skip is not None else None
        )
        requirements_skip_reason = _requirements_skip_reason(case.requirements, mode)
        skip_reason = explicit_skip_reason or requirements_skip_reason
        if skip_reason:
            status = (
                "failed"
                if strict_requirements and requirements_skip_reason
                else "skipped"
            )
            results.append(_result(case, status, 0.0, [skip_reason]))
            continue
        case_trials = trials if live else 1
        for trial in range(1, case_trials + 1):
            if isinstance(case, ToolContractCase):
                results.append(_run_tool_contract(case))
            elif isinstance(case, TrajectoryCase):
                results.append(_run_trajectory(case, client, trial=trial))
            elif isinstance(case, AnswerCase):
                results.append(_run_answer(case, client, trial=trial))
            elif isinstance(case, ToolLoopCase):
                results.append(_run_tool_loop(case, client, trial=trial))
            elif isinstance(case, GatewayCase):
                results.append(_run_gateway(case, trial=trial))

    report = EvalReport(
        mode=mode,
        suites=report_suites,
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
