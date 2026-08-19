"""Atomic typed finalization of one analysis turn."""

from __future__ import annotations

from dataclasses import dataclass

from analysis.common import AnalysisError, AnalysisErrorCode, canonical_hash, stable_identifier
from analysis.models import (
    AnalysisSessionState,
    BillingIntent,
    CancelledTurnOutcome,
    ClarificationTurnOutcome,
    CompletedTurnOutcome,
    ConflictTurnOutcome,
    FailedTurnOutcome,
    FinalizationIntent,
    ModelUsageEntry,
    ResponseArtifact,
    StillProcessingTurnOutcome,
    TurnOutcome,
    TurnReceipt,
    TurnReceiptStatus,
    UnsupportedTurnOutcome,
    WorkflowPhase,
    WorkflowTransition,
)
from analysis.store import AnalysisStore
from analysis.trace import AnalysisTrace


def _receipt_status(outcome: TurnOutcome) -> TurnReceiptStatus:
    if isinstance(outcome, FailedTurnOutcome):
        return TurnReceiptStatus.FAILED
    if isinstance(outcome, CancelledTurnOutcome):
        return TurnReceiptStatus.CANCELLED
    if isinstance(outcome, ConflictTurnOutcome):
        return TurnReceiptStatus.CONFLICT
    return TurnReceiptStatus.COMPLETED


def outcome_is_billable(outcome: TurnOutcome) -> bool:
    return not isinstance(
        outcome,
        (ConflictTurnOutcome, StillProcessingTurnOutcome),
    ) and not (isinstance(outcome, FailedTurnOutcome) and not outcome.billable)


def _response_metadata(outcome: TurnOutcome) -> dict[str, object]:
    if isinstance(outcome, CompletedTurnOutcome):
        return {"route": outcome.route, "model": outcome.model}
    if isinstance(outcome, ClarificationTurnOutcome):
        return {
            "question_id": str(outcome.question_id),
            "reason_code": outcome.reason_code,
            "model": outcome.model,
        }
    if isinstance(outcome, UnsupportedTurnOutcome):
        return {"reason_code": outcome.reason_code, "model": outcome.model}
    if isinstance(outcome, FailedTurnOutcome):
        return {
            "error_code": outcome.error_code,
            "retryable": outcome.retryable,
            "model": outcome.model,
        }
    if isinstance(outcome, CancelledTurnOutcome):
        return {
            "request_revision_id": outcome.request_revision_id,
            "model": outcome.model,
        }
    if isinstance(outcome, ConflictTurnOutcome):
        return {"retryable": outcome.retryable}
    return {}


def _validate_agreement(
    outcome: TurnOutcome,
    state: AnalysisSessionState,
    intent: FinalizationIntent = FinalizationIntent.COMMIT_TRANSITION,
) -> None:
    if isinstance(outcome, StillProcessingTurnOutcome):
        raise AnalysisError(
            AnalysisErrorCode.OUTCOME_INVALID,
            f"{outcome.kind} is a replay-only outcome",
        )
    if isinstance(outcome, ConflictTurnOutcome):
        if intent != FinalizationIntent.RECEIPT_ONLY:
            raise AnalysisError(
                AnalysisErrorCode.OUTCOME_INVALID,
                "conflict outcome requires receipt-only finalization",
            )
        return
    if isinstance(outcome, CompletedTurnOutcome):
        allowed = {
            "execution_question": set(WorkflowPhase),
            "explanation": {WorkflowPhase.COMPLETED},
            "standard": {WorkflowPhase.COMPLETED},
            "exploratory": {WorkflowPhase.COMPLETED},
        }.get(outcome.route, {WorkflowPhase.COMPLETED})
    else:
        allowed_by_kind: dict[str, set[WorkflowPhase]] = {
            "clarification": {
                WorkflowPhase.AWAITING_CLARIFICATION,
                WorkflowPhase.EXECUTING,
            },
            "unsupported": {
                WorkflowPhase.FAILED,
                WorkflowPhase.EXECUTING,
                WorkflowPhase.CANCELLED,
            },
            "failed": {
                WorkflowPhase.FAILED,
                WorkflowPhase.EXECUTING,
                WorkflowPhase.CANCELLED,
            },
            "cancelled": set(WorkflowPhase),
        }
        allowed = allowed_by_kind.get(outcome.kind)
    if allowed is not None and state.phase not in allowed:
        raise AnalysisError(
            AnalysisErrorCode.OUTCOME_INVALID,
            f"{outcome.kind} outcome is inconsistent with {state.phase.value} state",
        )


@dataclass(frozen=True)
class FinalizationResult:
    state: AnalysisSessionState
    receipt: TurnReceipt
    outcome: TurnOutcome
    usage_entries: tuple[ModelUsageEntry, ...]
    billing_intent: BillingIntent | None
    live_response_artifacts: tuple[ResponseArtifact, ...]
    trace: AnalysisTrace | None


