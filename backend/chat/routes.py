"""The /chat router: message streaming and title generation."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from api.rate_limit import CHAT_IP_LIMIT, CHAT_USER_LIMIT, chat_key_func, limiter

from chat.public_service import InsufficientCredit, start_public_chat
from chat.schemas import ChatRequest, TitleRequest
from chat.titles import make_title
from chat.turn_input import InvalidChatRequest

router = APIRouter(prefix="/chat", tags=["chatbot"])


@router.post("/title")
def generate_title(request: TitleRequest):
    return make_title(request)


@router.post("/message")
@limiter.limit(CHAT_USER_LIMIT, key_func=chat_key_func)
@limiter.limit(CHAT_IP_LIMIT)
async def chat_message(request: Request, chat_request: ChatRequest):
    # `request` is the Starlette Request that slowapi's @limiter.limit decorators
    # require; the parsed body is `chat_request`. Delegate to the orchestrator.
    try:
        stream = await start_public_chat(
            chat_request,
            is_cancelled=request.is_disconnected,
        )
    except InsufficientCredit:
        return JSONResponse(
            status_code=402,
            content={"error": "No credit remaining. Please top up to continue."},
        )
    except InvalidChatRequest as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
