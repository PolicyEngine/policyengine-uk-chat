"""Deterministic policyengine.py catalogue evidence for opening-turn routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Literal, Sequence

from engine.discovery import search_variables
from engine.py_runtime import uk_model_version
from engine.reforms import search_reform_targets

CatalogueKind = Literal["reform_target", "variable"]
CatalogueMatchType = Literal[
    "exact_identifier",
    "exact_alias",
    "exact_label",
    "strong_phrase",
    "fuzzy_suggestion",
]

MAX_CATALOGUE_QUERIES = 4
MATCH_LIMIT = 5
CANDIDATE_LIMIT = 100


@dataclass(frozen=True)
class CatalogueQuery:
    """One concise model-concept lookup requested by the gateway classifier."""

    kind: CatalogueKind
    query: str
    evidence: str | None = None


@dataclass(frozen=True)
class CatalogueMatch:
    """One current policyengine.py parameter or variable match."""

    kind: CatalogueKind
    query: str
    identifier: str
    label: str
    match_type: CatalogueMatchType = "strong_phrase"
    score: float = 0.9

    @property
    def authoritative(self) -> bool:
        return self.match_type != "fuzzy_suggestion"


@dataclass(frozen=True)
class CatalogueEvidence:
    """Resolved opening-turn catalogue evidence.

    ``available`` is false only when the current model catalogue could not be
    loaded. That is distinct from a successful lookup with no matches, which
    is represented by ``unresolved_queries``.
    """

    available: bool
    matches: tuple[CatalogueMatch, ...] = ()
    unresolved_queries: tuple[CatalogueQuery, ...] = ()

    @property
    def authoritative_matches(self) -> tuple[CatalogueMatch, ...]:
        return tuple(match for match in self.matches if match.authoritative)

    @property
    def suggestions(self) -> tuple[CatalogueMatch, ...]:
        return tuple(match for match in self.matches if not match.authoritative)


def _normalise_match_text(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def _classify_match(
    query: str,
    *,
    identifier: str,
    label: str,
    aliases: Sequence[str] = (),
    description: str | None = None,
) -> tuple[CatalogueMatchType, float]:
    """Classify deterministic lookup output by the evidence it actually gives.

    Fuzzy similarity remains useful for suggestions, but only exact and
    sufficiently specific phrase matches can authorize catalogue recovery.
    """

    query_text = _normalise_match_text(query)
    identifier_text = _normalise_match_text(identifier)
    label_text = _normalise_match_text(label)
    alias_texts = tuple(_normalise_match_text(alias) for alias in aliases)

    if query_text and query_text == identifier_text:
        return "exact_identifier", 1.0
    if query_text and query_text in alias_texts:
        return "exact_alias", 1.0
    if query_text and query_text == label_text:
        return "exact_label", 1.0

    query_tokens = set(query_text.split())
    strong_fields = (identifier_text, label_text, *alias_texts)
    if len(query_tokens) >= 2 and any(
        query_tokens.issubset(set(field.split())) for field in strong_fields if field
    ):
        return "strong_phrase", 0.9

    comparison_fields = (*strong_fields, _normalise_match_text(description))
    score = max(
        (
            SequenceMatcher(None, query_text, field).ratio()
            for field in comparison_fields
            if query_text and field
        ),
        default=0.0,
    )
    return "fuzzy_suggestion", round(score, 4)


def _catalogue_match(
    *,
    kind: CatalogueKind,
    query: str,
    identifier: str,
    label: str,
    aliases: Sequence[str] = (),
    description: str | None = None,
) -> CatalogueMatch:
    match_type, score = _classify_match(
        query,
        identifier=identifier,
        label=label,
        aliases=aliases,
        description=description,
    )
    return CatalogueMatch(
        kind=kind,
        query=query,
        identifier=identifier,
        label=label,
        match_type=match_type,
        score=score,
    )


def _normalise_queries(queries: Sequence[CatalogueQuery]) -> tuple[CatalogueQuery, ...]:
    """Bound and de-duplicate untrusted classifier output before model lookup."""

    normalised: list[CatalogueQuery] = []
    seen: set[tuple[str, str]] = set()
    for item in queries:
        if item.kind not in ("reform_target", "variable"):
            continue
        query = item.query.strip()
        if not query:
            continue
        key = (item.kind, query.casefold())
        if key in seen:
            continue
        seen.add(key)
        normalised.append(CatalogueQuery(item.kind, query, item.evidence))
        if len(normalised) == MAX_CATALOGUE_QUERIES:
            break
    return tuple(normalised)


def resolve_catalogue_queries(queries: Sequence[CatalogueQuery]) -> CatalogueEvidence:
    """Resolve gateway terms against the current policyengine.py model catalogue.

    This is intentionally an internal server lookup, not a model-facing tool.
    It confirms that a named concept exists without deciding which candidate the
    user intended or whether the request has enough information to execute.
    """

    queries = _normalise_queries(queries)
    if not queries:
        return CatalogueEvidence(available=True)

    try:
        # Check availability once before calling helpers that search the same
        # cached model. An unavailable catalogue must fail open at the gateway.
        uk_model_version()
        matches: list[CatalogueMatch] = []
        unresolved: list[CatalogueQuery] = []
        for item in queries:
            if item.kind == "reform_target":
                rows = search_reform_targets(item.query, limit=CANDIDATE_LIMIT)
                item_matches = [
                    _catalogue_match(
                        kind=item.kind,
                        query=item.query,
                        identifier=row["path"],
                        label=row.get("label") or row["path"],
                        aliases=row.get("aliases") or (),
                        description=row.get("description"),
                    )
                    for row in rows
                ]
            else:
                response = search_variables(item.query, limit=CANDIDATE_LIMIT)
                item_matches = [
                    _catalogue_match(
                        kind=item.kind,
                        query=item.query,
                        identifier=row["name"],
                        label=row.get("label") or row["name"],
                        description=row.get("description"),
                    )
                    for row in response["variables"]
                ]
            item_matches.sort(
                key=lambda match: (
                    not match.authoritative,
                    -match.score,
                    match.label.casefold(),
                )
            )
            item_matches = item_matches[:MATCH_LIMIT]
            matches.extend(item_matches)
            if not any(match.authoritative for match in item_matches):
                unresolved.append(item)
    except Exception:  # noqa: BLE001 - catalogue metadata must fail open
        return CatalogueEvidence(available=False)

    return CatalogueEvidence(
        available=True,
        matches=tuple(matches),
        unresolved_queries=tuple(unresolved),
    )
