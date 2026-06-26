"""Model-call configuration — the dependency-free seam every caller imports.

Split by sub-responsibility (models / sampling / clients / scope) so each file
owns one concern, but this package re-exports the public surface so callers do
`from config import X` regardless of which sub-module owns X. Kept import-light
(no heavy deps) so the gateway and the eval harness can import it without
dragging in the chat route.
"""

from config.clients import get_async_client, get_sync_client
from config.models import (
    DEFAULT_COMPLEX_MODEL,
    DEFAULT_FAST_MODEL,
    DEFAULT_REASONING_MODEL,
    FAST_MODEL_MAX_INPUT_TOKENS,
    SUGGESTION_MODEL,
    SUGGESTION_TIMEOUT_SECS,
    TITLE_MODEL,
)
from config.sampling import DEFAULT_TEMPERATURE, SUGGESTION_TEMPERATURE
from config.scope import load_scope_descriptor

__all__ = [
    "DEFAULT_TEMPERATURE",
    "SUGGESTION_TEMPERATURE",
    "DEFAULT_FAST_MODEL",
    "DEFAULT_COMPLEX_MODEL",
    "DEFAULT_REASONING_MODEL",
    "TITLE_MODEL",
    "SUGGESTION_MODEL",
    "SUGGESTION_TIMEOUT_SECS",
    "FAST_MODEL_MAX_INPUT_TOKENS",
    "get_sync_client",
    "get_async_client",
    "load_scope_descriptor",
]
