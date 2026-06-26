"""Text normalization helpers for deterministic lookup matching."""

import re


_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "available",
    "calculate",
    "calculated",
    "definition",
    "does",
    "for",
    "how",
    "is",
    "model",
    "not",
    "of",
    "policyengine",
    "show",
    "the",
    "to",
    "uk",
    "variable",
    "what",
}


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.lower()) if token not in _STOPWORDS}


def _normalise(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.replace("_", " ").replace(".", " ").lower()))


def _humanize(value: str) -> str:
    value = value.replace("_", " ")
    value = re.sub(r"(?<!^)(?=[A-Z])", " ", value)
    return value.strip().lower()
