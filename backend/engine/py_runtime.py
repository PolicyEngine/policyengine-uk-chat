"""policyengine.py runtime boundary for chat tools.

All country-specific access flows through the active country profile
(``engine.country``); under the default ``CHAT_COUNTRY=uk`` every code
path is identical to the historical UK-only runtime.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from engine.constants import DatasetConfig
from engine.country import active_country_profile


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    label: str
    uri: str
    row_level_access: bool
    notes: str | None = None


def _policyengine_module():
    import policyengine as pe

    country = active_country_profile().id
    if getattr(pe, country, None) is None:
        raise RuntimeError(
            "policyengine.py is installed, but the "
            f"{country.upper()} country package is not importable."
        )
    return pe


def _country_module():
    """The policyengine.py country module for the active profile."""

    return getattr(_policyengine_module(), active_country_profile().id)


def _manifest_module():
    from policyengine.provenance.manifest import resolve_dataset_reference

    return resolve_dataset_reference


@lru_cache(maxsize=1)
def uk_model_version():
    """Return the managed policyengine.py model version for the active country.

    Named for its original UK-only role; call sites treat it as "the
    deployment's model version".
    """

    return _country_module().model


@lru_cache(maxsize=1)
def resolve_dataset() -> DatasetSpec:
    """Resolve this chat's fixed dataset to its managed reference."""

    profile = active_country_profile()
    uri = _manifest_module()(profile.id, profile.dataset.uri)
    resolved = DatasetConfig(uri=uri)
    return DatasetSpec(
        name=resolved.name,
        label=resolved.label,
        uri=uri,
        row_level_access=False,
        notes=profile.dataset_notes,
    )


def _managed_dataset_folder() -> str:
    return os.environ.get("POLICYENGINE_DATA_FOLDER", "/tmp/policyengine-uk-chat-data")


@lru_cache(maxsize=16)
def _managed_dataset(reference: str, year: int, data_folder: str):
    """Load one configured policyengine.py dataset/year combination."""

    datasets = _country_module().ensure_datasets(
        datasets=[reference],
        years=[year],
        data_folder=data_folder,
    )
    if len(datasets) != 1:
        raise RuntimeError(
            f"Expected one managed {active_country_profile().id.upper()} dataset "
            f"for {reference!r} in {year}, received {list(datasets)}."
        )
    return next(iter(datasets.values()))


def managed_dataset(*, year: int):
    """Return UK Chat's fixed policyengine.py Dataset ready for Simulation."""

    spec = resolve_dataset()
    return _managed_dataset(spec.uri, year, _managed_dataset_folder())


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

    country_model = getattr(pe, active_country_profile().id).model
    normalized_reform = normalize_reform_dict(reform)
    policy = compile_reform_to_policy(
        normalized_reform or None,
        year=year,
        model_version=country_model,
    )
    simulation_dataset = managed_dataset(year=year)
    simulation_kwargs = {
        "dataset": simulation_dataset,
        "tax_benefit_model_version": country_model,
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
    """Thin wrapper around the active country's household calculator."""

    return _country_module().calculate_household(**kwargs)
