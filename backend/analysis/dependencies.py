"""Typed replaceable dependencies for the analysis application layer."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from analysis.binding import (
    BindingDecision,
    ReformTargetSelection,
    ReformTargetSelectionRequest,
)
from analysis.common import RuntimeVersions
from analysis.executor import ExecutionOutcome, OperationEvent
from analysis.interpreter import InterpretationResult, InterpreterContext
from analysis.models import (
    BillingIntent,
    BoundRequest,
    ExecutionAttempt,
    ExecutionPlan,
    FactRegister,
    SemanticRequestRevision,
    ModelUsageEntry,
)
from analysis.narration import NarrationResult
from analysis.request_compiler import CompilationInput, RequestCompilation


class TurnInterpreter(Protocol):
    def __call__(self, context: InterpreterContext) -> InterpretationResult: ...


class BindingService(Protocol):
    def __call__(
        self,
        revision: SemanticRequestRevision,
        *,
        runtime_versions: RuntimeVersions,
        reform_target_selector: ReformTargetSelectionService | None = None,
    ) -> BindingDecision: ...


class ReformTargetSelectionService(Protocol):
    def __call__(
        self,
        request: ReformTargetSelectionRequest,
    ) -> ReformTargetSelection | None: ...


class PlanCompiler(Protocol):
    def __call__(self, request: BoundRequest) -> ExecutionPlan: ...


class RequestCompilationService(Protocol):
    def compile(
        self,
        compilation_input: CompilationInput,
    ) -> RequestCompilation: ...


class BillingIntentBuilder(Protocol):
    def __call__(
        self,
        *,
        session_id: str,
        turn_id: str,
        user_id: str | None,
        usage_entries: tuple[ModelUsageEntry, ...],
    ) -> BillingIntent | None: ...


class AttemptVerifier(Protocol):
    def __call__(self, execution_id: str, token: str) -> ExecutionAttempt: ...


class CancellationProbe(Protocol):
    def __call__(self) -> bool: ...


class OperationEventSink(Protocol):
    def __call__(self, event: OperationEvent) -> None: ...


class PlanExecutor(Protocol):
    def __call__(
        self,
        *,
        plan: ExecutionPlan,
        attempt: ExecutionAttempt,
        token: str,
        revision: SemanticRequestRevision,
        bound_request: BoundRequest,
        verify_attempt: AttemptVerifier,
        is_cancelled: CancellationProbe,
        on_event: OperationEventSink,
    ) -> ExecutionOutcome: ...


class NarrationService(Protocol):
    def __call__(
        self,
        *,
        revision: SemanticRequestRevision,
        plan: ExecutionPlan,
        summaries: tuple[dict[str, Any], ...],
        facts: FactRegister,
        caveats: tuple[str, ...] = (),
    ) -> str | NarrationResult: ...


class RuntimeVersionProvider(Protocol):
    def __call__(self) -> RuntimeVersions: ...


class Clock(Protocol):
    def __call__(self) -> datetime: ...


class IdentifierFactory(Protocol):
    def __call__(self, namespace: str, *parts: object) -> str: ...


class OperationDispatcher(Protocol):
    def __call__(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Any,
    ) -> dict[str, Any]: ...


class AsyncCancellationProbe(Protocol):
    async def __call__(self) -> bool: ...
