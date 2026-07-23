"""policyengine.py runtime boundary for UK chat tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from engine.constants import (
    DATASET_LABELS,
    DEFAULT_UK_DATASET,
    DEFAULT_UK_DATASET_URI,
    STANDARD_POLICYENGINE_UK_DATASET,
    is_row_level_restricted_dataset,
)


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    label: str
    uri: str
    is_default: bool
    is_policyengine_standard_default: bool
    row_level_access: bool
    notes: str | None = None


def _policyengine_module():
    import policyengine as pe

    if pe.uk is None:
        raise RuntimeError(
            "policyengine.py is installed, but the UK country package is not importable."
        )
    return pe


def _manifest_module():
    from policyengine.provenance.manifest import resolve_dataset_reference

    return resolve_dataset_reference


@lru_cache(maxsize=1)
def uk_model_version():
    """Return the managed policyengine.py UK model version."""

    return _policyengine_module().uk.model


@lru_cache(maxsize=16)
def resolve_dataset(dataset: str | None = None) -> DatasetSpec:
    """Resolve a UK Chat dataset name to a managed dataset reference."""

    name = dataset or DEFAULT_UK_DATASET
    reference = (
        os.environ.get("POLICYENGINE_UK_DEFAULT_DATASET", DEFAULT_UK_DATASET_URI)
        if name == DEFAULT_UK_DATASET
        else name
    )
    uri = _manifest_module()("uk", reference)
    return DatasetSpec(
        name=name,
        label=DATASET_LABELS.get(name, name),
        uri=uri,
        is_default=name == DEFAULT_UK_DATASET,
        is_policyengine_standard_default=name == STANDARD_POLICYENGINE_UK_DATASET,
        row_level_access=not is_row_level_restricted_dataset(name),
        notes=(
            "UK Chat default. Uses the configured pinned Enhanced FRS release."
            if name == DEFAULT_UK_DATASET
            else (
                "policyengine.py standard certified UK dataset."
                if name == STANDARD_POLICYENGINE_UK_DATASET
                else None
            )
        ),
    )


def list_dataset_specs() -> list[DatasetSpec]:
    """Return the managed datasets UK Chat intentionally exposes."""

    names = [
        DEFAULT_UK_DATASET,
        STANDARD_POLICYENGINE_UK_DATASET,
        "frs_2023_24",
    ]
    specs: list[DatasetSpec] = []
    for name in names:
        try:
            specs.append(resolve_dataset(name))
        except Exception:
            # Keep discovery useful even when local package metadata is missing.
            specs.append(
                DatasetSpec(
                    name=name,
                    label=DATASET_LABELS.get(name, name),
                    uri="unavailable",
                    is_default=name == DEFAULT_UK_DATASET,
                    is_policyengine_standard_default=(
                        name == STANDARD_POLICYENGINE_UK_DATASET
                    ),
                    row_level_access=not is_row_level_restricted_dataset(name),
                    notes="Could not resolve in this environment.",
                )
            )
    return specs


def _managed_dataset_folder() -> str:
    return os.environ.get("POLICYENGINE_DATA_FOLDER", "/tmp/policyengine-uk-chat-data")


@lru_cache(maxsize=16)
def _managed_dataset(reference: str, year: int, data_folder: str):
    """Load one configured policyengine.py dataset/year combination."""

    datasets = _policyengine_module().uk.ensure_datasets(
        datasets=[reference],
        years=[year],
        data_folder=data_folder,
    )
    if len(datasets) != 1:
        raise RuntimeError(
            f"Expected one managed UK dataset for {reference!r} in {year}, "
            f"received {list(datasets)}."
        )
    return next(iter(datasets.values()))


def managed_dataset(*, dataset: str | None = None, year: int):
    """Return a configured policyengine.py Dataset ready for Simulation."""

    name = dataset or DEFAULT_UK_DATASET
    spec = resolve_dataset(name)
    return _managed_dataset(spec.uri, year, _managed_dataset_folder())


def managed_simulation_pair(
    *,
    year: int,
    dataset: str | None = None,
    reform: dict[str, Any] | None = None,
    extra_variables: dict[str, list[str]] | None = None,
):
    """Run baseline and reform policyengine.py Simulations on managed data."""

    from engine.reforms import normalize_reform_dict

    pe = _policyengine_module()
    from policyengine.core import Simulation
    from policyengine.tax_benefit_models.common.reform import compile_reform_to_policy

    normalized_reform = normalize_reform_dict(reform)
    policy = compile_reform_to_policy(
        normalized_reform or None,
        year=year,
        model_version=pe.uk.model,
    )
    simulation_dataset = managed_dataset(dataset=dataset, year=year)
    simulation_kwargs = {
        "dataset": simulation_dataset,
        "tax_benefit_model_version": pe.uk.model,
        "extra_variables": extra_variables or {},
    }
    baseline = Simulation(**simulation_kwargs)
    reform_simulation = (
        Simulation(**simulation_kwargs, policy=policy) if policy is not None else baseline
    )
    # Chat result handles are turn-local. Running in memory avoids persisting
    # UUID-named output datasets and retaining them in .py's global cache.
    baseline.run()
    if reform_simulation is not baseline:
        reform_simulation.run()
    return baseline, reform_simulation


def calculate_household_py(**kwargs: Any):
    """Thin wrapper around policyengine.py's UK household calculator."""

    return _policyengine_module().uk.calculate_household(**kwargs)
