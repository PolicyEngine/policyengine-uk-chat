"""Prompt text used by the UK chat backend.

Split by audience (system / gateway / meta) so each file owns one set of
model-facing instructions, but this package re-exports the public surface so
callers keep doing `from prompts import X`. Keep the constants declarative:
routes assemble blocks and call models; this package owns the prompt text.

For model-neutral engineering guidance around this runtime pathway, see
`docs/engineering/skills/uk-chat-runtime.md`.
"""

from prompts.gateway import (
    DEFAULT_SCOPE_DESCRIPTOR,
    GATEWAY_IRRELEVANT_DIRECTIVE,
    GATEWAY_NEEDS_PLAN_DIRECTIVE,
    GATEWAY_OUT_OF_SCOPE_DIRECTIVE,
    GATEWAY_PARTIAL_DIRECTIVE,
    gateway_system,
    lightweight_system,
)
from prompts.meta import SUGGESTION_SYSTEM, TITLE_SYSTEM
from prompts.system import CHARTS_MODE_DIRECTIVE, SYSTEM_PROMPT

__all__ = [
    "SYSTEM_PROMPT",
    "CHARTS_MODE_DIRECTIVE",
    "DEFAULT_SCOPE_DESCRIPTOR",
    "lightweight_system",
    "gateway_system",
    "GATEWAY_IRRELEVANT_DIRECTIVE",
    "GATEWAY_OUT_OF_SCOPE_DIRECTIVE",
    "GATEWAY_PARTIAL_DIRECTIVE",
    "GATEWAY_NEEDS_PLAN_DIRECTIVE",
    "SUGGESTION_SYSTEM",
    "TITLE_SYSTEM",
]
