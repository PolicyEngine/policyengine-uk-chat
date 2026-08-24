"""Request/response models for the chat endpoints."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class PriorToolResult(BaseModel):
    """One tool result computed in an earlier turn of this conversation.

    Clients send these alongside the assistant message that produced them.
    Earlier clients folded the same information into the message text, which
    the model could not tell apart from prose; both forms are accepted.
    """

    tool_name: str
    result: str
    tool_input: Optional[Dict[str, Any]] = None


class ChatMessage(BaseModel):
    role: str
    content: str
    tool_results: Optional[List[PriorToolResult]] = None


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    session_id: str | None = None
    user_id: str | None = None
    charts_mode: bool = False
    # Optional image attached to the latest user message. Sent as raw base64
    # (no `data:image/...;base64,` prefix) plus a media type like `image/png`.
    # When present, the backend converts the latest user message into a
    # multi-block content list with an Anthropic vision image block + the
    # original text block before calling the Messages API.
    image_base64: str | None = None
    image_media_type: str | None = None


class TitleRequest(BaseModel):
    first_user_message: str
    first_assistant_message: str | None = None
