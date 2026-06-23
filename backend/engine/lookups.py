"""Deterministic metadata lookup helpers for model-facing tools."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Dict, Iterable, List


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


def _limited(limit: int | None) -> int:
    if limit is None:
        return DEFAULT_LOOKUP_LIMIT
    return max(1, min(int(limit), MAX_LOOKUP_LIMIT))


def _token_f1(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens)
    if overlap == 0:
        return 0.0
    precision = overlap / len(candidate_tokens)
    recall = overlap / len(query_tokens)
    return 2 * precision * recall / (precision + recall)


@dataclass(frozen=True)
class _FieldScore:
    certainty: float
    matched_on: str
    match_reason: str
    exact: bool
    field_priority: int
    extra_tokens: int


@dataclass(frozen=True)
class _RankedCandidate:
    certainty: float
    candidate: Any
    matched_on: str
    match_reason: str
    exact: bool
    field_priority: int
    extra_tokens: int
    stable_key: str


def _score_field(
    query: str,
    candidate: str,
    *,
    matched_on: str,
    weight: float,
    field_priority: int,
) -> _FieldScore:
    query_norm = _normalise(query)
    candidate_norm = _normalise(candidate)
    query_tokens = _tokens(query)
    candidate_tokens = _tokens(candidate)
    extra_tokens = max(0, len(candidate_tokens - query_tokens))

    if query_norm and query_norm == candidate_norm:
        return _FieldScore(
            certainty=weight,
            matched_on=matched_on,
            match_reason=f"exact_{matched_on}",
            exact=True,
            field_priority=field_priority,
            extra_tokens=extra_tokens,
        )

    token_score = _token_f1(query_tokens, candidate_tokens)
    phrase_bonus = 0.08 if query_norm and query_norm in candidate_norm else 0.0
    sequence_score = 0.0
    if token_score > 0 and abs(len(candidate_tokens) - len(query_tokens)) <= 2:
        sequence_score = 0.15 * SequenceMatcher(None, query_norm, candidate_norm).ratio()
    certainty = min(weight, (token_score + phrase_bonus + sequence_score) * weight)
    reason = "token_f1"
    if phrase_bonus:
        reason = "phrase_match"
    return _FieldScore(
        certainty=certainty,
        matched_on=matched_on,
        match_reason=reason,
        exact=False,
        field_priority=field_priority,
        extra_tokens=extra_tokens,
    )


def _best_field_score(
    query: str,
    fields: Iterable[tuple[str, str, float, int]],
) -> _FieldScore:
    scores = [
        _score_field(
            query,
            value,
            matched_on=matched_on,
            weight=weight,
            field_priority=field_priority,
        )
        for matched_on, value, weight, field_priority in fields
        if value
    ]
    if not scores:
        return _FieldScore(0.0, "none", "no_candidate_text", False, 999, 999)
    return sorted(
        scores,
        key=lambda score: (
            -score.certainty,
            not score.exact,
            score.field_priority,
            score.extra_tokens,
            score.matched_on,
        ),
    )[0]


def _sort_ranked(ranked: Iterable[_RankedCandidate]) -> List[_RankedCandidate]:
    return sorted(
        ranked,
        key=lambda item: (
            -item.certainty,
            not item.exact,
            item.field_priority,
            item.extra_tokens,
            item.stable_key,
        ),
    )


def _needs_confirmation(ranked: List[_RankedCandidate]) -> bool:
    if not ranked:
        return False
    top = ranked[0]
    if top.certainty < MIN_MATCH_CERTAINTY:
        return True
    if len(ranked) == 1:
        return False
    runner_up = ranked[1]
    if top.exact and not runner_up.exact:
        return False
    return top.certainty - runner_up.certainty < MIN_MATCH_MARGIN


def _confirmation_reason(ranked: List[_RankedCandidate]) -> str:
    top = ranked[0]
    if top.certainty < MIN_MATCH_CERTAINTY:
        return LOW_CERTAINTY_CONFIRMATION_REASON
    runner_up = ranked[1]
    if top.exact and not runner_up.exact:
        return LOW_CERTAINTY_CONFIRMATION_REASON
    return LOW_MARGIN_CONFIRMATION_REASON


def _truncate_source(source: str) -> tuple[str, bool]:
    if len(source) <= MAX_FORMULA_SOURCE_CHARS:
        return source, False
    return source[:MAX_FORMULA_SOURCE_CHARS].rstrip() + "\n...", True


def _json_safe_default(value: Any) -> Any:
    if isinstance(value, type):
        return value.__name__
    if hasattr(value, "value"):
        return value.value
    if callable(value):
        return getattr(value, "__name__", str(value))
    return value


def _get_path(value: Any, path: str) -> Any:
    current = value
    for part in path.split("."):
        if isinstance(current, list):
            if not part.isdigit():
                return None
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current


def _flatten(value: Any, prefix: str = "") -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        rows: List[Dict[str, Any]] = []
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            rows.extend(_flatten(child, child_prefix))
        return rows
    if isinstance(value, list):
        rows = []
        for index, child in enumerate(value):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            rows.extend(_flatten(child, child_prefix))
        return rows
    return [{"path": prefix, "value": value}]


_PARAMETER_ALIASES = {
    "income_tax.personal_allowance": (
        "personal allowance",
        "tax free allowance",
        "income tax allowance",
    ),
    "income_tax.uk_brackets.0.rate": (
        "basic rate",
        "basic income tax rate",
    ),
    "income_tax.uk_brackets.1.threshold": (
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


def _parameter_context(path: str, value: Any, parameters: Dict[str, Any]) -> Dict[str, Any]:
    context: Dict[str, Any] = {}
    if path == "income_tax.uk_brackets.1.threshold" and isinstance(value, int | float):
        personal_allowance = _get_path(parameters, "income_tax.personal_allowance")
        context["threshold_basis"] = "taxable_income_after_personal_allowance"
        if isinstance(personal_allowance, int | float):
            context["personal_allowance"] = personal_allowance
            context["standard_gross_income_threshold"] = value + personal_allowance
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
    context = _parameter_context(candidate.path, candidate.value, parameters)
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
