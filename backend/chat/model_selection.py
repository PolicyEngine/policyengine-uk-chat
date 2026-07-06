"""Chat model selection and small turn-classification helpers."""

import logging
import re
from typing import Any, List

from config import (
    DEFAULT_COMPLEX_MODEL,
    DEFAULT_FAST_MODEL,
    DEFAULT_REASONING_MODEL,
    FAST_MODEL_MAX_INPUT_TOKENS,
)
from prompts import SYSTEM_PROMPT

from chat.system_blocks import REFERENCE_DOC

logger = logging.getLogger(__name__)


_REFORM_CAPABLE_TOOLS = {
    "analyse_microdata",
    "calculate_household",
    "run_economy_simulation",
}

_DISTRIBUTIONAL_OUTPUTS = {
    "decile_impact",
    "marginal_rate",
    "poverty_impact",
    "winners_losers",
}

_DISTRIBUTIONAL_TEXT_RE = re.compile(
    r"\b(?:deciles?|quintiles?|distributional|winners?|losers?|poverty|"
    r"inequality|gini|marginal tax|effective tax|marginal rate|effective rate)\b",
    re.IGNORECASE,
)

_POLICY_CONTEXT_RE = re.compile(
    r"\b(?:income tax|tax(?:es)?|taxable|national insurance|ni|universal credit|"
    r"uc|child benefit|pension credit|housing benefit|council tax|vat|"
    r"capital gains tax|inheritance tax|benefit(?:s)?|allowance(?:s)?|"
    r"personal allowance|threshold(?:s)?|basic rate|higher rate|"
    r"additional rate|tax rate|marginal rate|effective rate|taper rate|"
    r"band(?:s)?|taper(?:s)?|lha)\b",
    re.IGNORECASE,
)

_REFORM_INTENT_RE = re.compile(
    r"\b(?:reform|raise|increase|decrease|cut|reduce|lower|change|replace|"
    r"freeze|uprate|abolish|scrap|introduce|set)\b",
    re.IGNORECASE,
)

_POLICY_NUMERIC_CHANGE_RE = re.compile(
    r"(?:\bby\s+\d+(?:\.\d+)?\s*%)"
    r"|(?:\bfrom\s+\d+(?:\.\d+)?\s*%\s*to\s+\d+(?:\.\d+)?\s*%)"
    r"|(?:\b\d+\s*pp\b)",
    re.IGNORECASE,
)


# Upper-bound token cost of one attached image. Anthropic bills an image at
# roughly (width * height) / 750 tokens, capped near this for a full-size image.
# We don't have dimensions at selection time, so use the cap as a safe estimate
# rather than counting the base64 payload as if it were text (which inflated a
# ~500 KB photo to ~170k "tokens" and misrouted trivial questions to the
# expensive model).
_IMAGE_TOKEN_ESTIMATE = 1600


def _estimate_message_tokens(messages: List[dict]) -> int:
    char_count = 0
    image_count = 0
    for block in messages:
        content = block.get("content", "")
        if isinstance(content, str):
            char_count += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image":
                    image_count += 1
                elif isinstance(part, dict) and part.get("type") == "text":
                    char_count += len(str(part.get("text", "")))
                else:
                    # tool_use / tool_result / other blocks — count serialised size.
                    char_count += len(str(part))
        else:
            char_count += len(str(content))
    return char_count // 4 + image_count * _IMAGE_TOKEN_ESTIMATE


def _slot_value(slot: Any, attr: str) -> Any:
    if isinstance(slot, dict):
        return slot.get(attr)
    return getattr(slot, attr, None)


def _detect_gateway_reasoning_signal(verdict: Any) -> str | None:
    """Return a structured reform/distributional signal from the gateway."""
    if verdict is None:
        return None
    tool = getattr(verdict, "tool", None)
    slots = getattr(verdict, "slots", []) or []
    for slot in slots:
        name = str(_slot_value(slot, "name") or "")
        kind = str(_slot_value(slot, "kind") or "tool_input")
        source = str(_slot_value(slot, "source") or "")
        if (
            tool in _REFORM_CAPABLE_TOOLS
            and name == "reform"
            and source in {"prompt", "default"}
        ):
            return f"gateway:{tool}:reform"
        if kind == "output" and name in _DISTRIBUTIONAL_OUTPUTS:
            return f"gateway:{tool}:output:{name}"
    return None


def _detect_reform_signal(text: str) -> str | None:
    """Return a narrow text reform/distributional signal, if any.

    Opening turns should prefer the gateway's structured plan. This text check
    is a fallback for follow-ups and gateway fail-safe turns, so it requires
    policy context before generic change language can trigger the reasoning
    model.
    """
    if not text:
        return None
    distributional = _DISTRIBUTIONAL_TEXT_RE.search(text)
    if distributional:
        return distributional.group(0)

    if not _POLICY_CONTEXT_RE.search(text):
        return None

    reform_intent = _REFORM_INTENT_RE.search(text)
    if reform_intent:
        return reform_intent.group(0)

    numeric_change = _POLICY_NUMERIC_CHANGE_RE.search(text)
    if numeric_change:
        return numeric_change.group(0)
    return None


def select_chat_model(
    messages: List[dict],
    *,
    charts_mode: bool = False,
    gateway_verdict: Any = None,
) -> str:
    signal = _detect_gateway_reasoning_signal(gateway_verdict)
    if signal is None:
        signal = _detect_reform_signal(last_user_text(messages))
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


def last_user_text(conversation: List[dict]) -> str:
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


def is_followup(conversation: List[dict]) -> bool:
    """True once the conversation contains an assistant turn — i.e. this is not
    the opening user message. The gateway runs only on the opening turn; a
    single-message classifier can't see the context a follow-up depends on, and
    a user's reply to a partial/needs_plan prompt should flow straight to
    compute (which it does, because that turn now has a prior assistant reply).
    """
    return any(msg.get("role") == "assistant" for msg in conversation)
