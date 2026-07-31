"""Token-protected HTTP surface for deployed UK Chat evaluations."""

import os
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from chat.schemas import ChatRequest
from chat.turn_input import InvalidChatRequest
from eval.schemas import EvalChatResponse
from eval.service import run_eval_chat


router = APIRouter(prefix="/eval", tags=["evaluation"])


def require_eval_token(
    token: Annotated[str | None, Header(alias="X-Eval-Token")] = None,
) -> None:
    configured = os.environ.get("UK_CHAT_EVAL_TOKEN")
    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Evaluation endpoint is not configured",
        )
    if token is None or not secrets.compare_digest(token, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid evaluation token",
        )


@router.post("/chat/message", response_model=EvalChatResponse)
async def eval_chat_message(
    request: Request,
    chat_request: ChatRequest,
    _authorized: Annotated[None, Depends(require_eval_token)],
) -> EvalChatResponse:
    try:
        return await run_eval_chat(
            chat_request,
            is_cancelled=request.is_disconnected,
        )
    except InvalidChatRequest as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
