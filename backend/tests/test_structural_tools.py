from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "policyengine-uk-rust" / "interfaces" / "python"))

import agent_tools
from agent_tools import _build_structural_reform


def test_build_structural_reform_pre_hook():
    structural = _build_structural_reform(
        {
            "pre": (
                "def hook(year, persons, benunits, households):\n"
                "    persons = persons.copy()\n"
                "    persons['employment_income'] = persons['employment_income'] * 0.95\n"
                "    return persons, benunits, households\n"
            )
        }
    )
    assert structural is not None
    assert structural.pre is not None
    assert structural.post is None


def test_build_structural_reform_rejects_unknown_fields():
    try:
        _build_structural_reform({"during": "def hook(*args): return args"})
    except ValueError as exc:
        assert "Unknown structural_reform field" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unknown structural_reform field")


def test_run_economy_simulation_uses_true_baseline_hbai(monkeypatch):
    class DummyDump:
        def __init__(self, value):
            self.value = value

        def model_dump(self):
            return self.value

    baseline = SimpleNamespace(
        fiscal_year="2025/26",
        program_breakdown=DummyDump({"income_tax": 100.0}),
        budgetary_impact=DummyDump({"baseline_revenue": 1.0}),
        decile_impacts=[],
        winners_losers=DummyDump({"winners_pct": 0.0}),
        caseloads=DummyDump({"income_tax_payers": 0.0}),
        baseline_hbai_incomes=DummyDump({"mean_bhc": 100.0}),
        reform_hbai_incomes=DummyDump({"mean_bhc": 100.0}),
        baseline_poverty=DummyDump({"relative_bhc_children": 10.0}),
        reform_poverty=DummyDump({"relative_bhc_children": 10.0}),
    )
    reform = SimpleNamespace(
        fiscal_year="2025/26",
        program_breakdown=DummyDump({"income_tax": 90.0}),
        budgetary_impact=DummyDump({"baseline_revenue": 1.0}),
        decile_impacts=[],
        winners_losers=DummyDump({"winners_pct": 100.0}),
        caseloads=DummyDump({"income_tax_payers": 1.0}),
        baseline_hbai_incomes=DummyDump({"mean_bhc": 80.0}),
        reform_hbai_incomes=DummyDump({"mean_bhc": 80.0}),
        baseline_poverty=DummyDump({"relative_bhc_children": 20.0}),
        reform_poverty=DummyDump({"relative_bhc_children": 20.0}),
    )

    class DummySimulation:
        def __init__(self):
            self.calls = 0

        def run(self, policy=None, structural=None):
            self.calls += 1
            return baseline if self.calls == 1 else reform

        def run_microdata(self, policy=None, structural=None):
            return SimpleNamespace(persons="p", benunits="b", households="h")

    monkeypatch.setattr(agent_tools, "_build_simulation", lambda year, dataset: DummySimulation())
    monkeypatch.setattr(agent_tools, "_build_compiled_policy", lambda reform: None)
    monkeypatch.setattr(agent_tools, "_build_structural_reform", lambda structural_reform: object())
    import policyengine_uk_compiled
    monkeypatch.setattr(policyengine_uk_compiled, "combine_microdata", lambda baseline_microdata, reform_microdata: baseline_microdata)
    monkeypatch.setattr(policyengine_uk_compiled, "aggregate_microdata", lambda persons, benunits, households, year: reform)

    result = agent_tools.run_economy_simulation(
        year=2025,
        structural_reform={"pre": "def hook(year, persons, benunits, households): return persons, benunits, households"},
    )

    assert result["baseline_hbai_incomes"]["mean_bhc"] == 100.0
    assert result["reform_hbai_incomes"]["mean_bhc"] == 80.0
