"""Small deterministic input-precedence helpers shared by capabilities."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


DEFAULT_POLICY_YEAR = 2026


class InputSource(str, Enum):
    CURRENT_REQUEST = "current_request"
    REFERENCED_ARTIFACT = "referenced_artifact"
    SERVER_DEFAULT = "server_default"


class ResolvedYear(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    year: int
    source: InputSource


def resolve_policy_year(
    *,
    explicit_year: int | None,
    referenced_year: int | None,
) -> ResolvedYear:
    if explicit_year is not None:
        return ResolvedYear(year=explicit_year, source=InputSource.CURRENT_REQUEST)
    if referenced_year is not None:
        return ResolvedYear(
            year=referenced_year,
            source=InputSource.REFERENCED_ARTIFACT,
        )
    return ResolvedYear(
        year=DEFAULT_POLICY_YEAR,
        source=InputSource.SERVER_DEFAULT,
    )
