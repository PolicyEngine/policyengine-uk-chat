"""policyengine.py runtime boundary for UK chat tools."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    label: str
    uri: str
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
    from policyengine.provenance.manifest import get_release_manifest

    return get_release_manifest


@lru_cache(maxsize=1)
def uk_model_version():
    """Return the managed policyengine.py UK model version."""

    return _policyengine_module().uk.model


@lru_cache(maxsize=1)
def resolve_dataset() -> DatasetSpec:
    """Describe policyengine.py's certified default UK dataset."""

    manifest = _manifest_module()("uk")
    name = manifest.default_dataset
    return DatasetSpec(
        name=name,
        label=_dataset_label(name),
        uri=manifest.default_dataset_uri,
        row_level_access=False,
        notes="policyengine.py's certified default UK society dataset.",
    )


def _dataset_label(name: str) -> str:
    enhanced_frs = re.fullmatch(r"enhanced_frs_(\d{4})_(\d{2})", name)
    if enhanced_frs is None:
        return name
    return f"Enhanced FRS {enhanced_frs.group(1)}-{enhanced_frs.group(2)}"


def _managed_dataset_folder() -> str:
    return os.environ.get("POLICYENGINE_DATA_FOLDER", "/tmp/policyengine-uk-chat-data")


@lru_cache(maxsize=16)
def _managed_dataset(year: int, data_folder: str):
    """Load policyengine.py's certified default UK dataset for one year."""

    datasets = _policyengine_module().uk.ensure_datasets(
        years=[year],
        data_folder=data_folder,
    )
    if len(datasets) != 1:
        raise RuntimeError(
            f"Expected one certified default UK dataset in {year}, "
            f"received {list(datasets)}."
        )
    return next(iter(datasets.values()))


def managed_dataset(*, year: int):
    """Return policyengine.py's certified default UK Dataset for Simulation."""

    return _managed_dataset(year, _managed_dataset_folder())


def managed_simulation_pair(
    *,
    year: int,
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
    simulation_dataset = managed_dataset(year=year)
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
