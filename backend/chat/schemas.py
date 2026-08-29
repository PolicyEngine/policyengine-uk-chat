"""Request/response models for the chat endpoints."""

from typing import List

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    session_id: str | None = None
    turn_id: str | None = None
    user_id: str | None = None
    charts_mode: bool = False
    debug: bool = False
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
