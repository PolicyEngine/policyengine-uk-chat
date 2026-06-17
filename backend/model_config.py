"""Shared model-call configuration.

Lives in its own dependency-free module so every caller — the chat route, the
gateway, and the eval harness — imports the same values without circular
imports. Every `messages.create` / `messages.stream` call in the app must set
its temperature from one of these constants rather than inheriting the SDK
default.
"""

import os

# Default sampling temperature for model calls. 0 = deterministic, which is what
# the compute loop, titling, the gateway classifier, and the evals all want.
DEFAULT_TEMPERATURE = float(os.environ.get("ANTHROPIC_TEMPERATURE", "0"))

# Follow-up suggestion chips deliberately sample with variety, so they get their
# own (higher) temperature rather than the deterministic default.
SUGGESTION_TEMPERATURE = float(os.environ.get("ANTHROPIC_SUGGESTION_TEMPERATURE", "1"))
