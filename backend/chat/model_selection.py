"""Chat model selection and small turn-classification helpers."""

import logging
import re
from typing import List

from config import (
    DEFAULT_COMPLEX_MODEL,
    DEFAULT_FAST_MODEL,
    DEFAULT_REASONING_MODEL,
    FAST_MODEL_MAX_INPUT_TOKENS,
)
from prompts import SYSTEM_PROMPT

from chat.system_blocks import REFERENCE_DOC

logger = logging.getLogger(__name__)


_REFORM_KEYWORDS: List[str] = [
    "decile",
    "quintile",
    "distributional",
    "winners",
    "losers",
    "poverty",
    "inequality",
    "gini",
    "reform",
    "increase the",
    "raise the",
    "cut the",
    "change the",
    "replace",
    "freeze",
    "uprate",
    "bump",
    "marginal rate",
    "effective rate",
    "marginal tax",
    "effective tax",
    "percentage point",
    "1pp",
]

_REFORM_REGEX = re.compile(
    r"(?:\bby\s+\d+(?:\.\d+)?\s*%)"
    r"|(?:\bfrom\s+\d+(?:\.\d+)?\s*%\s*to\s+\d+(?:\.\d+)?\s*%)"
    r"|(?:\b\d+\s*pp\b)",
    re.IGNORECASE,
)


def _estimate_message_tokens(messages: List[dict]) -> int:
    char_count = sum(len(str(block.get("content", ""))) for block in messages)
    return char_count // 4


def _detect_reform_signal(text: str) -> str | None:
    """Return the matched reform/distributional signal, if any."""
    if not text:
        return None
    lowered = text.lower()
    for keyword in _REFORM_KEYWORDS:
        if keyword in lowered:
            return keyword
    match = _REFORM_REGEX.search(lowered)
    if match:
        return match.group(0)
    return None


def _select_chat_model(messages: List[dict], *, charts_mode: bool = False) -> str:
    signal = _detect_reform_signal(_last_user_text(messages))
    if signal:
        logger.info("[MODEL] Routed to reasoning model (signal=%r)", signal)
        return DEFAULT_REASONING_MODEL

    if charts_mode:
        logger.info("[MODEL] Routed to reasoning model (charts_mode=True)")
        return DEFAULT_REASONING_MODEL

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
