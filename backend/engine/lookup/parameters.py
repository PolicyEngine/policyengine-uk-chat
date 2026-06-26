"""Baseline parameter metadata lookup."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from engine.lookup.common import _flatten, _get_path, _limited
from engine.lookup.config import MAX_LOOKUP_LIMIT, MIN_PLAUSIBLE_MATCH_CERTAINTY
from engine.lookup.scoring import (
    _RankedCandidate,
    _best_field_score,
    _confirmation_reason,
    _needs_confirmation,
    _sort_ranked,
)
from engine.lookup.text import _humanize


BASIC_RATE_THRESHOLD_PATH = "income_tax.uk_brackets.1.threshold"
PERSONAL_ALLOWANCE_PATH = "income_tax.personal_allowance"

_PARAMETER_ALIASES = {
    PERSONAL_ALLOWANCE_PATH: (
        "personal allowance",
        "tax free allowance",
        "income tax allowance",
    ),
    "income_tax.uk_brackets.0.rate": (
        "basic rate",
        "basic income tax rate",
    ),
    BASIC_RATE_THRESHOLD_PATH: (
        "basic rate threshold",
        "basic rate limit",
        "basic rate upper limit",
        "higher rate threshold",
        "higher rate starts",
    ),
    "income_tax.uk_brackets.1.rate": (
        "higher rate",
        "higher income tax rate",
    ),
    "income_tax.uk_brackets.2.threshold": (
        "additional rate threshold",
        "additional rate starts",
    ),
    "income_tax.uk_brackets.2.rate": (
        "additional rate",
        "additional income tax rate",
    ),
    "vat.standard_rate": (
        "standard rate of VAT",
        "VAT standard rate",
        "standard VAT rate",
    ),
}


@dataclass(frozen=True)
class _ParameterCandidate:
    path: str
    value: Any
    label: str
    program: str
    aliases: tuple[str, ...]


def _parameter_candidates(parameters: Dict[str, Any]) -> List[_ParameterCandidate]:
    candidates = []
    for row in _flatten(parameters):
        path = row["path"]
        if not path:
            continue
        candidates.append(
            _ParameterCandidate(
                path=path,
                value=row["value"],
                label=_humanize(path.replace(".", " ")),
                program=path.split(".", 1)[0],
                aliases=tuple(_PARAMETER_ALIASES.get(path, ())),
            )
        )
    return candidates


def parameter_alias_paths() -> tuple[str, ...]:
    """Return compiled parameter paths that have hand-authored aliases."""

    return tuple(_PARAMETER_ALIASES)


def missing_parameter_alias_paths(parameters: Dict[str, Any]) -> tuple[str, ...]:
    """Return alias paths that no longer resolve in compiled baseline params."""

    return tuple(
        path
        for path in parameter_alias_paths()
        if _get_path(parameters, path) is None
    )


def _parameter_context(candidate: _ParameterCandidate, parameters: Dict[str, Any]) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    if (
        candidate.path == BASIC_RATE_THRESHOLD_PATH
        and candidate.program == "income_tax"
        and "basic rate threshold" in candidate.aliases
        and isinstance(candidate.value, int | float)
        and _get_path(parameters, BASIC_RATE_THRESHOLD_PATH) == candidate.value
    ):
        personal_allowance = _get_path(parameters, PERSONAL_ALLOWANCE_PATH)
        if not isinstance(personal_allowance, int | float):
            return context
        context["threshold_basis"] = "taxable_income_after_personal_allowance"
        context["personal_allowance"] = personal_allowance
        context["standard_gross_income_threshold"] = candidate.value + personal_allowance
        context["standard_gross_income_threshold_note"] = (
            "For a taxpayer receiving the full personal allowance, this is personal allowance plus the taxable-income threshold."
        )
    return context


def _rank_parameters(query: str, candidates: Iterable[_ParameterCandidate]) -> List[_RankedCandidate]:
    ranked = []
    for candidate in candidates:
        fields: list[tuple[str, str, float, int]] = [
            ("path", candidate.path, 1.00, 0),
            ("label", candidate.label, 0.85, 2),
        ]
        fields.extend(("alias", alias, 0.98, 1) for alias in candidate.aliases)
        score = _best_field_score(query, fields)
        ranked.append(
            _RankedCandidate(
                certainty=score.certainty,
                candidate=candidate,
                matched_on=score.matched_on,
                match_reason=score.match_reason,
                exact=score.exact,
                field_priority=score.field_priority,
                extra_tokens=score.extra_tokens,
                stable_key=candidate.path,
            )
        )
    return _sort_ranked(ranked)


def _parameter_match_item(
    ranked: _RankedCandidate,
    parameters: Dict[str, Any],
) -> Dict[str, Any]:
    candidate = ranked.candidate
    item = {
        "path": candidate.path,
        "value": candidate.value,
        "label": candidate.label,
        "program": candidate.program,
        "score": round(ranked.certainty, 4),
        "match_certainty": round(ranked.certainty, 4),
        "matched_on": ranked.matched_on,
        "match_reason": ranked.match_reason,
    }
    aliases = list(candidate.aliases)
    if aliases:
        item["aliases"] = aliases
    context = _parameter_context(candidate, parameters)
    if context:
        item["context"] = context
    return item


def _parameter_suggestion_item(ranked: _RankedCandidate) -> Dict[str, Any]:
    candidate = ranked.candidate
    return {
        "path": candidate.path,
        "label": candidate.label,
        "match_certainty": round(ranked.certainty, 4),
    }


def lookup_parameter_metadata(
    *,
    parameters: Dict[str, Any],
    query: str,
    year: int,
    limit: int | None = None,
) -> Dict[str, Any]:
    """Look up flattened baseline parameter values by path or natural query."""
    query = (query or "").strip()
    if not query:
        return {"status": "error", "needs_confirmation": False, "error": "query is required"}

    candidates = _parameter_candidates(parameters)
    limit = _limited(limit)
    ranked = _rank_parameters(query, candidates)
    plausible = [
        item
        for item in ranked
        if item.certainty >= MIN_PLAUSIBLE_MATCH_CERTAINTY
    ]

    if not plausible:
        suggestions = [
            _parameter_suggestion_item(ranked_candidate)
            for ranked_candidate in ranked[:limit]
        ]
        return {
            "status": "error",
            "needs_confirmation": False,
            "error": f"No parameter matched query: {query}",
            "year": year,
            "query": query,
            "suggestions": suggestions,
        }

    if _needs_confirmation(plausible):
        matches = [
            _parameter_match_item(item, parameters)
            for item in plausible[:MAX_LOOKUP_LIMIT]
        ]
        return {
            "status": "needs_confirmation",
            "needs_confirmation": True,
            "source": "policyengine_uk_compiled.Simulation.get_baseline_params",
            "year": year,
            "query": query,
            "match_certainty": matches[0]["match_certainty"],
            "confirmation_reason": _confirmation_reason(plausible),
            "message": "Multiple parameter matches are plausible. Ask the user which option they mean before answering.",
            "options": matches,
        }

    matches = [_parameter_match_item(item, parameters) for item in plausible[:limit]]
    return {
        "status": "success",
        "needs_confirmation": False,
        "source": "policyengine_uk_compiled.Simulation.get_baseline_params",
        "year": year,
        "query": query,
        "match_certainty": matches[0]["match_certainty"],
        "primary_match": matches[0],
        "matches": matches,
    }
