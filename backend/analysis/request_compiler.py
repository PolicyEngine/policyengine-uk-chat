"""Typed facade for semantic reduction, authoritative binding, and planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, assert_never

from analysis.binding import (
    BindingFailed,
    BindingServices,
    NeedsClarification,
    Ready,
    RequestBinder,
    Unsupported,
)
from analysis.capabilities import CAPABILITY_REGISTRY, CapabilityRegistry
from analysis.common import AnalysisError, AnalysisErrorCode, RuntimeVersions
from analysis.compiler import ExecutionPlanCompiler
from analysis.models import (
    AnalysisSessionState,
    BoundRequest,
    ExecutionPlan,
    ModelUsageEntry,
    PendingClarification,
    SemanticRequestRevision,
    ValidatedAnswerClarification,
    ValidatedReviseAnalysis,
    ValidatedStartAnalysis,
)
from analysis.operations import OperationCatalogue, default_operation_catalogue
from analysis.reducer import SemanticRequestReducer


SemanticTurnUpdate = (
    ValidatedStartAnalysis
    | ValidatedReviseAnalysis
    | ValidatedAnswerClarification
)


@dataclass(frozen=True)
class CompilationInput:
    update: SemanticTurnUpdate
    state: AnalysisSessionState
    current_revision: SemanticRequestRevision | None
    active_clarification: PendingClarification | None
    turn_id: str
    runtime_versions: RuntimeVersions
    registry: CapabilityRegistry = CAPABILITY_REGISTRY
    operation_catalogue: OperationCatalogue = field(
        default_factory=default_operation_catalogue
    )
    binding_services: BindingServices = field(default_factory=BindingServices)
    bootstrap: bool = False
    created_at: datetime | None = None


@dataclass(frozen=True)
class CompiledRequest:
    kind: Literal["compiled"]
    revision: SemanticRequestRevision
    bound_request: BoundRequest
    plan: ExecutionPlan
    usage_entries: tuple[ModelUsageEntry, ...] = ()


@dataclass(frozen=True)
class CompilationClarification:
    kind: Literal["clarification"]
    revision: SemanticRequestRevision
    clarification: PendingClarification
    usage_entries: tuple[ModelUsageEntry, ...] = ()


@dataclass(frozen=True)
class RequestUnsupported:
    kind: Literal["unsupported"]
    revision: SemanticRequestRevision
    reason: str
    usage_entries: tuple[ModelUsageEntry, ...] = ()


@dataclass(frozen=True)
class RequestCompilationFailed:
    kind: Literal["failed"]
    revision: SemanticRequestRevision | None
    reason: str
    error_code: AnalysisErrorCode
    usage_entries: tuple[ModelUsageEntry, ...] = ()


RequestCompilation = (
    CompiledRequest
    | CompilationClarification
    | RequestUnsupported
    | RequestCompilationFailed
)


class RequestCompiler:
    """Produce exactly one typed decision from one semantic turn update."""

    def compile(self, compilation_input: CompilationInput) -> RequestCompilation:
        try:
            revision = SemanticRequestReducer.reduce(
                compilation_input.update,
                state=compilation_input.state,
                current_revision=compilation_input.current_revision,
                active_clarification=compilation_input.active_clarification,
                turn_id=compilation_input.turn_id,
                bootstrap=compilation_input.bootstrap,
                created_at=compilation_input.created_at,
            )
        except AnalysisError as exc:
            return RequestCompilationFailed(
                kind="failed",
                revision=None,
                reason=str(exc),
                error_code=exc.code,
            )

        binding = RequestBinder(
            services=compilation_input.binding_services,
            registry=compilation_input.registry,
            operation_catalogue=compilation_input.operation_catalogue,
        ).bind(
            revision,
            runtime_versions=compilation_input.runtime_versions,
        )
        if isinstance(binding, NeedsClarification):
            return CompilationClarification(
                kind="clarification",
                revision=revision,
                clarification=binding.clarification,
                usage_entries=binding.usage_entries,
            )
        if isinstance(binding, Unsupported):
            return RequestUnsupported(
                kind="unsupported",
                revision=revision,
                reason=binding.reason,
                usage_entries=binding.usage_entries,
            )
        if isinstance(binding, BindingFailed):
            return RequestCompilationFailed(
                kind="failed",
                revision=revision,
                reason=binding.reason,
                error_code=binding.error_code,
                usage_entries=binding.usage_entries,
            )
        if isinstance(binding, Ready):
            try:
                plan = ExecutionPlanCompiler.compile(
                    binding.bound_request,
                    compilation_input.registry,
                    compilation_input.operation_catalogue,
                )
            except AnalysisError as exc:
                if exc.code == AnalysisErrorCode.REQUEST_UNSUPPORTED:
                    return RequestUnsupported(
                        kind="unsupported",
                        revision=revision,
                        reason=str(exc),
                        usage_entries=binding.usage_entries,
                    )
                return RequestCompilationFailed(
                    kind="failed",
                    revision=revision,
                    reason=str(exc),
                    error_code=exc.code,
                    usage_entries=binding.usage_entries,
                )
            return CompiledRequest(
                kind="compiled",
                revision=revision,
                bound_request=binding.bound_request,
                plan=plan,
                usage_entries=binding.usage_entries,
            )
        assert_never(binding)
