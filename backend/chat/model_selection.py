"""Chat model selection and small turn-classification helpers."""

from typing import List

from config import DEFAULT_COMPLEX_MODEL, DEFAULT_FAST_MODEL, FAST_MODEL_MAX_INPUT_TOKENS
from prompts import SYSTEM_PROMPT

from chat.system_blocks import REFERENCE_DOC


def _estimate_message_tokens(messages: List[dict]) -> int:
    char_count = sum(len(str(block.get("content", ""))) for block in messages)
    return char_count // 4


def _select_chat_model(messages: List[dict]) -> str:
    estimated_input_tokens = (
        _estimate_message_tokens(messages)
        + len(SYSTEM_PROMPT) // 4
        + len(REFERENCE_DOC) // 4
    )
    if estimated_input_tokens > FAST_MODEL_MAX_INPUT_TOKENS:
        return DEFAULT_COMPLEX_MODEL
    return DEFAULT_FAST_MODEL


def _last_user_text(conversation: List[dict]) -> str:
    """Latest user message as plain text (flattening any image+text content)."""
    for msg in reversed(conversation):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):  # [image block, text block, ...]
            return " ".join(
                str(b.get("text", "")) for b in content if isinstance(b, dict)
            ).strip()
    return ""


def _is_followup(conversation: List[dict]) -> bool:
    """True once the conversation contains an assistant turn — i.e. this is not
    the opening user message. The gateway runs only on the opening turn; a
    single-message classifier can't see the context a follow-up depends on, and
    a user's reply to a partial/needs_plan prompt should flow straight to
    compute (which it does, because that turn now has a prior assistant reply).
    """
    return any(msg.get("role") == "assistant" for msg in conversation)
