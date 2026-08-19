"""Single typed facade over deterministic and exploratory plan execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.executor import (
    ExecutionOutcome,
    OperationEvent,
    execute_exploratory_plan,
    execute_standard_plan,
)
from analysis.models import (
    BoundRequest,
    ExecutionAttempt,
    ExecutionCompletion,
    ExecutionMode,
    ExecutionPlan,
    ExecutionRecord,
    ModelUsageEntry,
    ResultEnvelope,
    SemanticRequestRevision,
)
from analysis.operations import OperationCatalogue
from tools.context import ToolExecutionContext, TurnResultStore


AttemptVerifier = Callable[[str, str], ExecutionAttempt]
CancellationProbe = Callable[[], bool]
OperationDispatch = Callable[
    [str, dict[str, Any], ToolExecutionContext],
    dict[str, Any],
]


class ExploratoryMessages(Protocol):
    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        temperature: float,
        system: str,
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
    ) -> object: ...


class ExploratoryModelAdapter(Protocol):
    @property
    def messages(self) -> ExploratoryMessages: ...


@dataclass(frozen=True)
class OperationStarted:
    operation: str
    step_id: str
    public_arguments: dict[str, Any] = field(default_factory=dict)
    kind: Literal["operation_started"] = field(
        default="operation_started",
        init=False,
    )


@dataclass(frozen=True)
class OperationCompleted:
    operation: str
    step_id: str
    status: Literal["success", "error"]
    public_output: dict[str, Any] = field(default_factory=dict)
    kind: Literal["operation_completed"] = field(
        default="operation_completed",
        init=False,
    )


ExecutionProgress = OperationStarted | OperationCompleted
ProgressSink = Callable[[ExecutionProgress], None]


class ExecutionControl(Protocol):
    def verify_attempt(self, execution_id: str, token: str) -> ExecutionAttempt: ...

    def is_cancelled(self) -> bool: ...

    def report_progress(self, progress: ExecutionProgress) -> None: ...


@dataclass(frozen=True)
class CallbackExecutionControl:
    attempt_verifier: AttemptVerifier
    cancellation_probe: CancellationProbe
    progress_sink: ProgressSink = lambda _progress: None

    def verify_attempt(self, execution_id: str, token: str) -> ExecutionAttempt:
        return self.attempt_verifier(execution_id, token)

    def is_cancelled(self) -> bool:
        return self.cancellation_probe()

    def report_progress(self, progress: ExecutionProgress) -> None:
        self.progress_sink(progress)


@dataclass(frozen=True)
class ExecutionRequest:
    plan: ExecutionPlan
    attempt: ExecutionAttempt
    token: str
    revision: SemanticRequestRevision
    bound_request: BoundRequest
    operation_catalogue: OperationCatalogue
    result_store: TurnResultStore
    control: ExecutionControl
    exploratory_model_adapter: ExploratoryModelAdapter | None = None


@dataclass(frozen=True)
class _ExecutionResultBase:
    completion: ExecutionCompletion
    record: ExecutionRecord
    envelopes: tuple[ResultEnvelope, ...]
    progress: tuple[ExecutionProgress, ...]
    usage_entries: tuple[ModelUsageEntry, ...] = ()
    model_usage: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionCompleted(_ExecutionResultBase):
    kind: Literal["completed"] = field(default="completed", init=False)


@dataclass(frozen=True)
class ExecutionFailed(_ExecutionResultBase):
    kind: Literal["failed"] = field(default="failed", init=False)


@dataclass(frozen=True)
class ExecutionCancelled(_ExecutionResultBase):
    kind: Literal["cancelled"] = field(default="cancelled", init=False)


ExecutionResult = ExecutionCompleted | ExecutionFailed | ExecutionCancelled


class StandardExecutionStrategy(Protocol):
    def __call__(
        self,
        *,
        plan: ExecutionPlan,
        attempt: ExecutionAttempt,
        token: str,
        revision: SemanticRequestRevision,
        bound_request: BoundRequest,
        verify_attempt: AttemptVerifier,
        dispatch: OperationDispatch = ...,
        context: ToolExecutionContext,
        operation_catalogue: OperationCatalogue,
        is_cancelled: CancellationProbe,
        on_event: Callable[[OperationEvent], None],
    ) -> ExecutionOutcome: ...


class ExploratoryExecutionStrategy(Protocol):
    def __call__(
        self,
        *,
        plan: ExecutionPlan,
        attempt: ExecutionAttempt,
        token: str,
        revision: SemanticRequestRevision,
        bound_request: BoundRequest,
        verify_attempt: AttemptVerifier,
        dispatch: OperationDispatch = ...,
        context: ToolExecutionContext,
        operation_catalogue: OperationCatalogue,
        client: Any | None,
        is_cancelled: CancellationProbe,
        on_event: Callable[[OperationEvent], None],
    ) -> ExecutionOutcome: ...


def _typed_progress(event: OperationEvent) -> ExecutionProgress:
    if event.kind == "start":
        return OperationStarted(
            operation=event.operation,
            step_id=event.step_id,
            public_arguments=event.arguments or {},
        )
    if event.kind == "complete":
        return OperationCompleted(
            operation=event.operation,
            step_id=event.step_id,
            status="success" if event.status == "success" else "error",
            public_output=event.output or {},
        )
    raise AnalysisError(
        AnalysisErrorCode.EXECUTION_FAILED,
        f"executor emitted unknown progress kind {event.kind!r}",
    )


def _result(
    outcome: ExecutionOutcome,
    progress: tuple[ExecutionProgress, ...],
) -> ExecutionResult:
    if outcome.completion.status == "completed":
        return ExecutionCompleted(
            completion=outcome.completion,
            record=outcome.record,
            envelopes=outcome.envelopes,
            progress=progress,
            usage_entries=outcome.usage_entries,
            model_usage=outcome.model_usage,
        )
    if outcome.completion.status == "cancelled":
        return ExecutionCancelled(
            completion=outcome.completion,
            record=outcome.record,
            envelopes=outcome.envelopes,
            progress=progress,
            usage_entries=outcome.usage_entries,
            model_usage=outcome.model_usage,
        )
    if outcome.completion.status == "failed":
        return ExecutionFailed(
            completion=outcome.completion,
            record=outcome.record,
            envelopes=outcome.envelopes,
            progress=progress,
            usage_entries=outcome.usage_entries,
            model_usage=outcome.model_usage,
        )
    raise AnalysisError(
        AnalysisErrorCode.EXECUTION_FAILED,
        f"execution strategy returned unsupported status {outcome.completion.status!r}",
    )


def failed_execution_result(
    request: ExecutionRequest,
    error_code: AnalysisErrorCode,
) -> ExecutionFailed:
    return ExecutionFailed(
        completion=ExecutionCompletion(
            execution_id=request.attempt.execution_id,
            status="failed",
            error_code=error_code.value,
        ),
        record=ExecutionRecord(
            execution_id=request.attempt.execution_id,
            plan_id=request.plan.plan_id,
        ),
        envelopes=(),
        progress=(),
    )


class ExecutionEngine:
    """Select one internal strategy and return one typed execution result."""

    def __init__(
        self,
        *,
        standard_strategy: StandardExecutionStrategy = execute_standard_plan,
        exploratory_strategy: ExploratoryExecutionStrategy = execute_exploratory_plan,
        dispatch: OperationDispatch | None = None,
    ) -> None:
        self._standard_strategy = standard_strategy
        self._exploratory_strategy = exploratory_strategy
        self._dispatch = dispatch

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        progress: list[ExecutionProgress] = []

        def report(event: OperationEvent) -> None:
            item = _typed_progress(event)
            progress.append(item)
            request.control.report_progress(item)

        reform_field = request.bound_request.fields.get("reform")
        context = ToolExecutionContext(
            turn_id=str(request.revision.turn_id),
            execution_id=str(request.attempt.execution_id),
            result_store=request.result_store,
            approved_reform=(reform_field.value if reform_field else None),
            require_approved_reform=reform_field is not None,
        )
        common: dict[str, Any] = {
            "plan": request.plan,
            "attempt": request.attempt,
            "token": request.token,
            "revision": request.revision,
            "bound_request": request.bound_request,
            "verify_attempt": request.control.verify_attempt,
            "context": context,
            "operation_catalogue": request.operation_catalogue,
            "is_cancelled": request.control.is_cancelled,
            "on_event": report,
        }
        if self._dispatch is not None:
            common["dispatch"] = self._dispatch
        if request.plan.mode == ExecutionMode.STANDARD:
            outcome = self._standard_strategy(**common)
        elif request.plan.mode == ExecutionMode.EXPLORATORY:
            outcome = self._exploratory_strategy(
                **common,
                client=request.exploratory_model_adapter,
            )
        else:
            raise AnalysisError(
                AnalysisErrorCode.PLAN_INVALID,
                f"execution engine cannot run {request.plan.mode.value} plans",
            )
        if not progress and outcome.events:
            for event in outcome.events:
                report(event)
        return _result(outcome, tuple(progress))
