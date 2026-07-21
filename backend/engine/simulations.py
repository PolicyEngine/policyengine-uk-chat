"""High-level policyengine.py society simulation lifecycle."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from engine.constants import DATASET_LABELS, DEFAULT_UK_DATASET
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
    dataset: str | None = None,
    extra_variables: dict[str, list[str]] | None = None,
) -> SocietySimulationRun:
    """Materialize baseline and reform policyengine.py Simulations."""

    normalized_reform = normalize_reform_dict(reform)
    dataset_spec = resolve_dataset(dataset or DEFAULT_UK_DATASET)
    baseline, reform_simulation = managed_simulation_pair(
        year=year,
        dataset=dataset_spec.name,
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


def get_capabilities() -> dict[str, Any]:
    from engine.discovery import supported_outputs
    from engine.py_runtime import list_dataset_specs

    return {
        "engine": "policyengine.py",
        "datasets": [asdict(spec) for spec in list_dataset_specs()],
        "default_dataset": DEFAULT_UK_DATASET,
        "supported_outputs": supported_outputs(),
    }


def dataset_label(dataset: str | None) -> str:
    return DATASET_LABELS.get(
        dataset or DEFAULT_UK_DATASET,
        dataset or DEFAULT_UK_DATASET,
    )
