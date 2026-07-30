"""Deterministic policyengine.py catalogue evidence for opening-turn routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from engine.discovery import search_variables
from engine.py_runtime import uk_model_version
from engine.reforms import search_reform_targets

CatalogueKind = Literal["reform_target", "variable"]

MAX_CATALOGUE_QUERIES = 4
MATCH_LIMIT = 5


@dataclass(frozen=True)
class CatalogueQuery:
    """One concise model-concept lookup requested by the gateway classifier."""

    kind: CatalogueKind
    query: str


@dataclass(frozen=True)
class CatalogueMatch:
    """One current policyengine.py parameter or variable match."""

    kind: CatalogueKind
    query: str
    identifier: str
    label: str


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
        normalised.append(CatalogueQuery(item.kind, query))
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
                rows = search_reform_targets(item.query, limit=MATCH_LIMIT)
                item_matches = [
                    CatalogueMatch(
                        kind=item.kind,
                        query=item.query,
                        identifier=row["path"],
                        label=row.get("label") or row["path"],
                    )
                    for row in rows
                ]
            else:
                response = search_variables(item.query, limit=MATCH_LIMIT)
                item_matches = [
                    CatalogueMatch(
                        kind=item.kind,
                        query=item.query,
                        identifier=row["name"],
                        label=row.get("label") or row["name"],
                    )
                    for row in response["variables"]
                ]
            if item_matches:
                matches.extend(item_matches)
            else:
                unresolved.append(item)
    except Exception:  # noqa: BLE001 - catalogue metadata must fail open
        return CatalogueEvidence(available=False)

    return CatalogueEvidence(
        available=True,
        matches=tuple(matches),
        unresolved_queries=tuple(unresolved),
    )
