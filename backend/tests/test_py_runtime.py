"""Tests for the policyengine.py runtime boundary."""

import sys
from types import ModuleType, SimpleNamespace

from engine import py_runtime


def test_certified_default_dataset_metadata_comes_from_release_manifest(monkeypatch):
    calls = []
    resolved_uri = (
        "hf://policyengine/policyengine-uk-data-private/"
        "enhanced_frs_2024_25.h5@1.56.16"
    )
    manifest = SimpleNamespace(
        default_dataset="enhanced_frs_2024_25",
        default_dataset_uri=resolved_uri,
        datasets={
            "enhanced_frs_2024_25": SimpleNamespace(
                revision="1.56.16",
                sha256="manifest-sha256",
            )
        },
        data_package=SimpleNamespace(
            name="policyengine-uk-data",
            version="1.56.16",
        ),
        certified_data_artifact=SimpleNamespace(
            dataset="enhanced_frs_2024_25",
            data_package=SimpleNamespace(
                name="policyengine-uk-data",
                version="1.56.16",
            ),
            sha256="certified-sha256",
        ),
        certification=SimpleNamespace(
            compatibility_basis="legacy_compatible_model_package",
            certified_for_model_version="2.90.2",
        ),
    )

    def fake_manifest(country):
        calls.append(country)
        return manifest

    monkeypatch.setattr(py_runtime, "_manifest_module", lambda: fake_manifest)
    py_runtime.resolve_dataset.cache_clear()
    try:
        spec = py_runtime.resolve_dataset()
    finally:
        py_runtime.resolve_dataset.cache_clear()

    assert calls == ["uk"]
    assert spec.name == "enhanced_frs_2024_25"
    assert spec.title == "Enhanced FRS 2024-25"
    assert spec.uri == resolved_uri
    assert spec.data_package_name == "policyengine-uk-data"
    assert spec.data_package_version == "1.56.16"
    assert spec.revision == "1.56.16"
    assert spec.sha256 == "certified-sha256"
    assert spec.certification_basis == "legacy_compatible_model_package"
    assert spec.certified_for_model_version == "2.90.2"
    assert spec.row_level_access is False


def test_managed_dataset_delegates_year_and_cache_folder(monkeypatch):
    calls = []

    monkeypatch.setattr(
        py_runtime,
        "_managed_dataset",
        lambda year, folder: calls.append((year, folder)) or "dataset",
    )

    assert py_runtime.managed_dataset(year=2026) == "dataset"
    assert calls == [(2026, "/tmp/policyengine-uk-chat-data")]


def test_native_default_loader_omits_dataset_selector(monkeypatch):
    calls = []

    class FakeUK:
        def ensure_datasets(self, **kwargs):
            calls.append(kwargs)
            return {"release-default-2026": "dataset"}

    monkeypatch.setattr(
        py_runtime,
        "_policyengine_module",
        lambda: SimpleNamespace(uk=FakeUK()),
    )
    py_runtime._managed_dataset.cache_clear()
    try:
        dataset = py_runtime._managed_dataset(2026, "/tmp/managed-data")
    finally:
        py_runtime._managed_dataset.cache_clear()

    assert dataset == "dataset"
    assert calls == [
        {
            "years": [2026],
            "data_folder": "/tmp/managed-data",
        }
    ]


def test_managed_simulation_pair_uses_high_level_simulation(monkeypatch):
    created = []
    compiled = []

    class FakeSimulation:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.ran = False
            created.append(self)

        def run(self):
            self.ran = True

    def compile_reform_to_policy(reform, *, year, model_version):
        compiled.append((reform, year, model_version))
        return "compiled-policy" if reform else None

    policyengine = ModuleType("policyengine")
    core = ModuleType("policyengine.core")
    core.Simulation = FakeSimulation
    common = ModuleType("policyengine.tax_benefit_models.common")
    reform_module = ModuleType("policyengine.tax_benefit_models.common.reform")
    reform_module.compile_reform_to_policy = compile_reform_to_policy
    monkeypatch.setitem(sys.modules, "policyengine", policyengine)
    monkeypatch.setitem(sys.modules, "policyengine.core", core)
    monkeypatch.setitem(sys.modules, "policyengine.tax_benefit_models.common", common)
    monkeypatch.setitem(
        sys.modules,
        "policyengine.tax_benefit_models.common.reform",
        reform_module,
    )

    model = object()
    monkeypatch.setattr(
        py_runtime,
        "_policyengine_module",
        lambda: SimpleNamespace(uk=SimpleNamespace(model=model)),
    )
    monkeypatch.setattr(
        py_runtime,
        "managed_dataset",
        lambda **_kwargs: "managed-dataset",
    )

    baseline, reform = py_runtime.managed_simulation_pair(
        year=2026,
        reform={"gov.example": 1},
        extra_variables={"household": ["rent"]},
    )

    assert compiled == [({"gov.example": 1}, 2026, model)]
    assert baseline.kwargs == {
        "dataset": "managed-dataset",
        "tax_benefit_model_version": model,
        "extra_variables": {"household": ["rent"]},
    }
    assert reform.kwargs["policy"] == "compiled-policy"
    assert baseline.ran is True
    assert reform.ran is True
    assert len(created) == 2

    baseline_only, empty_reform = py_runtime.managed_simulation_pair(
        year=2026,
        reform={"gov.example": None},
    )

    assert compiled[-1] == (None, 2026, model)
    assert empty_reform is baseline_only
    assert baseline_only.ran is True


def test_no_low_level_microsimulation_factory_remains():
    assert not hasattr(py_runtime, "managed_microsimulation")
