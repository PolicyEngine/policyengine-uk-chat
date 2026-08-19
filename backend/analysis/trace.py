"""Private control-flow diagnostics excluded from public API projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AnalysisTrace:
    workflow_version: int
    update_kind: str | None = None
    revision_relationship: str | None = None
    inherited_fields: tuple[str, ...] = ()
    cleared_fields: tuple[str, ...] = ()
    binding_outcome: str | None = None
    clarification_id: str | None = None
    plan_id: str | None = None
    plan_hash: str | None = None
    execution_mode: str | None = None
    permitted_operations: tuple[str, ...] = ()
    step_status: tuple[tuple[str, str], ...] = ()
    conflict_count: int = 0
    interpretation_retries: int = 0
    model_usage: dict[str, int] = field(default_factory=dict)

    def as_private_dict(self) -> dict[str, Any]:
        return {
            name: getattr(self, name)
            for name in self.__dataclass_fields__
        }
