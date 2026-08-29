"""Sampling temperatures for model calls."""

import os

# 0 = deterministic, which is what the compute loop, titling, capability
# classifier, and the evals all want.
DEFAULT_TEMPERATURE = float(os.environ.get("ANTHROPIC_TEMPERATURE", "0"))

# Follow-up suggestion chips deliberately sample with variety, so they get their
# own (higher) temperature rather than the deterministic default.
SUGGESTION_TEMPERATURE = float(os.environ.get("ANTHROPIC_SUGGESTION_TEMPERATURE", "1"))
