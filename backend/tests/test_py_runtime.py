"""Tests for the policyengine.py runtime boundary."""

import sys
from types import ModuleType, SimpleNamespace

from engine import py_runtime


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
        dataset="enhanced_frs_2023_24",
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
        dataset="enhanced_frs_2023_24",
        reform={"gov.example": None},
    )

    assert compiled[-1] == (None, 2026, model)
    assert empty_reform is baseline_only
    assert baseline_only.ran is True


def test_no_low_level_microsimulation_factory_remains():
    assert not hasattr(py_runtime, "managed_microsimulation")
