"""Manual runner for UK chat AI evaluation suites."""

import importlib.util
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

from analysis.binding import BindingFailed, NeedsClarification, Ready, Unsupported, bind_request
from analysis.candidate_validation import validate_candidate
from analysis.common import AnalysisError, RuntimeVersions
from analysis.compiler import compile_plan
from analysis.executor import authorize_exploratory_call
from analysis.facts import approved_non_result_values
from analysis.interpreter import (
    InterpreterContext,
    interpret_turn,
)
from analysis.models import (
    CANDIDATE_TURN_UPDATE_ADAPTER,
    FactRegister,
    ResultEnvelope,
    ValidatedAskAboutExecution,
    ValidatedCancelAnalysis,
)
from analysis.narration import NARRATION_DRAFT_ADAPTER, validate_narration
from analysis.reducer import reduce_semantic_update
from prompts.analysis import NARRATOR_SYSTEM
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
    ModelTurn,
    OutputExpectation,
    ToolContractCase,
    ToolLoopCase,
    TrajectoryCase,
    TurnInterpretationCase,
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
    "turn_interpretation": CASE_ROOT / "turn_interpretation",
}


EVAL_OPERATION_SELECTION_SYSTEM = """
Select only from the supplied UK tax-and-benefit calculation operations. Use
their schemas exactly, preserve the user's stated year and policy inputs, and
do not invent unavailable operations or arbitrary code execution.
""".strip()
EVAL_CHARTS_MODE_DIRECTIVE = """
Chart mode is enabled. Generate a chart only after its source calculation has
completed and use only the supplied chart operation.
""".strip()


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
    sections = [EVAL_OPERATION_SELECTION_SYSTEM]
    if case.charts_mode:
        sections.append(EVAL_CHARTS_MODE_DIRECTIVE)
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
            system=NARRATOR_SYSTEM,
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
    tool_outputs: List[Dict[str, Any]] = []
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
            try:
                output = execute_tool(call.name, call.input, context=tool_context)
            except Exception as exc:
                return _result(
                    case,
                    "failed",
                    0.0,
                    [f"{call.name}: {type(exc).__name__}: {exc}"],
                    {"text": final_text, "tool_calls": [call.model_dump() for call in tool_calls]},
                )
            tool_outputs.append(output)
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


def _partial_errors(actual: Dict[str, Any], expected: Dict[str, Any]) -> List[str]:
    return grade_output(actual, OutputExpectation(contains=expected))


def _turn_response_outcome(*, update, binding, plan) -> str:
    if isinstance(update, ValidatedAskAboutExecution):
        return "execution_question"
    if isinstance(update, ValidatedCancelAnalysis):
        return "cancelled"
    if binding is None:
        return "revision_accepted"
    if isinstance(binding, NeedsClarification):
        return "needs_clarification"
    if isinstance(binding, (Unsupported, BindingFailed)):
        return "unsupported"
    if plan is not None and plan.mode.value == "explanation":
        return "explanation"
    if plan is not None and plan.mode.value == "exploratory":
        return "ready_exploratory"
    return "ready_standard"


def _turn_contract(response_outcome: str) -> tuple[str, str]:
    if response_outcome == "execution_question":
        return "conversation_advanced", "completed"
    if response_outcome == "cancelled":
        return "cancellation_requested", "cancelled"
    if response_outcome == "needs_clarification":
        return "clarification_required", "clarification"
    if response_outcome == "unsupported":
        return "request_rejected", "unsupported"
    if response_outcome in {
        "explanation",
        "ready_standard",
        "ready_exploratory",
        "revision_accepted",
    }:
        return "plan_ready", "completed"
    if response_outcome in {"operation_rejected", "narration_rejected"}:
        return "attempt_outcome", "failed"
    return "turn_failed", "failed"


def _grade_turn_contract(case, details, errors, response_outcome: str) -> None:
    lifecycle, public = _turn_contract(response_outcome)
    details["lifecycle_outcome"] = lifecycle
    details["public_outcome_category"] = public
    if (
        case.expect.lifecycle_outcome is not None
        and lifecycle != case.expect.lifecycle_outcome
    ):
        errors.append(
            "lifecycle outcome: expected "
            f"{case.expect.lifecycle_outcome!r}, got {lifecycle!r}"
        )
    if (
        case.expect.public_outcome_category is not None
        and public != case.expect.public_outcome_category
    ):
        errors.append(
            "public outcome category: expected "
            f"{case.expect.public_outcome_category!r}, got {public!r}"
        )


