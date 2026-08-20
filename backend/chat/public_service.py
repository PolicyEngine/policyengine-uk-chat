"""Public-chat accounting and SSE projection over the shared turn engine."""

import json
import logging
from collections.abc import AsyncIterator

import billing
from billing.config import billing_enabled
from observability.segments import SegmentName
from policyengine_observability import segment

from analysis.models import BillingIntent
from billing.processor import (
    BillingIntentProcessor,
    BillingRecordResult,
)

from chat.events import (
    ChatEvent,
    ChatUsage,
    CancellationProbe,
    CancellationAccepted,
    ClarificationRequired,
    DuplicateProcessed,
    SuggestionsGenerated,
    TextChunk,
    ThinkingCompleted,
    ToolCompleted,
    ToolStarted,
    ToolUsed,
    TurnCancelled,
    TurnCompleted,
    TurnConflict,
    TurnFailed,
)
from chat.orchestrator import run_chat_turn
from chat.schemas import ChatRequest
from chat.turn_input import ChatTurnInput, prepare_turn_input
from analysis.persistence import SqlAnalysisStore
from analysis.store import MarkBillingRecordedCommand


logger = logging.getLogger(__name__)
MAX_PUBLIC_TOOL_RESULT_CHARS = 5000


def _serialise_public_operation_summary(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class InsufficientCredit(Exception):
    """Raised before streaming when an authenticated user has no credit."""


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _record_turn_usage(
    *,
    user_id: str | None,
    session_id: str,
    model: str | None,
    usage: ChatUsage,
    turn_id: str | None,
    usage_entries=(),
) -> dict | None:
    if not billing_enabled():
        return None

    try:
        with segment(SegmentName.BILLING_RECORD_USAGE):
            serialized_entries = [
                entry.model_dump(mode="json")
                if hasattr(entry, "model_dump")
                else dict(entry)
                for entry in usage_entries
            ] or None
            result = None
            for _attempt in range(2):
                result = billing.record_usage(
                    user_id=user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    model=model,
                    usage_entries=serialized_entries,
                    **usage.as_dict(),
                )
                if result.get("recorded", True):
                    break
            return result
    except Exception as exc:
        logger.warning("[CHAT] Failed to record usage: %s", exc)
        return None


def _mark_billing_intent_recorded(
    *,
    billing_result: dict | None,
    session_id: str,
    turn_id: str | None,
) -> None:
    if not billing_result or not billing_result.get("recorded") or not turn_id:
        return
    try:
        SqlAnalysisStore().mark_billing_recorded(
            MarkBillingRecordedCommand(session_id=session_id, turn_id=turn_id)
        )
    except Exception:
        logger.warning(
            "[CHAT] Billing succeeded but the analysis billing intent "
            "could not be marked recorded",
            exc_info=True,
        )


def _record_billing_intent(intent: BillingIntent) -> BillingRecordResult:
    usage = ChatUsage()
    for charge in intent.charge_inputs:
        usage = usage.plus(
            {
                "input_tokens": charge.input_tokens,
                "output_tokens": charge.output_tokens,
                "cache_creation_input_tokens": (
                    charge.cache_creation_input_tokens
                ),
                "cache_read_input_tokens": charge.cache_read_input_tokens,
            }
        )
    response = _record_turn_usage(
        user_id=intent.user_id,
        session_id=str(intent.session_id),
        turn_id=str(intent.turn_id),
        model=None,
        usage=usage,
        usage_entries=intent.charge_inputs,
    )
    response = response or {}
    return BillingRecordResult(
        recorded=bool(response.get("recorded")),
        duplicate=bool(response.get("duplicate")),
        cost_gbp=float(response.get("cost_gbp", 0)),
        response=response,
    )


def _process_billing_intent(
    intent: BillingIntent,
    *,
    store=None,
) -> BillingRecordResult:
    processor = BillingIntentProcessor(
        store=store or SqlAnalysisStore(),
        recorder=_record_billing_intent,
    )
    return processor.process(intent)


def _retry_pending_billing_intents(
    *,
    user_id: str,
    store=None,
) -> None:
    """Retry durable charges from earlier turns using immutable token inputs."""

    if not billing_enabled():
        return
    resolved_store = store or SqlAnalysisStore()
    try:
        BillingIntentProcessor(
            store=resolved_store,
            recorder=_record_billing_intent,
        ).process_pending(user_id=user_id)
    except Exception:
        logger.warning("[CHAT] Failed to retry pending billing intents", exc_info=True)


def _public_payload(event: ChatEvent, billing: dict | None = None) -> dict | None:
    if isinstance(event, ToolStarted):
        return {
            "type": event.type,
            "tool_name": event.tool_name,
            "tool_id": event.tool_id,
        }
    if isinstance(event, TextChunk):
        return {"type": event.type, "content": event.content}
    if isinstance(event, ToolUsed):
        return {
            "type": event.type,
            "tool_name": event.tool_name,
            "tool_id": event.tool_id,
            "tool_input": event.tool_input,
            "status": "pending",
        }
    if isinstance(event, ThinkingCompleted):
        return {"type": event.type}
    if isinstance(event, ToolCompleted):
        output = event.output if isinstance(event.output, dict) else {}
        summary = _serialise_public_operation_summary(
            {
                "status": output.get("status", event.status),
                "result_kind": event.tool_name,
                **({"error": "operation failed"} if event.status == "error" else {}),
            }
        )
        return {
            "type": event.type,
            "tool_name": event.tool_name,
            "tool_id": event.tool_id,
            "status": event.status,
            "result_summary": summary,
        }
    if isinstance(event, TurnCompleted):
        return {
            "type": event.type,
            "content": event.content,
            "session_id": event.session_id,
            "model": event.model,
            "route": event.route,
            "outcome": event.outcome,
            "stop_reason": event.stop_reason,
            "usage": event.usage.as_dict(),
            "cost_gbp": billing["cost_gbp"] if billing else None,
            "balance": billing["balance"] if billing else None,
            "turn_id": event.turn_id,
            "processed_duplicate": event.processed_duplicate,
            **(
                {
                    "artifacts": [
                        artifact.model_dump(mode="json")
                        if hasattr(artifact, "model_dump")
                        else dict(artifact)
                        for artifact in event.response_artifacts
                    ]
                }
                if event.response_artifacts
                else {}
            ),
        }
    if isinstance(event, SuggestionsGenerated):
        return {"type": event.type, "suggestions": event.suggestions}
    if isinstance(event, TurnFailed):
        return {
            "type": event.type,
            "content": event.content,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "model": event.model,
            "route": "failed",
            "outcome": "failed",
            "stop_reason": event.stop_reason,
            "usage": event.usage.as_dict(),
            "cost_gbp": billing["cost_gbp"] if billing else None,
            "balance": billing["balance"] if billing else None,
            "billable": event.billable,
        }
    if isinstance(event, TurnCancelled):
        return None
    if isinstance(event, ClarificationRequired):
        return {
            "type": event.type,
            "question_id": event.question_id,
            "reason_code": event.reason_code,
        }
    if isinstance(event, TurnConflict):
        return {
            "type": event.type,
            "content": event.content,
            "session_id": event.session_id,
            "turn_id": event.turn_id,
            "retryable": event.retryable,
        }
    if isinstance(event, DuplicateProcessed):
        return {"type": event.type, "status": event.status}
    if isinstance(event, CancellationAccepted):
        return {"type": event.type, "status": "accepted"}
    raise TypeError(f"Unsupported chat event: {type(event).__name__}")


async def _stream_public_chat(
    chat_request: ChatRequest,
    turn: ChatTurnInput,
    *,
    is_cancelled: CancellationProbe,
) -> AsyncIterator[str]:
    async for event in run_chat_turn(turn, is_cancelled=is_cancelled):
        billing = None
        if isinstance(event, TurnCompleted):
            if not event.processed_duplicate:
                if event.billing_intent is not None:
                    result = _process_billing_intent(event.billing_intent)
                    billing = dict(result.response) if result.response else None
                else:
                    billing = _record_turn_usage(
                        user_id=chat_request.user_id,
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                        model=event.model,
                        usage=event.usage,
                        usage_entries=event.usage_entries,
                    )
                    _mark_billing_intent_recorded(
                        billing_result=billing,
                        session_id=event.session_id,
                        turn_id=event.turn_id,
                    )
        elif isinstance(event, TurnCancelled):
            _record_turn_usage(
                user_id=chat_request.user_id,
                session_id=event.session_id,
                turn_id=event.turn_id,
                model=event.model,
                usage=event.usage,
                usage_entries=(),
            )
        elif isinstance(event, TurnFailed) and event.billable:
            if event.billing_intent is not None:
                result = _process_billing_intent(event.billing_intent)
                billing = dict(result.response) if result.response else None
            else:
                billing = _record_turn_usage(
                    user_id=chat_request.user_id,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                    model=event.model,
                    usage=event.usage,
                    usage_entries=event.usage_entries,
                )
                _mark_billing_intent_recorded(
                    billing_result=billing,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                )

        payload = _public_payload(event, billing)
        if payload is not None:
            yield _sse(payload)


async def start_public_chat(
    chat_request: ChatRequest,
    *,
    is_cancelled: CancellationProbe,
) -> AsyncIterator[str]:
    """Authorize a public turn and return its lazily consumed SSE stream."""

    if billing_enabled() and chat_request.user_id:
        _retry_pending_billing_intents(user_id=chat_request.user_id)
        try:
            with segment(SegmentName.BILLING_CHECK_BALANCE):
                has_credit, _ = billing.check_balance(chat_request.user_id)
            if not has_credit:
                raise InsufficientCredit
        except RuntimeError:
            pass

    # Prepare before returning so invalid payloads map to an HTTP 400 rather than
    # failing after a 200 streaming response has already started.
    turn = prepare_turn_input(chat_request)
    return _stream_public_chat(chat_request, turn, is_cancelled=is_cancelled)
