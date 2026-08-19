"""Projection from typed analysis results to the established chat events."""

from __future__ import annotations

from collections.abc import Iterable

from analysis.execution_engine import (
    ExecutionProgress,
    OperationStarted,
)
from analysis.finalization import FinalizationResult
from analysis.models import (
    CancelledTurnOutcome,
    ClarificationTurnOutcome,
    CompletedTurnOutcome,
    ConflictTurnOutcome,
    FailedTurnOutcome,
    ModelUsageEntry,
    BillingIntent,
    StillProcessingTurnOutcome,
    TurnOutcome,
)
from analysis.trace import AnalysisTrace
from chat.events import (
    CancellationAccepted,
    ChatEvent,
    ChatUsage,
    ClarificationRequired,
    DuplicateProcessed,
    TextChunk,
    ToolCompleted,
    ToolStarted,
    ToolUsed,
    TurnCompleted,
    TurnConflict,
    TurnFailed,
)


def aggregate_usage(entries: Iterable[ModelUsageEntry]) -> ChatUsage:
    total = ChatUsage()
    for entry in entries:
        total = total.plus(
            {
                "input_tokens": entry.input_tokens,
                "output_tokens": entry.output_tokens,
                "cache_creation_input_tokens": entry.cache_creation_input_tokens,
                "cache_read_input_tokens": entry.cache_read_input_tokens,
            }
        )
    return total


class ChatEventProjector:
    """Translate application results without changing analysis state."""

    @staticmethod
    def project_progress(
        execution_id: str,
        progress: ExecutionProgress,
    ) -> tuple[ChatEvent, ...]:
        tool_id = f"{execution_id}:{progress.step_id}"
        if isinstance(progress, OperationStarted):
            return (
                ToolStarted(progress.operation, tool_id),
                ToolUsed(
                    progress.operation,
                    tool_id,
                    progress.public_arguments,
                ),
            )
        return (
            ToolCompleted(
                progress.operation,
                tool_id,
                progress.status,
                progress.public_output,
            ),
        )

    @staticmethod
    def project_finalization(
        result: FinalizationResult,
    ) -> tuple[ChatEvent, ...]:
        return ChatEventProjector.project_outcome(
            outcome=result.outcome,
            session_id=str(result.receipt.session_id),
            turn_id=str(result.receipt.turn_id),
            usage_entries=result.usage_entries,
            trace=result.trace,
            billing_intent=result.billing_intent,
        )

    @staticmethod
    def project_outcome(
        *,
        outcome: TurnOutcome,
        session_id: str,
        turn_id: str,
        usage_entries: tuple[ModelUsageEntry, ...] = (),
        trace: AnalysisTrace | None = None,
        billing_intent: BillingIntent | None = None,
    ) -> tuple[ChatEvent, ...]:
        usage = aggregate_usage(usage_entries)
        duplicate_prefix: list[ChatEvent] = (
            [DuplicateProcessed(session_id, turn_id, outcome.kind)]
            if getattr(outcome, "duplicate", False)
            else []
        )
        if isinstance(outcome, StillProcessingTurnOutcome):
            return (
                DuplicateProcessed(session_id, turn_id, "processing"),
                TextChunk(outcome.content),
                TurnCompleted(
                    content=outcome.content,
                    session_id=session_id,
                    turn_id=turn_id,
                    model=None,
                    route="duplicate",
                    outcome="still_processing",
                    stop_reason="processing",
                    usage=usage,
                    processed_duplicate=True,
                    analysis_trace=trace,
                    usage_entries=(),
                    response_artifacts=(),
                    billing_intent=None,
                ),
            )
        if isinstance(outcome, ConflictTurnOutcome):
            return tuple(duplicate_prefix) + (
                TurnConflict(
                    outcome.content,
                    session_id,
                    turn_id,
                    outcome.retryable,
                ),
            )
        if isinstance(outcome, FailedTurnOutcome):
            return tuple(duplicate_prefix) + (
                TurnFailed(
                    content=outcome.content,
                    session_id=session_id,
                    turn_id=turn_id,
                    stop_reason=outcome.error_code,
                    usage=usage,
                    model=outcome.model,
                    billable=outcome.billable,
                    analysis_trace=trace,
                    usage_entries=usage_entries,
                    billing_intent=billing_intent,
                ),
            )
        prefix = duplicate_prefix
        if isinstance(outcome, ClarificationTurnOutcome):
            prefix.append(
                ClarificationRequired(outcome.question_id, outcome.reason_code)
            )
        if isinstance(outcome, CancelledTurnOutcome):
            prefix.append(
                CancellationAccepted(
                    session_id,
                    turn_id,
                    outcome.request_revision_id,
                )
            )
        prefix.append(TextChunk(outcome.content))
        route = (
            outcome.route
            if isinstance(outcome, CompletedTurnOutcome)
            else outcome.kind
        )
        prefix.append(
            TurnCompleted(
                content=outcome.content,
                session_id=session_id,
                turn_id=turn_id,
                model=getattr(outcome, "model", None),
                route=route,
                outcome=outcome.kind,
                stop_reason=(
                    "processed_duplicate"
                    if getattr(outcome, "duplicate", False)
                    else "end_turn"
                ),
                usage=usage,
                processed_duplicate=getattr(outcome, "duplicate", False),
                analysis_trace=trace,
                usage_entries=usage_entries,
                response_artifacts=(
                    outcome.response_artifacts
                    if isinstance(outcome, CompletedTurnOutcome)
                    else ()
                ),
                billing_intent=billing_intent,
            )
        )
        return tuple(prefix)


def public_events(
    *,
    outcome: TurnOutcome,
    session_id: str,
    turn_id: str,
    usage_entries: tuple[ModelUsageEntry, ...] = (),
    trace: AnalysisTrace | None = None,
    billing_intent: BillingIntent | None = None,
) -> tuple[ChatEvent, ...]:
    """Compatibility function for callers migrating to `ChatEventProjector`."""

    return ChatEventProjector.project_outcome(
        outcome=outcome,
        session_id=session_id,
        turn_id=turn_id,
        usage_entries=usage_entries,
        trace=trace,
        billing_intent=billing_intent,
    )
