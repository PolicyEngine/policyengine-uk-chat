"""Tests for the policyengine.py runtime boundary."""

import sys
from types import ModuleType, SimpleNamespace

from engine import py_runtime
from engine.constants import (
    DEFAULT_UK_DATASET,
    DEFAULT_UK_DATASET_URI,
    is_row_level_restricted_dataset,
)


def test_default_dataset_resolves_pinned_release_reference(monkeypatch):
    calls = []

    def fake_resolve(country, reference):
        calls.append((country, reference))
        return reference

    monkeypatch.delenv("POLICYENGINE_UK_DEFAULT_DATASET", raising=False)
    monkeypatch.setattr(py_runtime, "_manifest_module", lambda: fake_resolve)
    py_runtime.resolve_dataset.cache_clear()
    try:
        spec = py_runtime.resolve_dataset()
    finally:
        py_runtime.resolve_dataset.cache_clear()

    assert calls == [("uk", DEFAULT_UK_DATASET_URI)]
    assert spec.name == DEFAULT_UK_DATASET
    assert spec.uri == DEFAULT_UK_DATASET_URI
    assert spec.row_level_access is False


def test_default_dataset_honours_deployment_override(monkeypatch):
    override = (
        "hf://policyengine/policyengine-uk-data-private/"
        "enhanced_frs_2023_24.h5@incident-fallback"
    )
    calls = []

    monkeypatch.setenv("POLICYENGINE_UK_DEFAULT_DATASET", override)
    monkeypatch.setattr(
        py_runtime,
        "_manifest_module",
        lambda: lambda country, reference: calls.append((country, reference))
        or reference,
    )
    py_runtime.resolve_dataset.cache_clear()
    try:
        spec = py_runtime.resolve_dataset()
    finally:
        py_runtime.resolve_dataset.cache_clear()

    assert calls == [("uk", override)]
    assert spec.uri == override
    assert spec.name == "enhanced_frs_2023_24"
    assert spec.label == "Enhanced FRS 2023-24"
    assert "2024-25" not in (spec.notes or "")
    assert spec.is_default is True
    assert spec.row_level_access is False


def test_managed_dataset_materializes_resolved_reference(monkeypatch):
    calls = []
    spec = py_runtime.DatasetSpec(
        name=DEFAULT_UK_DATASET,
        label="Enhanced FRS 2024-25",
        uri=DEFAULT_UK_DATASET_URI,
        is_default=True,
        is_policyengine_standard_default=False,
        row_level_access=False,
    )

    monkeypatch.setattr(py_runtime, "resolve_dataset", lambda _name: spec)
    monkeypatch.setattr(
        py_runtime,
        "_managed_dataset",
        lambda reference, year, folder: calls.append((reference, year, folder))
        or "dataset",
    )

    assert py_runtime.managed_dataset(year=2026) == "dataset"
    assert calls == [
        (DEFAULT_UK_DATASET_URI, 2026, "/tmp/policyengine-uk-chat-data")
    ]


def test_all_enhanced_frs_vintages_are_row_level_restricted():
    assert is_row_level_restricted_dataset("enhanced_frs_2023_24")
    assert is_row_level_restricted_dataset(DEFAULT_UK_DATASET)


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
        dataset="enhanced_frs_2024_25",
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
        dataset="enhanced_frs_2024_25",
        reform={"gov.example": None},
    )

    assert compiled[-1] == (None, 2026, model)
    assert empty_reform is baseline_only
    assert baseline_only.ran is True


def test_no_low_level_microsimulation_factory_remains():
    assert not hasattr(py_runtime, "managed_microsimulation")
