"""Assembly of model system blocks and the lightweight gateway system."""

import json
from typing import Any, List

from gateway import gateway_writer_directive
from prompts import (
    CHARTS_MODE_DIRECTIVE,
    DEFAULT_SCOPE_DESCRIPTOR,
    SYSTEM_PROMPT,
    lightweight_system,
)
from tools.definitions import TOOL_DEFINITIONS

SCOPE_DESCRIPTOR = DEFAULT_SCOPE_DESCRIPTOR
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
    """System prompt + optional per-turn directives.

    The system prompt is marked with cache_control so it persists across
    requests. Per-turn directives are appended after the cache breakpoint so
    toggling them does not invalidate the cached block.
    """
    blocks: List[dict] = [{
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }]
    if charts_mode:
        blocks.append({"type": "text", "text": CHARTS_MODE_DIRECTIVE})
    if gateway_plan:
        blocks.append({"type": "text", "text": gateway_plan})
    return blocks


def build_lightweight_system_blocks(verdict) -> List[dict]:
    """Lean system payload for a non-`ready` gateway outcome: the lightweight
    prompt with no tools plus the per-outcome writer directive.
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
