"""Tests for the policyengine.py runtime boundary."""

import sys
from types import ModuleType, SimpleNamespace

from engine import py_runtime
from engine.constants import UK_CHAT_DATASET


def test_fixed_dataset_name_resolves_through_release_manifest(monkeypatch):
    calls = []
    resolved_uri = (
        "hf://policyengine/policyengine-uk-data-private/"
        "enhanced_frs_2024_25.h5@1.56.16"
    )

    def fake_resolve(country, reference):
        calls.append((country, reference))
        return resolved_uri

    monkeypatch.setattr(py_runtime, "_manifest_module", lambda: fake_resolve)
    py_runtime.resolve_dataset.cache_clear()
    try:
        spec = py_runtime.resolve_dataset()
    finally:
        py_runtime.resolve_dataset.cache_clear()

    assert calls == [("uk", UK_CHAT_DATASET.name)]
    assert spec.name == UK_CHAT_DATASET.name
    assert spec.label == UK_CHAT_DATASET.label
    assert spec.uri == resolved_uri
    assert spec.row_level_access is False


def test_managed_dataset_materializes_manifest_name(monkeypatch):
    calls = []
    spec = py_runtime.DatasetSpec(
        name=UK_CHAT_DATASET.name,
        label="Enhanced FRS 2024-25",
        uri="hf://example/enhanced_frs_2024_25.h5@1.56.16",
        row_level_access=False,
    )

    monkeypatch.setattr(py_runtime, "resolve_dataset", lambda: spec)
    monkeypatch.setattr(
        py_runtime,
        "_managed_dataset",
        lambda reference, year, folder: calls.append((reference, year, folder))
        or "dataset",
    )

    assert py_runtime.managed_dataset(year=2026) == "dataset"
    assert calls == [
        (UK_CHAT_DATASET.name, 2026, "/tmp/policyengine-uk-chat-data")
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


def test_mirror_hugging_face_token_sets_hf_token(monkeypatch):
    monkeypatch.setenv("HUGGING_FACE_TOKEN", "hf_secret")
    monkeypatch.delenv("HF_TOKEN", raising=False)

    py_runtime._mirror_hugging_face_token()

    import os

    assert os.environ["HF_TOKEN"] == "hf_secret"


def test_mirror_hugging_face_token_keeps_existing_hf_token(monkeypatch):
    monkeypatch.setenv("HUGGING_FACE_TOKEN", "hf_secret")
    monkeypatch.setenv("HF_TOKEN", "hf_existing")

    py_runtime._mirror_hugging_face_token()

    import os

    assert os.environ["HF_TOKEN"] == "hf_existing"


def test_mirror_hugging_face_token_noop_without_token(monkeypatch):
    monkeypatch.delenv("HUGGING_FACE_TOKEN", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    py_runtime._mirror_hugging_face_token()

    import os

    assert "HF_TOKEN" not in os.environ
