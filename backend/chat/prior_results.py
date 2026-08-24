"""Tool results established by earlier turns of the same conversation.

The runtime is stateless: the client resends the transcript on every turn, and
a turn's `ToolExecutionContext` and result handles die with it. Earlier tool
output therefore reached later turns only as text the client had appended to
assistant prose, unlabelled and indistinguishable from the model's own words.
A later turn could restate a number the conversation had already computed, with
nothing marking the earlier figure as authoritative.

This module makes that carry-over typed and bounded. Prior results arrive as a
declared field on the request, are trimmed to a fixed budget, and are rendered
into a dedicated system block that names each value's source tool. What the
model may not do with them lives in the system prompt.

The content is still client-supplied and is not evidence that a tool ever ran.
It is treated as a consistency constraint on prose, never as a substitute for
computing: a turn that needs a number calls the tool again.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Most recent results are kept when a conversation exceeds the budget. Without
# a cap, replaying every prior turn's output grows without bound.
MAX_PRIOR_RESULTS = 20
MAX_RESULT_CHARS = 2000
MAX_BLOCK_CHARS = 20000

BLOCK_HEADER = "ESTABLISHED TOOL RESULTS FROM EARLIER TURNS"
BLOCK_PREAMBLE = (
    "These tool results were computed earlier in this conversation. They are "
    "the authoritative record of what this conversation has already "
    "established. Do not state a number that contradicts them. If your answer "
    "needs one of these figures, either repeat it exactly or recompute it with "
    "a tool; if a recomputed value differs, say so explicitly and explain what "
    "changed. Never silently replace an established figure with a different "
    "one. They are a record of earlier output, not a substitute for computing "
    "a value this turn."
)


@dataclass(frozen=True)
class PriorToolResult:
    """One tool result carried in from an earlier turn."""

    tool_name: str
    result: str
    tool_input: dict[str, Any] | None = None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "…[truncated]"


def bound_prior_results(results: list[PriorToolResult]) -> list[PriorToolResult]:
    """Keep the most recent results within the per-result and count budgets.

    Order is preserved so the block reads oldest to newest, matching the order
    the conversation computed them in.
    """

    kept = results[-MAX_PRIOR_RESULTS:]
    return [
        PriorToolResult(
            tool_name=item.tool_name,
            result=_truncate(item.result, MAX_RESULT_CHARS),
            tool_input=item.tool_input,
        )
        for item in kept
    ]


def render_established_results_block(results: list[PriorToolResult]) -> str | None:
    """Render prior results as one system block, or None when there are none."""

    bounded = bound_prior_results(results)
    if not bounded:
        return None

    lines = [BLOCK_HEADER, "", BLOCK_PREAMBLE, ""]
    for index, item in enumerate(bounded, start=1):
        lines.append(f"{index}. {item.tool_name}")
        if item.tool_input:
            lines.append(f"   called with: {item.tool_input}")
        lines.append(f"   returned: {item.result}")
    block = "\n".join(lines)
    return _truncate(block, MAX_BLOCK_CHARS)
