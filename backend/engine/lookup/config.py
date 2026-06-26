"""Shared lookup limits and string-match thresholds."""

MAX_LOOKUP_LIMIT = 10
DEFAULT_LOOKUP_LIMIT = 5
MAX_FORMULAS_PER_VARIABLE = 4
MAX_FORMULA_SOURCE_CHARS = 4000

# Match certainty is a deterministic string parsing score for how well the
# user's query matched parameter/variable names, labels, aliases, or docs. It
# is not factual confidence in the underlying policy value or formula.
MIN_MATCH_CERTAINTY = 0.72
MIN_MATCH_MARGIN = 0.14
MIN_PLAUSIBLE_MATCH_CERTAINTY = 0.35

LOW_CERTAINTY_CONFIRMATION_REASON = "low_string_match_certainty"
LOW_MARGIN_CONFIRMATION_REASON = "close_string_match_certainty_margin"
