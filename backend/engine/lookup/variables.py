"""PolicyEngine UK variable metadata and formula lookup."""

from __future__ import annotations

import inspect
from functools import lru_cache
from typing import Any, Dict, Iterable, List

from engine.lookup.common import _json_safe_default, _limited
from engine.lookup.config import (
    MAX_FORMULAS_PER_VARIABLE,
    MAX_FORMULA_SOURCE_CHARS,
    MAX_LOOKUP_LIMIT,
    MIN_PLAUSIBLE_MATCH_CERTAINTY,
)
from engine.lookup.scoring import (
    _RankedCandidate,
    _best_field_score,
    _confirmation_reason,
    _needs_confirmation,
    _sort_ranked,
)


def _truncate_source(source: str) -> tuple[str, bool]:
    if len(source) <= MAX_FORMULA_SOURCE_CHARS:
        return source, False
    return source[:MAX_FORMULA_SOURCE_CHARS].rstrip() + "\n...", True


@lru_cache(maxsize=1)
def _variable_registry() -> Dict[str, Any]:
    from policyengine_uk import CountryTaxBenefitSystem

    return CountryTaxBenefitSystem().variables


def _formula_records(variable: Any) -> List[Dict[str, Any]]:
    formulas = getattr(variable, "formulas", None) or {}
    records = []
    for effective_date, formula in list(formulas.items())[:MAX_FORMULAS_PER_VARIABLE]:
        try:
            source = inspect.getsource(formula)
            source, truncated = _truncate_source(source)
            records.append(
                {
                    "effective_date": str(effective_date),
                    "source": source,
                    "truncated": truncated,
                }
            )
        except (OSError, TypeError) as exc:
            records.append(
                {
                    "effective_date": str(effective_date),
                    "error": f"Formula source unavailable: {exc}",
                }
            )
    return records


def _rank_variables(query: str, variables: Iterable[Any]) -> List[_RankedCandidate]:
    ranked = []
    for variable in variables:
        name = getattr(variable, "name", "") or ""
        fields = [
            ("name", name, 1.00, 0),
            ("label", getattr(variable, "label", "") or "", 0.90, 1),
            ("documentation", getattr(variable, "documentation", "") or "", 0.55, 2),
        ]
        score = _best_field_score(query, fields)
        ranked.append(
            _RankedCandidate(
                certainty=score.certainty,
                candidate=variable,
                matched_on=score.matched_on,
                match_reason=score.match_reason,
                exact=score.exact,
                field_priority=score.field_priority,
                extra_tokens=score.extra_tokens,
                stable_key=name,
            )
        )
    return _sort_ranked(ranked)


def _variable_record(
    ranked: _RankedCandidate,
    *,
    include_formula: bool,
) -> Dict[str, Any]:
    variable = ranked.candidate
    formulas = _formula_records(variable) if include_formula else []
    entity = getattr(variable, "entity", None)
    category = getattr(variable, "category", None)
    has_formula = bool(getattr(variable, "formulas", None))
    if not include_formula:
        formula_status = "not_requested"
    elif not has_formula:
        formula_status = "input_variable"
    elif any("source" in formula for formula in formulas):
        formula_status = "available"
    else:
        formula_status = "source_unavailable"

    record = {
        "name": getattr(variable, "name", None),
        "label": getattr(variable, "label", None),
        "documentation": getattr(variable, "documentation", None),
        "entity": getattr(entity, "key", str(entity) if entity is not None else None),
        "definition_period": str(getattr(variable, "definition_period", None)),
        "value_type": _json_safe_default(getattr(variable, "value_type", None)),
        "default_value": getattr(variable, "default_value", None),
        "unit": getattr(variable, "unit", None),
        "category": _json_safe_default(category),
        "has_formula": has_formula,
        "formula_status": formula_status,
        "formulas": formulas,
        "score": round(ranked.certainty, 4),
        "match_certainty": round(ranked.certainty, 4),
        "matched_on": ranked.matched_on,
        "match_reason": ranked.match_reason,
    }
    return record


def _variable_suggestion_item(ranked: _RankedCandidate) -> Dict[str, Any]:
    variable = ranked.candidate
    return {
        "name": getattr(variable, "name", None),
        "label": getattr(variable, "label", None),
        "match_certainty": round(ranked.certainty, 4),
    }


def lookup_variable_metadata(
    *,
    query: str,
    include_formula: bool = True,
    limit: int | None = None,
) -> Dict[str, Any]:
    """Look up PolicyEngine UK variable metadata and formula source."""
    query = (query or "").strip()
    if not query:
        return {"status": "error", "needs_confirmation": False, "error": "query is required"}

    variables = _variable_registry()
    limit = _limited(limit)
    ranked = _rank_variables(query, variables.values())
    plausible = [
        item
        for item in ranked
        if item.certainty >= MIN_PLAUSIBLE_MATCH_CERTAINTY
    ]

    if not plausible:
        suggestions = [
            _variable_suggestion_item(ranked_candidate)
            for ranked_candidate in ranked[:limit]
        ]
        return {
            "status": "error",
            "needs_confirmation": False,
            "error": f"No variable matched query: {query}",
            "query": query,
            "suggestions": suggestions,
        }

    if _needs_confirmation(plausible):
        matches = [
            _variable_record(item, include_formula=include_formula)
            for item in plausible[:MAX_LOOKUP_LIMIT]
        ]
        return {
            "status": "needs_confirmation",
            "needs_confirmation": True,
            "source": "policyengine_uk.CountryTaxBenefitSystem.variables",
            "query": query,
            "match_certainty": matches[0]["match_certainty"],
            "confirmation_reason": _confirmation_reason(plausible),
            "message": "Multiple variable matches are plausible. Ask the user which option they mean before answering.",
            "options": matches,
        }

    matches = [
        _variable_record(item, include_formula=include_formula)
        for item in plausible[:limit]
    ]
    return {
        "status": "success",
        "needs_confirmation": False,
        "source": "policyengine_uk.CountryTaxBenefitSystem.variables",
        "query": query,
        "match_certainty": matches[0]["match_certainty"],
        "primary_match": matches[0],
        "matches": matches,
    }