def _run_turn_interpretation(
    case: TurnInterpretationCase,
    *,
    mode: str,
    client: ModelClient,
) -> CaseResult:
    """Evaluate interpretation, reduction, binding, and compilation separately."""

    context = InterpreterContext(
        state=case.initial_state,
        active_revision=case.active_revision,
        active_clarification=case.active_clarification,
        executions={item.execution_id: item for item in case.executions},
        latest_user_message=case.prompt,
        recent_messages=tuple(case.recent_messages),
        permitted_revision_ids=frozenset(case.permitted_revision_ids),
    )
    details: Dict[str, Any] = {}
    errors: List[str] = []
    try:
        if mode == "live":
            raw_client = getattr(client, "client", None)
            interpretation = interpret_turn(context, client=raw_client)
            update = interpretation.update
            validated_update = interpretation.validated_update
        else:
            if case.offline_candidate is None:
                return _result(
                    case,
                    "skipped",
                    0.0,
                    ["offline_candidate is required for offline turn interpretation"],
                )
            update = CANDIDATE_TURN_UPDATE_ADAPTER.validate_python(
                case.offline_candidate
            )
            validated_update = validate_candidate(
                update,
                state=context.state,
                current_revision=context.active_revision,
                active_clarification=context.active_clarification,
                executions=context.executions,
                user_message=case.prompt,
            )
        candidate = CANDIDATE_TURN_UPDATE_ADAPTER.dump_python(update, mode="json")
        details["candidate"] = candidate
        errors.extend(
            _partial_errors(candidate, case.expect.candidate_contains)
        )

        binding = None
        plan = None
        reduced_revision = None
        if not isinstance(
            validated_update,
            (ValidatedAskAboutExecution, ValidatedCancelAnalysis),
        ):
            reduced_revision = reduce_semantic_update(
                validated_update,
                state=case.initial_state,
                current_revision=case.active_revision,
                active_clarification=case.active_clarification,
                turn_id=case.turn_id,
                bootstrap=bool(case.recent_messages and case.active_revision is None),
            )
            reduced = reduced_revision.model_dump(mode="json")
            details["reduced_revision"] = reduced
            errors.extend(
                _partial_errors(
                    reduced,
                    case.expect.reduced_revision_contains,
                )
            )
            runtime_versions = RuntimeVersions(
                catalogue_version="eval-catalogue-v1",
                engine_version="eval-engine-v1",
                country_package_version="eval-country-v1",
                dataset_identifier="eval-dataset-v1",
            )
            binding = bind_request(
                reduced_revision,
                default_year=2026,
                runtime_versions=runtime_versions,
                reform_validator=lambda reform, _year: {
                    "valid": True,
                    "normalized_reform": reform,
                },
            )
            binding_name = (
                "clarification"
                if isinstance(binding, NeedsClarification)
                else "unsupported"
                if isinstance(binding, Unsupported)
                else "failed"
                if isinstance(binding, BindingFailed)
                else "explanation"
                if (
                    isinstance(binding, Ready)
                    and binding.bound_request.fields["analysis_kind"].value
                    == "explanation"
                )
                else "ready"
            )
            details["binding_outcome"] = binding_name
            if (
                case.expect.binding_outcome is not None
                and binding_name != case.expect.binding_outcome
            ):
                errors.append(
                    "binding outcome: expected "
                    f"{case.expect.binding_outcome!r}, got {binding_name!r}"
                )
            if isinstance(binding, Ready):
                plan = compile_plan(binding.bound_request)
                plan_data = plan.model_dump(mode="json")
                details["plan"] = plan_data
                errors.extend(
                    _partial_errors(plan_data, case.expect.plan_contains)
                )
                if list(plan.allowed_operations) != case.expect.permitted_operations:
                    errors.append(
                        "permitted operations: expected "
                        f"{case.expect.permitted_operations!r}, got "
                        f"{list(plan.allowed_operations)!r}"
                    )
                if case.adversarial_operation_call is not None:
                    call = case.adversarial_operation_call
                    authorize_exploratory_call(
                        plan=plan,
                        execution_id="eval_execution",
                        operation=call.name,
                        arguments=call.input,
                        envelopes={
                            step.step_id: ResultEnvelope(
                                execution_id="eval_execution",
                                source_step_id=step.step_id,
                                result_id=f"{step.result_binding}_eval_local",
                                result_type=step.result_type,
                                value={"status": "success"},
                            )
                            for step in plan.steps
                        },
                        definitions={
                            item["name"]: item for item in TOOL_DEFINITIONS
                        },
                    )
                if case.adversarial_narration_draft is not None:
                    draft = NARRATION_DRAFT_ADAPTER.validate_python(
                        case.adversarial_narration_draft
                    )
                    validate_narration(
                        draft,
                        facts=FactRegister(),
                        approved_values=approved_non_result_values(
                            reduced_revision,
                            plan_maximum_iterations=(
                                plan.max_model_iterations or None
                            ),
                            plan_maximum_operation_calls=(
                                plan.max_operation_calls or None
                            ),
                        ),
                    )

        outcome = _turn_response_outcome(
            update=validated_update,
            binding=binding,
            plan=plan,
        )
        details["response_outcome"] = outcome
        _grade_turn_contract(case, details, errors, outcome)
        if outcome != case.expect.response_outcome:
            errors.append(
                f"response outcome: expected {case.expect.response_outcome!r}, got {outcome!r}"
            )
        if case.expect.error_code is not None:
            errors.append(
                f"expected error code {case.expect.error_code!r}, but processing succeeded"
            )
    except AnalysisError as exc:
        details["error_code"] = exc.code.value
        _grade_turn_contract(
            case,
            details,
            errors,
            case.expect.response_outcome,
        )
        if case.expect.response_outcome not in {
            "candidate_rejected",
            "plan_rejected",
            "operation_rejected",
            "narration_rejected",
        }:
            errors.append(f"unexpected analysis error {exc.code.value!r}: {exc}")
        if case.expect.error_code != exc.code.value:
            errors.append(
                f"error code: expected {case.expect.error_code!r}, got {exc.code.value!r}"
            )
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")

    return _result(
        case,
        "failed" if errors else "passed",
        0.0 if errors else 1.0,
        errors,
        details,
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
        elif isinstance(case, TurnInterpretationCase):
            results.append(
                _run_turn_interpretation(case, mode=mode, client=client)
            )

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
