"""Normalize HTTP chat payloads into framework-independent turn input."""

import uuid
from dataclasses import dataclass
from typing import Any

from chat.schemas import ChatRequest


ALLOWED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class InvalidChatRequest(ValueError):
    """Raised when a syntactically valid ChatRequest cannot form a chat turn."""


@dataclass(frozen=True, slots=True)
class ChatTurnInput:
    messages: list[dict[str, Any]]
    session_id: str
    charts_mode: bool = False


def prepare_turn_input(chat_request: ChatRequest) -> ChatTurnInput:
    """Deduplicate messages, attach an optional image, and assign a session."""

    messages: list[dict[str, Any]] = [
        {"role": message.role, "content": message.content}
        for message in chat_request.messages
    ]
    deduplicated: list[dict[str, Any]] = []
    for message in messages:
        if not deduplicated or deduplicated[-1]["role"] != message["role"]:
            deduplicated.append(message)
        else:
            deduplicated[-1]["content"] += "\n\n" + message["content"]

    if chat_request.image_base64 or chat_request.image_media_type:
        if not (chat_request.image_base64 and chat_request.image_media_type):
            raise InvalidChatRequest(
                "image_base64 and image_media_type must be provided together"
            )
        media_type = chat_request.image_media_type
        if media_type not in ALLOWED_IMAGE_MEDIA_TYPES:
            raise InvalidChatRequest(f"Unsupported image media type: {media_type}")

        for index in range(len(deduplicated) - 1, -1, -1):
            if deduplicated[index]["role"] != "user":
                continue
            text_content = deduplicated[index]["content"]
            content: list[dict[str, Any]] = [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": chat_request.image_base64,
                    },
                }
            ]
            if text_content:
                content.append({"type": "text", "text": text_content})
            deduplicated[index] = {"role": "user", "content": content}
            break

    return ChatTurnInput(
        messages=deduplicated,
        session_id=chat_request.session_id or str(uuid.uuid4()),
        charts_mode=chat_request.charts_mode,
    )
