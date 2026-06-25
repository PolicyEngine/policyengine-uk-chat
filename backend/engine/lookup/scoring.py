"""Deterministic string scoring and confirmation logic for lookup tools."""

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, List

from engine.lookup.config import (
    LOW_CERTAINTY_CONFIRMATION_REASON,
    LOW_MARGIN_CONFIRMATION_REASON,
    MIN_MATCH_CERTAINTY,
    MIN_MATCH_MARGIN,
)
from engine.lookup.text import _normalise, _tokens


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


def _token_f1(query_tokens: set[str], candidate_tokens: set[str]) -> float:
    if not query_tokens or not candidate_tokens:
        return 0.0
    overlap = len(query_tokens & candidate_tokens)
    if overlap == 0:
        return 0.0
    precision = overlap / len(candidate_tokens)
    recall = overlap / len(query_tokens)
    return 2 * precision * recall / (precision + recall)


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
    if not ranked:
        return LOW_CERTAINTY_CONFIRMATION_REASON
    top = ranked[0]
    if top.certainty < MIN_MATCH_CERTAINTY:
        return LOW_CERTAINTY_CONFIRMATION_REASON
    if len(ranked) == 1:
        return LOW_CERTAINTY_CONFIRMATION_REASON

    # Callers should ask for a reason only when _needs_confirmation() is true.
    # At this point the remaining confirmation case is a close margin between
    # the top candidate and runner-up.
    runner_up = ranked[1]
    if top.exact and not runner_up.exact:
        return LOW_MARGIN_CONFIRMATION_REASON
    return LOW_MARGIN_CONFIRMATION_REASON
