"""Model ids and the fast/complex routing threshold for chat model calls."""

import os

# Fast model: the default chat model under the token threshold, plus capability
# classifier, titling, and follow-up suggestions.
DEFAULT_FAST_MODEL = os.environ.get("ANTHROPIC_FAST_MODEL", "claude-haiku-4-5")

# Complex model: used once a turn's estimated input exceeds the fast-model cap.
DEFAULT_COMPLEX_MODEL = os.environ.get("ANTHROPIC_COMPLEX_MODEL", "claude-sonnet-4-6")

# Reasoning model: used for reform, distributional, and chart-heavy turns where
# the fast model tends to spend iterations guessing at the reform API shape.
DEFAULT_REASONING_MODEL = os.environ.get("ANTHROPIC_REASONING_MODEL", "claude-opus-4-5")

TITLE_MODEL = os.environ.get("ANTHROPIC_TITLE_MODEL", DEFAULT_FAST_MODEL)

# Follow-up suggestion chips run on the same fast model — cheap, latency-tolerant.
SUGGESTION_MODEL = os.environ.get("ANTHROPIC_SUGGESTION_MODEL", DEFAULT_FAST_MODEL)
SUGGESTION_TIMEOUT_SECS = float(os.environ.get("ANTHROPIC_SUGGESTION_TIMEOUT_SECS", "5"))

# Estimated-input-token ceiling above which a turn routes to the complex model.
FAST_MODEL_MAX_INPUT_TOKENS = int(
    os.environ.get("ANTHROPIC_FAST_MODEL_MAX_INPUT_TOKENS", "120000")
)
