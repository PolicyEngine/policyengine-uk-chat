"""High-level policyengine.py society simulation lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from engine.py_runtime import (
    DatasetSpec,
    managed_simulation_pair,
    resolve_dataset,
)
from engine.reforms import normalize_reform_dict


@dataclass(frozen=True)
class SocietySimulationRun:
    """A baseline/reform pair backed by policyengine.core.Simulation."""

    year: int
    dataset: DatasetSpec
    reform_applied: bool
    reform: dict[str, Any] | None
    baseline: Any
    reform_simulation: Any

    def metadata(self) -> dict[str, Any]:
        return {
            "status": "success",
            "fiscal_year": str(self.year),
            "year": self.year,
            "dataset": asdict(self.dataset),
            "reform_applied": self.reform_applied,
        }


def build_society_simulation(
    *,
    year: int,
    reform: dict[str, Any] | None,
    extra_variables: dict[str, list[str]] | None = None,
) -> SocietySimulationRun:
    """Materialize baseline and reform policyengine.py Simulations."""

    normalized_reform = normalize_reform_dict(reform)
    dataset_spec = resolve_dataset()
    baseline, reform_simulation = managed_simulation_pair(
        year=year,
        reform=normalized_reform or None,
        extra_variables=extra_variables,
    )
    return SocietySimulationRun(
        year=year,
        dataset=dataset_spec,
        reform_applied=bool(normalized_reform),
        reform=normalized_reform or None,
        baseline=baseline,
        reform_simulation=reform_simulation,
    )
