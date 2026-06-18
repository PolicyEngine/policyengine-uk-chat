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


def get_async_client():
    """Build an asynchronous Anthropic client (the chat stream + the follow-up
    suggestion helper). `anthropic` is imported lazily."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.AsyncAnthropic(api_key=api_key)
