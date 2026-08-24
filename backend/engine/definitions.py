"""Authoritative variable definitions with formula and source metadata.

`engine.discovery` answers "does this variable exist and where does it live".
This module answers "what does this variable mean, how is it defined, and where
does that definition come from" for one variable at a time.

The compiled model exposes a machine-readable composition for a variable through
its `adds` and `subtracts` lists. That covers a minority of variables; the rest
carry a label and description but no machine-readable formula. Both cases are
reported explicitly so an answer cannot present a description as a formula, per
the source-divergence constraint in PolicyEngine/policyengine-uk-chat#140.

Matching is deterministic. The same query against the same model version always
resolves to the same variable, the same ranked options, or the same error.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import get_close_matches
from typing import Any

from engine.discovery import _default_output_entities
from engine.py_runtime import uk_model_version
from engine.serialization import json_safe

# Ranking tiers, best first. A query resolves to a single variable only when
# exactly one variable occupies the best non-empty tier.
MATCH_EXACT_NAME = "exact_name"
MATCH_EXACT_LABEL = "exact_label"
MATCH_PHRASE = "phrase"
MATCH_ALL_TOKENS = "all_tokens"
MATCH_DESCRIPTION = "description"

MATCH_TIERS = (
    MATCH_EXACT_NAME,
    MATCH_EXACT_LABEL,
    MATCH_PHRASE,
    MATCH_ALL_TOKENS,
    MATCH_DESCRIPTION,
)

MAX_OPTIONS = 10
DEFAULT_OPTIONS = 5
MAX_SUGGESTIONS = 5

NO_FORMULA_NOTE = (
    "The compiled model exposes no machine-readable formula for this variable. "
    "The label and description are documentation, not a formula. Describe the "
    "calculation only from tool output or parameter lookups, and say the exact "
    "formula is not available rather than reconstructing it."
)
COMPOSITION_NOTE = (
    "This variable is defined by the model as the sum of its `adds` variables "
    "minus its `subtracts` variables. Each referenced name is itself a model "
    "variable that can be looked up."
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Dropped only when ranking suggestions for a query that matched no tier, so a
# prose question is scored on its content words. Tier matching is unaffected.
_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "by", "do", "does", "for", "how", "in", "is",
        "much", "of", "or", "paid", "pay", "the", "this", "to", "what", "which",
    }
)


@dataclass(frozen=True)
class VariableMatch:
    """One candidate variable and the tier that produced it."""

    name: str
    tier: str

    @property
    def rank(self) -> int:
        return MATCH_TIERS.index(self.tier)


def _normalise(value: str | None) -> str:
    """Lower-case, replace separators with spaces, and collapse whitespace."""

    return " ".join(_TOKEN_RE.findall((value or "").lower()))


def _tokens(value: str | None) -> list[str]:
    return _TOKEN_RE.findall((value or "").lower())


def _content_words(value: str | None) -> str:
    """The query with filler words removed, as a normalised string."""

    return " ".join(token for token in _tokens(value) if token not in _STOP_WORDS)


def _tier_for(
    query_norm: str,
    query_tokens: list[str],
    name: str,
    label: str | None,
    description: str | None,
) -> str | None:
    """Return the best tier this variable qualifies for, or None."""

    name_norm = _normalise(name)
    label_norm = _normalise(label)
    if query_norm == name_norm:
        return MATCH_EXACT_NAME
    if label_norm and query_norm == label_norm:
        return MATCH_EXACT_LABEL
    identity = f"{name_norm} {label_norm}".strip()
    if query_norm and query_norm in identity:
        return MATCH_PHRASE
    identity_tokens = set(_tokens(identity))
    if query_tokens and identity_tokens.issuperset(query_tokens):
        return MATCH_ALL_TOKENS
    description_tokens = set(_tokens(description))
    if query_tokens and (identity_tokens | description_tokens).issuperset(query_tokens):
        return MATCH_DESCRIPTION
    return None


def rank_matches(query: str, model: Any) -> list[VariableMatch]:
    """Rank every qualifying variable deterministically, best first.

    Ties within a tier are broken by shorter canonical name then alphabetical
    order, so the ordering never depends on registry iteration order.
    """

    query_norm = _normalise(query)
    query_tokens = _tokens(query)
    if not query_norm:
        return []
    matches: list[VariableMatch] = []
    for name, variable in model.variables_by_name.items():
        tier = _tier_for(
            query_norm,
            query_tokens,
            name,
            getattr(variable, "label", None),
            getattr(variable, "description", None),
        )
        if tier is not None:
            matches.append(VariableMatch(name=name, tier=tier))
    matches.sort(key=lambda match: (match.rank, len(match.name), match.name))
    return matches


def _formula(variable: Any) -> dict[str, Any]:
    adds = list(getattr(variable, "adds", None) or [])
    subtracts = list(getattr(variable, "subtracts", None) or [])
    if not adds and not subtracts:
        return {
            "available": False,
            "kind": None,
            "adds": [],
            "subtracts": [],
            "statement": None,
            "note": NO_FORMULA_NOTE,
        }
    name = getattr(variable, "name", None) or ""
    terms = " + ".join(adds) if adds else "0"
    for subtracted in subtracts:
        terms += f" - {subtracted}"
    return {
        "available": True,
        "kind": "composition",
        "adds": adds,
        "subtracts": subtracts,
        "statement": f"{name} = {terms}",
        "note": COMPOSITION_NOTE,
    }


def _source(model: Any) -> dict[str, Any]:
    return {
        "model": getattr(model, "id", None),
        "package": getattr(model, "package_name", None),
        "country": getattr(model, "country_code", None),
        "note": (
            "Definitions come from the same compiled model version that runs "
            "the simulations, so they cannot diverge from the calculations."
        ),
    }


def _definition(name: str, variable: Any, model: Any) -> dict[str, Any]:
    return {
        "name": name,
        "label": getattr(variable, "label", None),
        "entity": getattr(variable, "entity", None),
        "description": getattr(variable, "description", None),
        "definition_period": getattr(variable, "definition_period", None),
        "value_type": getattr(getattr(variable, "value_type", None), "__name__", None)
        or str(getattr(variable, "value_type", "")),
        "default_value": json_safe(getattr(variable, "default_value", None)),
        "possible_values": json_safe(getattr(variable, "possible_values", None)),
        "is_default_society_output": bool(_default_output_entities(model, name)),
        "default_output_entities": _default_output_entities(model, name),
        "formula": _formula(variable),
    }


def _option(name: str, tier: str, model: Any) -> dict[str, Any]:
    variable = model.variables_by_name[name]
    return {
        "name": name,
        "label": getattr(variable, "label", None),
        "entity": getattr(variable, "entity", None),
        "matched_on": tier,
    }


def _score(
    hits: int,
    query_size: int,
    haystack: set[str],
    name: str,
) -> tuple[float, float, int, str]:
    """Sort key for one suggestion: most query tokens covered, least padding.

    Recall alone ties `universal_credit` with `is_uc_eligible`, both of which
    contain "universal" and "credit". Precision — the share of the variable's
    own words the query accounts for — breaks that tie towards the variable the
    query actually names. Sorting ascending puts the best candidate first.
    """

    return (
        -hits / query_size,
        -hits / len(haystack) if haystack else 0.0,
        len(name),
        name,
    )


def _suggestions(query: str, model: Any) -> list[str]:
    """Close variable names for a query that matched no tier.

    Candidates are drawn from both canonical names and labels, so a query
    phrased in prose can still surface the variable it was reaching for. The
    result is ordered by name for a stable, reproducible response.
    """

    query_tokens = [token for token in _tokens(query) if token not in _STOP_WORDS]
    if not query_tokens:
        return []

    scored: list[tuple[float, float, int, str]] = []
    vocabulary: set[str] = set()
    for name, variable in model.variables_by_name.items():
        haystack = set(_tokens(name)) | set(_tokens(getattr(variable, "label", None)))
        vocabulary |= haystack
        hits = sum(1 for token in query_tokens if token in haystack)
        if hits:
            scored.append(_score(hits, len(query_tokens), haystack, name))
    if scored:
        scored.sort()
        return [name for *_, name in scored[:MAX_SUGGESTIONS]]

    # No token matched any name or label: the query is probably misspelled, so
    # repair each token against the vocabulary and score again.
    repaired = [
        match
        for token in query_tokens
        for match in get_close_matches(token, sorted(vocabulary), n=1, cutoff=0.75)
    ]
    if not repaired:
        return []
    for name, variable in model.variables_by_name.items():
        haystack = set(_tokens(name)) | set(_tokens(getattr(variable, "label", None)))
        hits = sum(1 for token in repaired if token in haystack)
        if hits:
            scored.append(_score(hits, len(repaired), haystack, name))
    scored.sort()
    return [name for *_, name in scored[:MAX_SUGGESTIONS]]


def get_variable_definition(
    query: str,
    limit: int = DEFAULT_OPTIONS,
) -> dict[str, Any]:
    """Resolve one query to an authoritative variable definition.

    Four deterministic outcomes:

    - `success` when the query is an exact variable name, or when exactly one
      variable occupies the best matching tier.
    - `needs_confirmation` when several variables tie at the best tier; ranked
      options are returned and no definition is chosen.
    - `error` with close-match suggestions when nothing matches.
    - `error` when the query is empty.
    """

    bounded_limit = max(1, min(int(limit), MAX_OPTIONS))
    if not _normalise(query):
        return {
            "status": "error",
            "error": "query must contain at least one letter or digit",
            "query": query,
        }

    model = uk_model_version()
    matches = rank_matches(query, model)
    if not matches:
        # A prose question carries filler that no variable name or label
        # contains. Retry on its content words before giving up, so
        # "what does universal credit include" still resolves.
        content_query = _content_words(query)
        if content_query and content_query != _normalise(query):
            matches = rank_matches(content_query, model)
    if not matches:
        return {
            "status": "error",
            "error": f"No variable matches: {query}",
            "query": query,
            "suggestions": _suggestions(query, model),
            "hint": (
                "Try a shorter concept name rather than a full question, or "
                "call search_variables to browse candidates."
            ),
            "source": _source(model),
        }

    best_rank = matches[0].rank
    tied = [match for match in matches if match.rank == best_rank]
    if len(tied) > 1:
        return {
            "status": "needs_confirmation",
            "query": query,
            "matched_on": matches[0].tier,
            "reason": (
                f"{len(tied)} variables match this description equally well. "
                "Ask the user which one they mean, or call this tool again "
                "with an exact variable name."
            ),
            "option_count": len(tied),
            "options": [_option(match.name, match.tier, model) for match in tied[:bounded_limit]],
            "source": _source(model),
        }

    match = tied[0]
    return {
        "status": "success",
        "query": query,
        "matched_on": match.tier,
        "variable": _definition(match.name, model.variables_by_name[match.name], model),
        "source": _source(model),
    }
