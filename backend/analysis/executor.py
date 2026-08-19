"""Typed, attempt-authorized standard and exploratory plan execution."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from pydantic import ValidationError

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.compiler import validate_plan
from analysis.models import (
    BoundRequest,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionCompletion,
    ExecutionMode,
    ExecutionPlan,
    ExecutionRecord,
    FactRegister,
    ModelUsageEntry,
    OperationConstraint,
    OperationExecutionMetadata,
    PlanStep,
    ResultEnvelope,
    ResultReference,
    ResponseArtifact,
    SemanticRequestRevision,
)
from analysis.operations import OperationCatalogue, default_operation_catalogue
from config import DEFAULT_FAST_MODEL, DEFAULT_TEMPERATURE, get_sync_client
from tools.registry import RegisteredTool


EXPLORATORY_MODEL = DEFAULT_FAST_MODEL
logger = logging.getLogger(__name__)

Dispatch = Callable[[str, dict[str, Any], Any], dict[str, Any]]
CancellationProbe = Callable[[], bool]
AttemptVerifier = Callable[[str, str], ExecutionAttempt]


@dataclass(frozen=True)
class OperationEvent:
    kind: str
    operation: str
    step_id: str
    arguments: dict[str, Any] | None = None
    status: str | None = None
    output: dict[str, Any] | None = None


EventSink = Callable[[OperationEvent], None]


@dataclass(frozen=True)
class ExecutionOutcome:
    completion: ExecutionCompletion
    record: ExecutionRecord
    envelopes: tuple[ResultEnvelope, ...]
    events: tuple[OperationEvent, ...]
    usage_entries: tuple[ModelUsageEntry, ...] = ()
    model_usage: dict[str, int] = field(default_factory=dict)


def _default_dispatch(name: str, arguments: dict[str, Any], context: Any):
    from tools.dispatch import execute_tool

    return execute_tool(name, arguments, context=context)


def _default_context(
    turn_id: str,
    execution_id: str,
    approved_reform: dict[str, Any] | None,
):
    from tools.context import new_tool_context

    context = new_tool_context(turn_id, execution_id=execution_id)
    if approved_reform is not None:
        context.approved_reform = dict(approved_reform)
        context.require_approved_reform = True
    return context


def _spec_map(
    specs: Iterable[RegisteredTool] | None = None,
    operation_catalogue: OperationCatalogue | None = None,
) -> dict[str, RegisteredTool]:
    if specs is not None:
        return {spec.name: spec for spec in specs}
    catalogue = operation_catalogue or default_operation_catalogue()
    return dict(catalogue.operations)


def validate_registered_arguments(
    operation: str,
    arguments: dict[str, Any],
    specs: Mapping[str, RegisteredTool],
) -> dict[str, Any]:
    spec = specs.get(operation)
    if spec is None:
        raise AnalysisError(
            AnalysisErrorCode.OPERATION_NOT_PERMITTED,
            f"operation {operation} is not registered",
        )
    try:
        return spec.input_adapter.validate_python(arguments)
    except (ValidationError, TypeError, ValueError) as exc:
        raise AnalysisError(
            AnalysisErrorCode.OPERATION_NOT_PERMITTED,
            f"operation {operation} received arguments outside its registered input contract",
        ) from exc


def validate_execution_authority(
    *,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    token: str,
    bound_request: BoundRequest,
    verify_attempt: AttemptVerifier | None = None,
) -> ExecutionAttempt:
    """Validate immutable identity links and, when supplied, the stored token hash."""

    validate_plan(plan)
    if (
        attempt.status not in {
            ExecutionAttemptStatus.CLAIMED,
            ExecutionAttemptStatus.RUNNING,
            ExecutionAttemptStatus.CANCELLATION_REQUESTED,
        }
        or attempt.session_id != plan.session_id
        or attempt.request_revision_id != plan.request_revision_id
        or attempt.bound_request_id != plan.bound_request_id
        or attempt.plan_id != plan.plan_id
        or attempt.plan_hash != plan.plan_hash
        or bound_request.bound_request_id != plan.bound_request_id
        or bound_request.request_revision_id != plan.request_revision_id
        or bound_request.session_id != plan.session_id
    ):
        raise AnalysisError(
            AnalysisErrorCode.EXECUTION_CONFLICT,
            "execution attempt does not match the compiled plan and bound request",
        )
    if verify_attempt is not None:
        stored = verify_attempt(str(attempt.execution_id), token)
        if stored.plan_hash != plan.plan_hash:
            raise AnalysisError(
                AnalysisErrorCode.EXECUTION_TOKEN_INVALID,
                "execution token is not valid for this plan",
            )
        return stored
    if not token:
        raise AnalysisError(
            AnalysisErrorCode.EXECUTION_TOKEN_INVALID,
            "an execution token is required",
        )
    return attempt


def _check_before_operation(
    *,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    token: str,
    bound_request: BoundRequest,
    verify_attempt: AttemptVerifier | None,
    is_cancelled: CancellationProbe,
) -> bool:
    stored = validate_execution_authority(
        plan=plan,
        attempt=attempt,
        token=token,
        bound_request=bound_request,
        verify_attempt=verify_attempt,
    )
    return (
        is_cancelled()
        or stored.status == ExecutionAttemptStatus.CANCELLATION_REQUESTED
    )


def _resolve_argument(
    value: Any,
    *,
    execution_id: str,
    permitted_dependencies: set[str],
    envelopes: Mapping[str, ResultEnvelope],
) -> Any:
    if isinstance(value, ResultReference):
        envelope = envelopes.get(value.source_step_id)
        if envelope is None:
            raise AnalysisError(
                AnalysisErrorCode.RESULT_INVALID,
                f"dependency {value.source_step_id} is unavailable or incomplete",
            )
        if value.source_step_id not in permitted_dependencies:
            raise AnalysisError(
                AnalysisErrorCode.OPERATION_NOT_PERMITTED,
                "operation referenced a dependency absent from the compiled plan",
            )
        if str(envelope.execution_id) != execution_id:
            raise AnalysisError(
                AnalysisErrorCode.RESULT_INVALID,
                "operation referenced a result from another execution",
            )
        if (
            value.expected_result_type is not None
            and envelope.result_type != value.expected_result_type
        ):
            raise AnalysisError(
                AnalysisErrorCode.RESULT_INVALID,
                "operation dependency has an incompatible result type",
            )
        return str(envelope.result_id)
    if isinstance(value, dict):
        if "source_step_id" in value and set(value).issubset(
            {"source_step_id", "expected_result_type"}
        ):
            return _resolve_argument(
                ResultReference.model_validate(value),
                execution_id=execution_id,
                permitted_dependencies=permitted_dependencies,
                envelopes=envelopes,
            )
        return {
            key: _resolve_argument(
                item,
                execution_id=execution_id,
                permitted_dependencies=permitted_dependencies,
                envelopes=envelopes,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _resolve_argument(
                item,
                execution_id=execution_id,
                permitted_dependencies=permitted_dependencies,
                envelopes=envelopes,
            )
            for item in value
        ]
    if isinstance(value, tuple):
        return tuple(
            _resolve_argument(
                item,
                execution_id=execution_id,
                permitted_dependencies=permitted_dependencies,
                envelopes=envelopes,
            )
            for item in value
        )
    return value


def _public_argument(value: Any, *, field_name: str | None = None) -> Any:
    """Describe an operation input without exposing request-local handles."""

    if isinstance(value, ResultReference):
        reference = {"source_step_id": value.source_step_id}
        if value.expected_result_type is not None:
            reference["expected_result_type"] = value.expected_result_type
        return reference
    if isinstance(value, dict):
        return {
            key: _public_argument(item, field_name=key)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_public_argument(item, field_name=field_name) for item in value]
    if isinstance(value, tuple):
        return tuple(_public_argument(item, field_name=field_name) for item in value)
    if field_name in {"result_id", "simulation_id"} and isinstance(value, str):
        # Defense in depth for a call site that accidentally supplies resolved
        # arguments.  Public operation events must never carry an execution-
        # local reusable identifier.
        return {"request_local_dependency": True}
    return value


def public_operation_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: _public_argument(value, field_name=name)
        for name, value in arguments.items()
    }


def _validated_envelope(
    *,
    execution_id: str,
    step_id: str,
    spec: RegisteredTool,
    expected_result_types: tuple[str, ...],
    raw_result: Any,
) -> tuple[ResultEnvelope, dict[str, Any], dict[str, Any]]:
    if not isinstance(raw_result, dict) or "error" in raw_result:
        raise AnalysisError(
            AnalysisErrorCode.EXECUTION_FAILED,
            "registered operation did not return a successful object",
        )
    try:
        validated_model = spec.output_adapter.validate_python(raw_result)
        validated = validated_model.model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as exc:
        raise AnalysisError(
            AnalysisErrorCode.RESULT_INVALID,
            f"operation {spec.name} returned a value outside its output contract",
        ) from exc
    if spec.result_type not in expected_result_types:
        raise AnalysisError(
            AnalysisErrorCode.RESULT_INVALID,
            f"operation {spec.name} produced unexpected result type {spec.result_type}",
        )
    raw_id = validated.get("result_id")
    result_id = raw_id if isinstance(raw_id, str) else f"result_{uuid4().hex}"
    try:
        fact_values = spec.fact_extractor(validated)
        public_summary = spec.public_summary_builder(validated)
    except Exception as exc:
        raise AnalysisError(
            AnalysisErrorCode.RESULT_INVALID,
            f"operation {spec.name} could not produce a sanitized summary",
        ) from exc
    if not isinstance(fact_values, dict) or not isinstance(public_summary, dict):
        raise AnalysisError(
            AnalysisErrorCode.RESULT_INVALID,
            "registered result projections must return objects",
        )
    return (
        ResultEnvelope(
            execution_id=execution_id,
            source_step_id=step_id,
            result_id=result_id,
            result_type=spec.result_type,
            value=validated,
            public_summary=public_summary,
        ),
        fact_values,
        public_summary,
    )


def _emit(
    events: list[OperationEvent],
    event: OperationEvent,
    sink: EventSink,
) -> None:
    events.append(event)
    sink(event)


def _run_operation(
    *,
    operation: str,
    step_id: str,
    arguments: dict[str, Any],
    event_arguments: Mapping[str, Any],
    expected_result_types: tuple[str, ...],
    dispatch: Dispatch,
    context: Any,
    definitions: Mapping[str, dict[str, Any]],
    specs: Mapping[str, RegisteredTool],
    events: list[OperationEvent],
    sink: EventSink,
) -> tuple[ResultEnvelope, dict[str, Any], OperationExecutionMetadata]:
    spec = specs.get(operation)
    if spec is None:
        raise AnalysisError(
            AnalysisErrorCode.OPERATION_NOT_PERMITTED,
            f"operation {operation} has no registered result contract",
        )
    arguments = validate_registered_arguments(operation, arguments, specs)
    if context is not None and hasattr(context, "active_step_id"):
        context.active_step_id = step_id
    _emit(
        events,
        OperationEvent(
            "start",
            operation,
            step_id,
            arguments=public_operation_arguments(event_arguments),
        ),
        sink,
    )
    started = time.perf_counter()
    try:
        raw_result = dispatch(operation, arguments, context)
        envelope, fact_values, public_summary = _validated_envelope(
            execution_id=context.execution_id,
            step_id=step_id,
            spec=spec,
            expected_result_types=expected_result_types,
            raw_result=raw_result,
        )
    except AnalysisError:
        duration = max(0, round((time.perf_counter() - started) * 1000))
        _emit(
            events,
            OperationEvent("complete", operation, step_id, status="error", output={}),
            sink,
        )
        raise
    except Exception as exc:
        logger.exception("Registered operation %s failed", operation)
        _emit(
            events,
            OperationEvent("complete", operation, step_id, status="error", output={}),
            sink,
        )
        raise AnalysisError(
            AnalysisErrorCode.EXECUTION_FAILED,
            f"registered operation {operation} failed",
        ) from exc
    duration = max(0, round((time.perf_counter() - started) * 1000))
    _emit(
        events,
        OperationEvent(
            "complete", operation, step_id, status="success", output=public_summary
        ),
        sink,
    )
    return (
        envelope,
        fact_values,
        OperationExecutionMetadata(
            step_id=step_id,
            operation=operation,
            status="completed",
            duration_ms=duration,
            result_kind=envelope.result_type,
        ),
    )


def _execution_record(
    *,
    plan: ExecutionPlan,
    execution_id: str,
    summaries: Mapping[str, dict[str, Any]],
    fact_values: Mapping[str, dict[str, Any]],
    revision: SemanticRequestRevision,
) -> ExecutionRecord:
    from analysis.facts import build_fact_register

    def operation_for(step_id: str, summary: dict[str, Any]) -> str:
        return next(
            (
                step.operation
                for step in plan.steps
                if step.step_id == step_id
            ),
            summary.get("operation", "exploratory_operation"),
        )

    public = tuple(
        {
            "step_id": step_id,
            "operation": operation_for(step_id, summary),
            "summary": summary,
        }
        for step_id, summary in summaries.items()
    )
    fact_inputs = tuple(
        {
            "step_id": step_id,
            "operation": next(
                (
                    step.operation
                    for step in plan.steps
                    if step.step_id == step_id
                ),
                summaries.get(step_id, {}).get("operation", "exploratory_operation"),
            ),
            "summary": values,
        }
        for step_id, values in fact_values.items()
    )
    facts = build_fact_register(revision=revision, operation_summaries=fact_inputs)
    response_artifacts = tuple(
        ResponseArtifact(
            artifact_id=f"chart_{step_id}",
            content=summary["chart_markdown"],
        )
        for step_id, summary in summaries.items()
        if operation_for(step_id, summary) == "generate_chart"
        and isinstance(summary.get("chart_markdown"), str)
    )
    return ExecutionRecord(
        execution_id=execution_id,
        plan_id=plan.plan_id,
        operation_summaries=public,
        fact_register=facts,
        response_artifacts=response_artifacts,
    )


def _completion(
    execution_id: str,
    status: str,
    metadata: list[OperationExecutionMetadata],
    error_code: AnalysisErrorCode | None = None,
) -> ExecutionCompletion:
    return ExecutionCompletion(
        execution_id=execution_id,
        status=status,
        operations=tuple(metadata),
        error_code=error_code.value if error_code else None,
    )


def _required_results_present(
    plan: ExecutionPlan,
    envelopes: Iterable[ResultEnvelope],
) -> bool:
    actual = {item.result_type for item in envelopes}
    return set(plan.required_result_types).issubset(actual)


def execute_standard_plan(
    *,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    token: str,
    revision: SemanticRequestRevision,
    bound_request: BoundRequest,
    verify_attempt: AttemptVerifier | None = None,
    dispatch: Dispatch = _default_dispatch,
    context: Any | None = None,
    definitions: Iterable[dict[str, Any]] | None = None,
    specs: Iterable[RegisteredTool] | None = None,
    operation_catalogue: OperationCatalogue | None = None,
    is_cancelled: CancellationProbe = lambda: False,
    on_event: EventSink = lambda _event: None,
) -> ExecutionOutcome:
    validate_execution_authority(
        plan=plan,
        attempt=attempt,
        token=token,
        bound_request=bound_request,
        verify_attempt=verify_attempt,
    )
    if plan.mode != ExecutionMode.STANDARD:
        raise AnalysisError(AnalysisErrorCode.PLAN_INVALID, "expected a standard plan")
    reform_field = bound_request.fields.get("reform")
    context = context or _default_context(
        str(revision.turn_id),
        str(attempt.execution_id),
        reform_field.value if reform_field else None,
    )
    operation_catalogue = operation_catalogue or default_operation_catalogue()
    definitions_map = {
        item["name"]: item
        for item in (definitions or operation_catalogue.tool_definitions())
    }
    specs_map = _spec_map(specs, operation_catalogue)
    envelopes: dict[str, ResultEnvelope] = {}
    summaries: dict[str, dict[str, Any]] = {}
    fact_values: dict[str, dict[str, Any]] = {}
    metadata: list[OperationExecutionMetadata] = []
    events: list[OperationEvent] = []
    remaining = list(plan.steps)
    error: AnalysisError | None = None
    while remaining:
        runnable = next(
            (
                step
                for step in remaining
                if set(step.depends_on).issubset(envelopes)
            ),
            None,
        )
        if runnable is None:
            error = AnalysisError(
                AnalysisErrorCode.RESULT_INVALID,
                "no plan step has all of its typed dependencies",
            )
            break
        if _check_before_operation(
            plan=plan,
            attempt=attempt,
            token=token,
            bound_request=bound_request,
            verify_attempt=verify_attempt,
            is_cancelled=is_cancelled,
        ):
            for step in remaining:
                metadata.append(
                    OperationExecutionMetadata(
                        step_id=step.step_id,
                        operation=step.operation,
                        status="cancelled",
                        duration_ms=0,
                        error_code=AnalysisErrorCode.EXECUTION_CANCELLED.value,
                    )
                )
            return ExecutionOutcome(
                completion=_completion(
                    str(attempt.execution_id),
                    "cancelled",
                    metadata,
                    AnalysisErrorCode.EXECUTION_CANCELLED,
                ),
                record=_execution_record(
                    plan=plan,
                    execution_id=str(attempt.execution_id),
                    summaries=summaries,
                    fact_values=fact_values,
                    revision=revision,
                ),
                envelopes=tuple(envelopes.values()),
                events=tuple(events),
            )
        try:
            arguments = {
                name: _resolve_argument(
                    value,
                    execution_id=str(attempt.execution_id),
                    permitted_dependencies=set(runnable.depends_on),
                    envelopes=envelopes,
                )
                for name, value in runnable.arguments.items()
            }
            envelope, extracted, operation_metadata = _run_operation(
                operation=runnable.operation,
                step_id=runnable.step_id,
                arguments=arguments,
                event_arguments=runnable.arguments,
                expected_result_types=(runnable.result_type,),
                dispatch=dispatch,
                context=context,
                definitions=definitions_map,
                specs=specs_map,
                events=events,
                sink=on_event,
            )
        except AnalysisError as exc:
            error = exc
            metadata.append(
                OperationExecutionMetadata(
                    step_id=runnable.step_id,
                    operation=runnable.operation,
                    status="failed",
                    duration_ms=0,
                    error_code=exc.code.value,
                )
            )
            break
        envelopes[runnable.step_id] = envelope
        summaries[runnable.step_id] = envelope.public_summary
        fact_values[runnable.step_id] = extracted
        metadata.append(operation_metadata)
        remaining.remove(runnable)
    if error is None and not _required_results_present(plan, envelopes.values()):
        error = AnalysisError(
            AnalysisErrorCode.REQUIRED_RESULTS_MISSING,
            "execution did not produce every required validated result type",
        )
    status = "failed" if error else "completed"
    return ExecutionOutcome(
        completion=_completion(
            str(attempt.execution_id),
            status,
            metadata,
            error.code if error else None,
        ),
        record=_execution_record(
            plan=plan,
            execution_id=str(attempt.execution_id),
            summaries=summaries,
            fact_values=fact_values,
            revision=revision,
        ),
        envelopes=tuple(envelopes.values()),
        events=tuple(events),
    )


def _constraint(plan: ExecutionPlan, operation: str) -> OperationConstraint:
    constraint = next(
        (item for item in plan.operation_constraints if item.operation == operation),
        None,
    )
    if constraint is None:
        raise AnalysisError(
            AnalysisErrorCode.OPERATION_NOT_PERMITTED,
            f"operation {operation} has no compiled constraint",
        )
    return constraint


def _within_allowed(value: Any, allowed: Any) -> bool:
    if isinstance(allowed, list):
        return value in allowed
    if isinstance(allowed, dict):
        if "enum" in allowed and value not in allowed["enum"]:
            return False
        if "minimum" in allowed and value < allowed["minimum"]:
            return False
        if "maximum" in allowed and value > allowed["maximum"]:
            return False
        return True
    return value == allowed


def authorize_exploratory_call(
    *,
    plan: ExecutionPlan,
    execution_id: str,
    operation: str,
    arguments: dict[str, Any],
    envelopes: Mapping[str, ResultEnvelope],
    definitions: Mapping[str, dict[str, Any]],
    specs: Mapping[str, RegisteredTool] | None = None,
) -> dict[str, Any]:
    if operation not in plan.allowed_operations:
        raise AnalysisError(
            AnalysisErrorCode.OPERATION_NOT_PERMITTED,
            f"operation {operation} is not permitted by this plan",
        )
    constraint = _constraint(plan, operation)
    dependency_arguments = {"simulation_id", "result_id"}
    allowed_names = set(constraint.fixed_arguments) | set(
        constraint.allowed_arguments
    ) | dependency_arguments
    extra = set(arguments).difference(allowed_names)
    if extra:
        raise AnalysisError(
            AnalysisErrorCode.OPERATION_NOT_PERMITTED,
            f"operation supplied unpermitted arguments: {', '.join(sorted(extra))}",
        )
    resolved = dict(arguments)
    for name, fixed in constraint.fixed_arguments.items():
        if name in resolved and resolved[name] != fixed:
            raise AnalysisError(
                AnalysisErrorCode.OPERATION_NOT_PERMITTED,
                f"operation changed fixed argument {name}",
            )
        resolved[name] = fixed
    for name, value in resolved.items():
        if name in constraint.allowed_arguments and not _within_allowed(
            value, constraint.allowed_arguments[name]
        ):
            raise AnalysisError(
                AnalysisErrorCode.OPERATION_NOT_PERMITTED,
                f"operation argument {name} is outside compiled limits",
            )
    for name in dependency_arguments.intersection(resolved):
        raw = resolved[name]
        if not isinstance(raw, dict) or "source_step_id" not in raw:
            raise AnalysisError(
                AnalysisErrorCode.OPERATION_NOT_PERMITTED,
                "exploratory dependencies must use a source-step reference",
            )
        source = str(raw["source_step_id"])
        envelope = envelopes.get(source)
        if envelope is None or (
            source not in constraint.permitted_dependencies
            and envelope.result_type not in constraint.permitted_dependencies
        ):
            raise AnalysisError(
                AnalysisErrorCode.OPERATION_NOT_PERMITTED,
                "operation referenced an unavailable dependency",
            )
        if str(envelope.execution_id) != execution_id:
            raise AnalysisError(
                AnalysisErrorCode.RESULT_INVALID,
                "operation referenced a result from another execution",
            )
        if envelope.result_type not in constraint.permitted_dependency_types:
            raise AnalysisError(
                AnalysisErrorCode.RESULT_INVALID,
                "operation dependency has an incompatible result type",
            )
        resolved[name] = str(envelope.result_id)
    if operation not in definitions:
        raise AnalysisError(
            AnalysisErrorCode.OPERATION_NOT_PERMITTED,
            f"operation {operation} is not registered",
        )
    validate_registered_arguments(operation, resolved, specs or _spec_map())
    return resolved


def _response_text(response: Any) -> str:
    return " ".join(
        str(getattr(block, "text", "")).strip()
        for block in getattr(response, "content", ()) or ()
        if getattr(block, "type", None) == "text"
    ).strip()


def execute_exploratory_plan(
    *,
    plan: ExecutionPlan,
    attempt: ExecutionAttempt,
    token: str,
    revision: SemanticRequestRevision,
    bound_request: BoundRequest,
    verify_attempt: AttemptVerifier | None = None,
    dispatch: Dispatch = _default_dispatch,
    context: Any | None = None,
    definitions: Iterable[dict[str, Any]] | None = None,
    specs: Iterable[RegisteredTool] | None = None,
    operation_catalogue: OperationCatalogue | None = None,
    client: Any | None = None,
    is_cancelled: CancellationProbe = lambda: False,
    on_event: EventSink = lambda _event: None,
) -> ExecutionOutcome:
    validate_execution_authority(
        plan=plan,
        attempt=attempt,
        token=token,
        bound_request=bound_request,
        verify_attempt=verify_attempt,
    )
    if plan.mode != ExecutionMode.EXPLORATORY:
        raise AnalysisError(AnalysisErrorCode.PLAN_INVALID, "expected an exploratory plan")
    reform_field = bound_request.fields.get("reform")
    context = context or _default_context(
        str(revision.turn_id),
        str(attempt.execution_id),
        reform_field.value if reform_field else None,
    )
    operation_catalogue = operation_catalogue or default_operation_catalogue()
    definitions_list = list(
        definitions or operation_catalogue.tool_definitions()
    )
    definitions_map = {item["name"]: item for item in definitions_list}
    specs_map = _spec_map(specs, operation_catalogue)
    permitted_tools = [
        {**item, "strict": True}
        for item in definitions_list
        if item["name"] in plan.allowed_operations
    ]
    envelopes: dict[str, ResultEnvelope] = {}
    summaries: dict[str, dict[str, Any]] = {}
    fact_values: dict[str, dict[str, Any]] = {}
    metadata: list[OperationExecutionMetadata] = []
    events: list[OperationEvent] = []

    # Deterministic prerequisite steps are executed before the restricted model.
    for step in plan.steps:
        if _check_before_operation(
            plan=plan,
            attempt=attempt,
            token=token,
            bound_request=bound_request,
            verify_attempt=verify_attempt,
            is_cancelled=is_cancelled,
        ):
            return ExecutionOutcome(
                completion=_completion(
                    str(attempt.execution_id),
                    "cancelled",
                    metadata,
                    AnalysisErrorCode.EXECUTION_CANCELLED,
                ),
                record=_execution_record(
                    plan=plan,
                    execution_id=str(attempt.execution_id),
                    summaries=summaries,
                    fact_values=fact_values,
                    revision=revision,
                ),
                envelopes=tuple(envelopes.values()),
                events=tuple(events),
            )
        try:
            arguments = {
                name: _resolve_argument(
                    value,
                    execution_id=str(attempt.execution_id),
                    permitted_dependencies=set(step.depends_on),
                    envelopes=envelopes,
                )
                for name, value in step.arguments.items()
            }
            envelope, extracted, item_metadata = _run_operation(
                operation=step.operation,
                step_id=step.step_id,
                arguments=arguments,
                event_arguments=step.arguments,
                expected_result_types=(step.result_type,),
                dispatch=dispatch,
                context=context,
                definitions=definitions_map,
                specs=specs_map,
                events=events,
                sink=on_event,
            )
        except AnalysisError as exc:
            metadata.append(
                OperationExecutionMetadata(
                    step_id=step.step_id,
                    operation=step.operation,
                    status="failed",
                    duration_ms=0,
                    error_code=exc.code.value,
                )
            )
            return ExecutionOutcome(
                completion=_completion(
                    str(attempt.execution_id), "failed", metadata, exc.code
                ),
                record=_execution_record(
                    plan=plan,
                    execution_id=str(attempt.execution_id),
                    summaries=summaries,
                    fact_values=fact_values,
                    revision=revision,
                ),
                envelopes=tuple(envelopes.values()),
                events=tuple(events),
            )
        envelopes[step.step_id] = envelope
        summaries[step.step_id] = envelope.public_summary
        fact_values[step.step_id] = extracted
        metadata.append(item_metadata)

    resolved_client = client or get_sync_client()
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": json.dumps(
                {
                    "objective": plan.objective,
                    "fixed_inputs": plan.fixed_inputs,
                    "available_dependencies": {
                        step_id: {
                            "source_step_id": step_id,
                            "result_type": envelope.result_type,
                            "summary": envelope.public_summary,
                        }
                        for step_id, envelope in envelopes.items()
                    },
                    "required_result_types": plan.required_result_types,
                    "limits": {
                        "iterations": plan.max_model_iterations,
                        "operation_calls": plan.max_operation_calls,
                    },
                },
                default=str,
            ),
        }
    ]
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    usage_entries: list[ModelUsageEntry] = []
    calls = 0
    failure: AnalysisError | None = None
    for iteration in range(plan.max_model_iterations):
        if _check_before_operation(
            plan=plan,
            attempt=attempt,
            token=token,
            bound_request=bound_request,
            verify_attempt=verify_attempt,
            is_cancelled=is_cancelled,
        ):
            completion = _completion(
                str(attempt.execution_id),
                "cancelled",
                metadata,
                AnalysisErrorCode.EXECUTION_CANCELLED,
            )
            break
        try:
            response = resolved_client.messages.create(
                model=EXPLORATORY_MODEL,
                max_tokens=4096,
                temperature=DEFAULT_TEMPERATURE,
                system=(
                    "Complete only the supplied objective. Use only the supplied "
                    "operations and source-step dependencies. Do not alter fixed inputs."
                ),
                tools=permitted_tools,
                messages=messages,
            )
        except Exception:
            usage_entries.append(
                ModelUsageEntry(
                    usage_entry_id=(
                        f"usage_{attempt.execution_id}_exploratory_{iteration + 1}"
                    ),
                    session_id=plan.session_id,
                    turn_id=revision.turn_id,
                    operation="exploratory_execution",
                    model=EXPLORATORY_MODEL,
                )
            )
            failure = AnalysisError(
                AnalysisErrorCode.EXECUTION_FAILED,
                "exploratory model call failed",
            )
            break
        response_usage = getattr(response, "usage", None)
        for name in usage:
            usage[name] += getattr(response_usage, name, 0)
        usage_entries.append(
            ModelUsageEntry(
                usage_entry_id=(
                    f"usage_{attempt.execution_id}_exploratory_{iteration + 1}"
                ),
                session_id=plan.session_id,
                turn_id=revision.turn_id,
                operation="exploratory_execution",
                model=EXPLORATORY_MODEL,
                **{
                    name: getattr(response_usage, name, 0)
                    for name in usage
                },
            )
        )
        tool_blocks = [
            block
            for block in getattr(response, "content", ()) or ()
            if getattr(block, "type", None) == "tool_use"
        ]
        if not tool_blocks:
            if _required_results_present(plan, envelopes.values()):
                completion = _completion(
                    str(attempt.execution_id), "completed", metadata
                )
                break
            if iteration + 1 >= plan.max_model_iterations:
                failure = AnalysisError(
                    AnalysisErrorCode.REQUIRED_RESULTS_MISSING,
                    "exploratory execution ended without required validated results",
                )
                break
            messages.extend(
                [
                    {"role": "assistant", "content": _response_text(response)},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "missing_result_types": sorted(
                                    set(plan.required_result_types)
                                    - {item.result_type for item in envelopes.values()}
                                )
                            }
                        ),
                    },
                ]
            )
            continue
        assistant_content = []
        tool_results = []
        for block in tool_blocks:
            if _check_before_operation(
                plan=plan,
                attempt=attempt,
                token=token,
                bound_request=bound_request,
                verify_attempt=verify_attempt,
                is_cancelled=is_cancelled,
            ):
                return ExecutionOutcome(
                    completion=_completion(
                        str(attempt.execution_id),
                        "cancelled",
                        metadata,
                        AnalysisErrorCode.EXECUTION_CANCELLED,
                    ),
                    record=_execution_record(
                        plan=plan,
                        execution_id=str(attempt.execution_id),
                        summaries=summaries,
                        fact_values=fact_values,
                        revision=revision,
                    ),
                    envelopes=tuple(envelopes.values()),
                    events=tuple(events),
                    usage_entries=tuple(usage_entries),
                    model_usage=usage,
                )
            if calls >= plan.max_operation_calls:
                failure = AnalysisError(
                    AnalysisErrorCode.RESOURCE_LIMIT,
                    "exploratory operation-call limit was reached",
                )
                break
            operation = str(getattr(block, "name", ""))
            raw_arguments = getattr(block, "input", None)
            if not isinstance(raw_arguments, dict):
                failure = AnalysisError(
                    AnalysisErrorCode.OPERATION_NOT_PERMITTED,
                    "exploratory operation arguments must be an object",
                )
                break
            try:
                arguments = authorize_exploratory_call(
                    plan=plan,
                    execution_id=str(attempt.execution_id),
                    operation=operation,
                    arguments=raw_arguments,
                    envelopes=envelopes,
                    definitions=definitions_map,
                    specs=specs_map,
                )
                calls += 1
                step_id = f"explore_{calls}"
                constraint = _constraint(plan, operation)
                envelope, extracted, item_metadata = _run_operation(
                    operation=operation,
                    step_id=step_id,
                    arguments=arguments,
                    event_arguments=raw_arguments,
                    expected_result_types=constraint.result_types,
                    dispatch=dispatch,
                    context=context,
                    definitions=definitions_map,
                    specs=specs_map,
                    events=events,
                    sink=on_event,
                )
            except AnalysisError as exc:
                failure = exc
                break
            envelopes[step_id] = envelope
            summaries[step_id] = {
                **envelope.public_summary,
                "operation": operation,
            }
            fact_values[step_id] = extracted
            metadata.append(item_metadata)
            assistant_content.append(
                {
                    "type": "tool_use",
                    "id": getattr(block, "id", step_id),
                    "name": operation,
                    "input": raw_arguments,
                }
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": getattr(block, "id", step_id),
                    "content": json.dumps(envelope.public_summary, default=str),
                }
            )
        if failure:
            break
        messages.extend(
            [
                {"role": "assistant", "content": assistant_content},
                {"role": "user", "content": tool_results},
            ]
        )
    else:
        completion = _completion(
            str(attempt.execution_id),
            "failed",
            metadata,
            AnalysisErrorCode.REQUIRED_RESULTS_MISSING,
        )
    if failure:
        completion = _completion(
            str(attempt.execution_id), "failed", metadata, failure.code
        )
    elif "completion" not in locals():
        completion = _completion(
            str(attempt.execution_id),
            "failed",
            metadata,
            AnalysisErrorCode.REQUIRED_RESULTS_MISSING,
        )
    return ExecutionOutcome(
        completion=completion,
        record=_execution_record(
            plan=plan,
            execution_id=str(attempt.execution_id),
            summaries=summaries,
            fact_values=fact_values,
            revision=revision,
        ),
        envelopes=tuple(envelopes.values()),
        events=tuple(events),
        usage_entries=tuple(usage_entries),
        model_usage=usage,
    )
