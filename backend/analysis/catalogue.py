"""Authoritative PolicyEngine catalogue resolution without chat-stage assumptions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from importlib.metadata import PackageNotFoundError, version
from typing import Callable, Literal, Sequence

from analysis.common import AnalysisError, AnalysisErrorCode


CatalogueKind = Literal["reform_target", "variable"]


@dataclass(frozen=True)
class CatalogueCandidate:
    kind: CatalogueKind
    query: str
    identifier: str
    label: str
    match_type: str
    score: float

    @property
    def authoritative(self) -> bool:
        return self.match_type != "fuzzy_suggestion"


@dataclass(frozen=True)
class CatalogueResolution:
    available: bool
    query: str
    candidates: tuple[CatalogueCandidate, ...] = ()

    @property
    def authoritative(self) -> tuple[CatalogueCandidate, ...]:
        return tuple(item for item in self.candidates if item.authoritative)


def current_catalogue_version() -> str:
    try:
        return version("policyengine-uk")
    except PackageNotFoundError:
        return "unknown"


def _normalise(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _classify(
    query: str,
    *,
    identifier: str,
    label: str,
    aliases: Sequence[str] = (),
    description: str | None = None,
) -> tuple[str, float]:
    query_text = _normalise(query)
    identifier_text = _normalise(identifier)
    label_text = _normalise(label)
    alias_texts = tuple(_normalise(alias) for alias in aliases)
    if query_text == identifier_text:
        return "exact_identifier", 1.0
    if query_text in alias_texts:
        return "exact_alias", 1.0
    if query_text == label_text:
        return "exact_label", 1.0
    tokens = set(query_text.split())
    if len(tokens) >= 2 and any(
        tokens.issubset(set(field.split()))
        for field in (identifier_text, label_text, *alias_texts)
        if field
    ):
        return "strong_phrase", 0.9
    score = max(
        (
            SequenceMatcher(None, query_text, field).ratio()
            for field in (
                identifier_text,
                label_text,
                *alias_texts,
                _normalise(description),
            )
            if query_text and field
        ),
        default=0.0,
    )
    return "fuzzy_suggestion", round(score, 4)


def resolve_catalogue_term(
    kind: CatalogueKind,
    query: str,
    *,
    reform_search: Callable[[str, int], list[dict]] | None = None,
    variable_search: Callable[[str, int], dict] | None = None,
    limit: int = 20,
) -> CatalogueResolution:
    """Resolve one bounded term and distinguish absence from lookup failure."""

    query = query.strip()
    if not query:
        return CatalogueResolution(available=True, query=query)
    try:
        if kind == "reform_target":
            if reform_search is None:
                from engine.reforms import search_reform_targets

                reform_search = search_reform_targets
            rows = reform_search(query, limit)
            candidates = [
                (
                    row["path"],
                    row.get("label") or row["path"],
                    row.get("aliases") or (),
                    row.get("description"),
                )
                for row in rows
            ]
        else:
            if variable_search is None:
                from engine.discovery import search_variables

                variable_search = search_variables
            response = variable_search(query, limit)
            candidates = [
                (
                    row["name"],
                    row.get("label") or row["name"],
                    (),
                    row.get("description"),
                )
                for row in response.get("variables", ())
            ]
    except Exception:
        return CatalogueResolution(available=False, query=query)

    resolved = []
    for identifier, label, aliases, description in candidates:
        match_type, score = _classify(
            query,
            identifier=identifier,
            label=label,
            aliases=aliases,
            description=description,
        )
        resolved.append(
            CatalogueCandidate(
                kind=kind,
                query=query,
                identifier=identifier,
                label=label,
                match_type=match_type,
                score=score,
            )
        )
    resolved.sort(
        key=lambda item: (
            not item.authoritative,
            -item.score,
            item.label.casefold(),
        )
    )
    return CatalogueResolution(
        available=True,
        query=query,
        candidates=tuple(resolved[:5]),
    )


def require_catalogue(resolution: CatalogueResolution) -> CatalogueResolution:
    if not resolution.available:
        raise AnalysisError(
            AnalysisErrorCode.CATALOGUE_UNAVAILABLE,
            "the PolicyEngine catalogue could not be loaded",
            retryable=True,
        )
    return resolution
