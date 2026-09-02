"""policyengine.py runtime boundary for UK chat tools."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


UK_SOCIETY_DATASET_TITLE = "Enhanced FRS 2024-25"


@dataclass(frozen=True)
class DatasetSpec:
    name: str
    title: str
    uri: str
    data_package_name: str
    data_package_version: str
    revision: str
    row_level_access: bool
    sha256: str | None = None
    certification_basis: str | None = None
    certified_for_model_version: str | None = None
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
    uri = manifest.default_dataset_uri
    reference = manifest.datasets.get(name)
    certified_artifact = manifest.certified_data_artifact
    certification = manifest.certification
    artifact_package = (
        certified_artifact.data_package
        if certified_artifact is not None
        and certified_artifact.dataset == name
        and certified_artifact.data_package is not None
        else manifest.data_package
    )
    revision = reference.revision if reference is not None else None
    if revision is None:
        revision = _dataset_revision(uri)
    certified_sha256 = (
        certified_artifact.sha256
        if certified_artifact is not None and certified_artifact.dataset == name
        else None
    )
    sha256 = certified_sha256 or (
        reference.sha256 if reference is not None else None
    )
    return DatasetSpec(
        name=name,
        title=UK_SOCIETY_DATASET_TITLE,
        uri=uri,
        data_package_name=artifact_package.name,
        data_package_version=artifact_package.version,
        revision=revision,
        row_level_access=False,
        sha256=sha256,
        certification_basis=(
            certification.compatibility_basis if certification is not None else None
        ),
        certified_for_model_version=(
            certification.certified_for_model_version
            if certification is not None
            else None
        ),
        notes="policyengine.py's certified default UK society dataset.",
    )


def _dataset_revision(uri: str) -> str:
    _source, separator, revision = uri.rpartition("@")
    if not separator or not revision:
        raise RuntimeError(
            "policyengine.py's certified default dataset URI has no revision."
        )
    return revision


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
