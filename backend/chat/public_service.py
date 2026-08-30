"""Public-chat accounting and SSE projection over the shared turn engine."""

import json
import logging
from collections.abc import AsyncIterator

import billing
from billing.config import billing_enabled
from observability.segments import SegmentName
from policyengine_observability import segment

from chat.events import (
    ChatEvent,
    ChatUsage,
    CancellationProbe,
    SuggestionsGenerated,
    TextChunk,
    InvocationActivity,
    TurnCancelled,
    TurnCompleted,
    TurnFailed,
)
from chat.capability_runtime import run_capability_chat_turn
from chat.schemas import ChatRequest
from chat.turn_input import ChatTurnInput, prepare_turn_input


logger = logging.getLogger(__name__)


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
    turn_id: str | None = None,
) -> dict | None:
    if not billing_enabled():
        return None

    if turn_id is not None:
        try:
            from persistence.idempotency import SQLIdempotencyRepository

            if not SQLIdempotencyRepository().claim_billing(turn_id):
                return None
        except Exception as exc:
            logger.warning("[CHAT] Failed to claim idempotent billing: %s", exc)
            return None

    try:
        with segment(SegmentName.BILLING_RECORD_USAGE):
            return billing.record_usage(
                user_id=user_id,
                session_id=session_id,
                model=model,
                **usage.as_dict(),
            )
    except Exception as exc:
        logger.warning("[CHAT] Failed to record usage: %s", exc)
        return None


def _public_payload(event: ChatEvent, billing: dict | None = None) -> dict | None:
    if isinstance(event, TextChunk):
        return {"type": event.type, "content": event.content}
    if isinstance(event, InvocationActivity):
        record = event.record
        return {
            "type": event.type,
            "phase": event.phase,
            "invocation": record.model_dump(
                mode="json",
                exclude={"conversation_id"},
                exclude_none=True,
            ),
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
        }
    if isinstance(event, SuggestionsGenerated):
        return {"type": event.type, "suggestions": event.suggestions}
    if isinstance(event, TurnFailed):
        return {"type": event.type, "content": event.content}
    if isinstance(event, TurnCancelled):
        return None
    raise TypeError(f"Unsupported chat event: {type(event).__name__}")


async def _stream_public_chat(
    chat_request: ChatRequest,
    turn: ChatTurnInput,
    *,
    is_cancelled: CancellationProbe,
) -> AsyncIterator[str]:
    async for event in run_capability_chat_turn(turn, is_cancelled=is_cancelled):
        billing = None
        if isinstance(event, TurnCompleted):
            billing = _record_turn_usage(
                user_id=chat_request.user_id,
                session_id=event.session_id,
                model=event.model,
                usage=event.usage,
                turn_id=event.turn_id,
            )
        elif isinstance(event, TurnCancelled):
            _record_turn_usage(
                user_id=chat_request.user_id,
                session_id=event.session_id,
                model=event.model,
                usage=event.usage,
                turn_id=event.turn_id,
            )
        elif isinstance(event, TurnFailed) and event.billable:
            _record_turn_usage(
                user_id=chat_request.user_id,
                session_id=event.session_id,
                model=None,
                usage=event.usage,
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
