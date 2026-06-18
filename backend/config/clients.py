"""Anthropic client factories."""

import os


def get_sync_client():
    """Build a synchronous Anthropic client. `anthropic` is imported lazily so
    this module stays importable for offline unit tests without the SDK."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=api_key)
