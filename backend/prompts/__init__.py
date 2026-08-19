"""Prompt text used by the UK chat backend.

Split by audience so each file owns one set of
model-facing instructions, but this package re-exports the public surface so
callers keep doing `from prompts import X`. Keep the constants declarative:
routes assemble blocks and call models; this package owns the prompt text.

For model-neutral engineering guidance around this runtime pathway, see
`docs/engineering/skills/uk-chat-runtime.md`.
"""

from prompts.meta import SUGGESTION_SYSTEM, TITLE_SYSTEM

__all__ = [
    "SUGGESTION_SYSTEM",
    "TITLE_SYSTEM",
]
