"""The /chat router: message streaming and title generation."""

from fastapi import APIRouter, Request

from api.rate_limit import CHAT_IP_LIMIT, CHAT_USER_LIMIT, chat_key_func, limiter

from chat.orchestrator import stream_chat
from chat.schemas import ChatRequest, TitleRequest
from chat.titles import make_title

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
    return stream_chat(request, chat_request)
