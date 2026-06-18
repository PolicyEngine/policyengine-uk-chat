"""Shared model-call configuration and runtime helpers.

Dependency-free seam that every caller — the chat route, the gateway, and the
eval harness — imports, so shared model defaults, the Anthropic client factory,
and the scope-descriptor loader live in one place instead of being duplicated
(which the chatbot<->gateway import direction would otherwise force).
"""

import os
from pathlib import Path

# Default sampling temperature for model calls. 0 = deterministic, which is what
# the compute loop, titling, the gateway classifier, and the evals all want.
DEFAULT_TEMPERATURE = float(os.environ.get("ANTHROPIC_TEMPERATURE", "0"))

# Follow-up suggestion chips deliberately sample with variety, so they get their
# own (higher) temperature rather than the deterministic default.
SUGGESTION_TEMPERATURE = float(os.environ.get("ANTHROPIC_SUGGESTION_TEMPERATURE", "1"))

# Default fast model, shared by the chat route and the gateway classifier.
DEFAULT_FAST_MODEL = os.environ.get("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5")

_SCOPE_DESCRIPTOR_PATH = Path(__file__).resolve().parent / "scope_descriptor.md"


def get_sync_client():
    """Build a synchronous Anthropic client. `anthropic` is imported lazily so
    this module stays importable for offline unit tests without the SDK."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)


def load_scope_descriptor(default: str) -> str:
    """Read the engine-generated `scope_descriptor.md`, falling back to `default`
    (the curated DEFAULT_SCOPE_DESCRIPTOR) when it hasn't been built locally."""
    try:
        return _SCOPE_DESCRIPTOR_PATH.read_text().strip()
    except FileNotFoundError:
        return default
