"""Best-effort follow-up suggestion chips."""

import asyncio
import json
import logging
from typing import List

from config import (
    SUGGESTION_MODEL,
    SUGGESTION_TEMPERATURE,
    SUGGESTION_TIMEOUT_SECS,
    get_async_client,
)
from prompts import SUGGESTION_SYSTEM

logger = logging.getLogger(__name__)


async def _generate_followup_suggestions(
    last_user_message: str,
    assistant_answer: str,
) -> List[str]:
    """Best-effort follow-up question generation.

    Returns up to 3 short follow-up strings. ANY failure (timeout, API error,
    malformed JSON, empty result) returns []. Never raises — callers must be
    able to drop the suggestions silently without surfacing an error.
    """
    if not assistant_answer.strip():
        return []
    try:
        client = get_async_client()
        # Trim to keep input small: the helper sees the last user turn and the
        # assistant answer, both capped. Long tool transcripts aren't needed —
        # the answer text is what the user is reading.
        user_block = (
            "Latest user question:\n"
            + last_user_message.strip()[:1500]
            + "\n\nAssistant answer:\n"
            + assistant_answer.strip()[:4000]
        )
        response = await asyncio.wait_for(
            client.messages.create(
                model=SUGGESTION_MODEL,
                max_tokens=200,
                temperature=SUGGESTION_TEMPERATURE,
                system=SUGGESTION_SYSTEM,
                messages=[{"role": "user", "content": user_block}],
            ),
            timeout=SUGGESTION_TIMEOUT_SECS,
        )
        text_parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
        raw = "".join(text_parts).strip()
        if not raw:
            return []
        # Strip optional code fences just in case the model ignored instructions.
        if raw.startswith("```"):
            raw = raw.strip("`")
            if raw.lower().startswith("json"):
                raw = raw[4:]
            raw = raw.strip()
        # Allow either {"suggestions":[...]} or a bare JSON list.
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            items = parsed.get("suggestions") or parsed.get("questions") or []
        elif isinstance(parsed, list):
            items = parsed
        else:
            return []
        cleaned: List[str] = []
        for item in items:
            if not isinstance(item, str):
                continue
            s = item.strip().strip('"').strip()
            if not s:
                continue
            # Hard cap length and dedupe.
            if len(s) > 120:
                s = s[:117].rstrip() + "..."
            if s not in cleaned:
                cleaned.append(s)
            if len(cleaned) == 3:
                break
        return cleaned
    except Exception as e:  # noqa: BLE001 — best-effort, swallow everything
        logger.info(f"[CHAT] Follow-up suggestion generation failed (silent): {e}")
        return []
