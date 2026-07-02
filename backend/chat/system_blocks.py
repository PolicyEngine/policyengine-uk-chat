"""Assembly of the model system blocks, plus the reference doc, the engine
scope descriptor, and the lightweight (no-computation) system used by the
gateway's non-`ready` outcomes.
"""

import json
import logging
from pathlib import Path
from typing import Any, List

from config import load_scope_descriptor
from gateway import gateway_writer_directive
from prompts import (
    CHARTS_MODE_DIRECTIVE,
    DEFAULT_SCOPE_DESCRIPTOR,
    SYSTEM_PROMPT,
    lightweight_system,
)
from tools.definitions import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)

_REFERENCE_PATH = Path(__file__).resolve().parent.parent / "reference.md"
try:
    REFERENCE_DOC = _REFERENCE_PATH.read_text()
    logger.info(f"[CHAT] Loaded reference.md ({len(REFERENCE_DOC)} chars)")
except FileNotFoundError:
    REFERENCE_DOC = ""
    logger.warning("[CHAT] reference.md not found — run engine/reference.py")

# Engine-derived scope descriptor (generated alongside reference.md), with a
# curated fallback for local dev. Drives the gateway's lightweight prompt.
SCOPE_DESCRIPTOR = load_scope_descriptor(DEFAULT_SCOPE_DESCRIPTOR)
LIGHTWEIGHT_SYSTEM = lightweight_system(SCOPE_DESCRIPTOR)


def tool_defs_for_anthropic():
    """Convert our TOOL_DEFINITIONS to Anthropic SDK format.
    Mark the last tool with cache_control so the system prompt + all tools
    are cached across requests (prompt caching)."""
    defs = []
    for i, t in enumerate(TOOL_DEFINITIONS):
        d = {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        if i == len(TOOL_DEFINITIONS) - 1:
            d["cache_control"] = {"type": "ephemeral"}
        defs.append(d)
    return defs


def serialise_tool_result(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def build_system_blocks(
    charts_mode: bool = False,
    gateway_plan: str | None = None,
) -> List[dict]:
    """System prompt + cached library reference + optional per-turn directives.

    The system prompt and reference are each marked with cache_control so they
    persist across requests. Per-turn directives (charts mode, the gateway plan)
    are appended AFTER both cache breakpoints so toggling them never invalidates
    cached blocks.
    """
    blocks: List[dict] = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    if REFERENCE_DOC:
        blocks.append({
            "type": "text",
            "text": REFERENCE_DOC,
            "cache_control": {"type": "ephemeral"},
        })
    if charts_mode:
        blocks.append({"type": "text", "text": CHARTS_MODE_DIRECTIVE})
    if gateway_plan:
        blocks.append({"type": "text", "text": gateway_plan})
    return blocks


def build_lightweight_system_blocks(verdict) -> List[dict]:
    """Lean system payload for a non-`ready` gateway outcome: the lightweight
    prompt (no reference doc, no tools) plus the per-outcome writer directive.
    The model still writes the actual reply to the user's message.
    """
    blocks: List[dict] = [{
        "type": "text",
        "text": LIGHTWEIGHT_SYSTEM,
        "cache_control": {"type": "ephemeral"},
    }]
    directive = gateway_writer_directive(verdict)
    if directive:
        blocks.append({"type": "text", "text": directive})
    return blocks
