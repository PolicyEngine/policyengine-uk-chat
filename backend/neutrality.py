"""Shared factual-neutrality vocabulary for prompts and evals."""

POLICY_VALUE_LABELS = (
    "good",
    "bad",
    "fair",
    "unfair",
    "regressive",
    "progressive",
    "generous",
    "punitive",
)

POLICY_VALUE_LABELS_TEXT = ", ".join(POLICY_VALUE_LABELS)

# Patterns are deliberately word-aware. The progressivity and regressivity
# families include the adverbial forms that can imply a tax-incidence label in
# distributional prose, without matching unrelated substrings.
FACTUAL_NEUTRALITY_PATTERNS = (
    ("good", r"\bgood\b"),
    ("bad", r"\bbad\b"),
    ("fair", r"\bfair\b"),
    ("unfair", r"\bunfair\b"),
    ("regressive", r"\bregressiv(?:e|ely|ity)\b"),
    ("progressive", r"\bprogressiv(?:e|ely|ity)\b"),
    ("generous", r"\bgener(?:ous(?:ly)?|osity)\b"),
    ("punitive", r"\bpunitive(?:ly)?\b"),
)