def finalize_turn(
    *,
    store: AnalysisStore,
    receipt: TurnReceipt,
    transition: WorkflowTransition,
    outcome: TurnOutcome,
    usage_entries: tuple[ModelUsageEntry, ...] = (),
    trace: AnalysisTrace | None = None,
    billing_intent: BillingIntent | None = None,
) -> FinalizationResult:
    """Commit lifecycle completion, replay data, usage, and billing intent once."""

    _validate_agreement(
        outcome,
        transition.next_state,
        transition.finalization_intent,
    )
    completed_receipt = TurnReceipt(
        session_id=receipt.session_id,
        turn_id=receipt.turn_id,
        request_hash=receipt.request_hash,
        state_version=transition.next_state.state_version,
        status=_receipt_status(outcome),
        outcome_category=outcome.kind,
        response_content=outcome.content,
        response_metadata=_response_metadata(outcome),
        usage_id=(
            stable_identifier("turn_usage", receipt.session_id, receipt.turn_id)
            if usage_entries
            else None
        ),
        response_checksum=canonical_hash(outcome.content),
        created_at=receipt.created_at,
    )
    if billing_intent is not None:
        expected_usage_ids = tuple(item.usage_entry_id for item in usage_entries)
        if not outcome_is_billable(outcome):
            raise AnalysisError(
                AnalysisErrorCode.OUTCOME_INVALID,
                "a non-billable outcome cannot persist a billing intent",
            )
        if (
            billing_intent.session_id != receipt.session_id
            or billing_intent.turn_id != receipt.turn_id
            or billing_intent.usage_entry_ids != expected_usage_ids
        ):
            raise AnalysisError(
                AnalysisErrorCode.OUTCOME_INVALID,
                "billing intent identity or usage entries do not match finalization",
            )
    intents = (billing_intent,) if billing_intent is not None else ()
    enriched = WorkflowTransition(
        expected_state_version=transition.expected_state_version,
        current_phase=transition.current_phase,
        next_state=transition.next_state,
        finalization_intent=transition.finalization_intent,
        revisions=transition.revisions,
        bound_requests=transition.bound_requests,
        clarifications=transition.clarifications,
        clarification_resolutions=transition.clarification_resolutions,
        plans=transition.plans,
        execution_attempts=transition.execution_attempts,
        execution_completions=transition.execution_completions,
        turn_receipts=(completed_receipt,),
        usage_entries=usage_entries,
        billing_intents=intents,
        status_changes=transition.status_changes,
    )
    state = store.commit_transition(enriched)
    return FinalizationResult(
        state=state,
        receipt=completed_receipt,
        outcome=outcome,
        usage_entries=usage_entries,
        billing_intent=billing_intent,
        live_response_artifacts=(
            outcome.response_artifacts
            if isinstance(outcome, CompletedTurnOutcome)
            else ()
        ),
        trace=trace,
    )


def replay_outcome(receipt: TurnReceipt) -> TurnOutcome:
    content = receipt.response_content or "This request is still processing."
    category = receipt.outcome_category
    metadata = receipt.response_metadata
    if receipt.status == TurnReceiptStatus.PROCESSING:
        return StillProcessingTurnOutcome(content=content)
    if category == "clarification":
        question_id = metadata.get("question_id")
        reason_code = metadata.get("reason_code")
        return ClarificationTurnOutcome(
            content=content,
            question_id=(
                question_id if isinstance(question_id, str) else "replayed_clarification"
            ),
            reason_code=(
                reason_code if isinstance(reason_code, str) else "replayed_clarification"
            ),
            model=metadata.get("model") if isinstance(metadata.get("model"), str) else None,
            duplicate=True,
        )
    if category == "unsupported":
        return UnsupportedTurnOutcome(
            content=content,
            reason_code=str(metadata.get("reason_code", "replayed")),
            duplicate=True,
        )
    if category == "failed":
        return FailedTurnOutcome(
            content=content,
            error_code=str(metadata.get("error_code", "replayed_failure")),
            retryable=bool(metadata.get("retryable", False)),
            duplicate=True,
        )
    if category == "cancelled":
        return CancelledTurnOutcome(
            content=content,
            request_revision_id=(
                metadata.get("request_revision_id")
                if isinstance(metadata.get("request_revision_id"), str)
                else None
            ),
            duplicate=True,
        )
    if category == "conflict":
        return ConflictTurnOutcome(
            content=content,
            retryable=bool(metadata.get("retryable", True)),
            duplicate=True,
        )
    if receipt.status == TurnReceiptStatus.FAILED:
        return FailedTurnOutcome(
            content=content,
            error_code="replayed_failure",
            duplicate=True,
        )
    if receipt.status == TurnReceiptStatus.CANCELLED:
        return CancelledTurnOutcome(content=content, duplicate=True)
    if receipt.status == TurnReceiptStatus.CONFLICT:
        return ConflictTurnOutcome(content=content, duplicate=True)
    return CompletedTurnOutcome(
        content=content,
        route=str(metadata.get("route", "duplicate")),
        duplicate=True,
    )
