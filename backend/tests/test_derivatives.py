"""Tests that society derivatives delegate to policyengine.py outputs."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from engine import derivatives
from engine.simulations import SocietySimulationRun


def _run() -> SocietySimulationRun:
    return SocietySimulationRun(
        year=2026,
        dataset=SimpleNamespace(name="enhanced_frs_2024_25"),
        reform_applied=True,
        reform={"gov.example": 1},
        baseline="baseline",
        reform_simulation="reform",
    )


def _install_policyengine_outputs(monkeypatch, **members):
    policyengine = ModuleType("policyengine")
    outputs = ModuleType("policyengine.outputs")
    for name, value in members.items():
        setattr(outputs, name, value)
    policyengine.outputs = outputs
    monkeypatch.setitem(sys.modules, "policyengine", policyengine)
    monkeypatch.setitem(sys.modules, "policyengine.outputs", outputs)


def test_aggregate_result_uses_policyengine_output_classes(monkeypatch):
    calls = []

    class FakeOutput:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.result = None

        def run(self):
            self.result = 12_345

    _install_policyengine_outputs(
        monkeypatch,
        Aggregate=FakeOutput,
        AggregateType=lambda value: value,
        ChangeAggregate=FakeOutput,
        ChangeAggregateType=lambda value: value,
    )

    baseline = derivatives.aggregate_result(
        _run(),
        target="baseline",
        entity="household",
        variable="household_tax",
        operation="sum",
    )
    change = derivatives.aggregate_result(
        _run(),
        target="change",
        entity="person",
        variable="income_tax",
        operation="mean",
        filter_variable="age",
        filter_variable_geq=18,
    )

    assert baseline["value"] == 12_345
    assert calls[0]["simulation"] == "baseline"
    assert calls[0]["aggregate_type"] == "sum"
    assert calls[1]["baseline_simulation"] == "baseline"
    assert calls[1]["reform_simulation"] == "reform"
    assert calls[1]["aggregate_type"] == "mean"
    assert calls[1]["filter_variable"] == "age"
    assert calls[1]["filter_variable_geq"] == 18
    assert change["filter_variable"] == "age"
    assert change["filter_variable_geq"] == 18


def test_budgetary_impact_uses_official_change_aggregates(monkeypatch):
    calls = []
    values = {
        ("baseline", "household_tax"): 100.0,
        ("reform", "household_tax"): 112.0,
        ("change", "household_tax"): 12.0,
        ("baseline", "household_benefits"): 50.0,
        ("reform", "household_benefits"): 55.0,
        ("change", "household_benefits"): 5.0,
    }

    def fake_aggregate(_run, *, target, entity, variable, operation):
        calls.append((target, entity, variable, operation))
        return {"value": values[(target, variable)]}

    monkeypatch.setattr(derivatives, "aggregate_result", fake_aggregate)

    result = derivatives.budgetary_impact(_run())

    assert result["tax_revenue"]["change"] == 12.0
    assert result["benefit_spending"]["change"] == 5.0
    assert result["net_budgetary_impact"] == 7.0
    assert ("change", "household", "household_tax", "sum") in calls
    assert ("change", "household", "household_benefits", "sum") in calls


def test_program_breakdown_uses_non_overlapping_default_rows(monkeypatch):
    captured = {}

    def build_program_statistics(programs, _baseline, _reform):
        captured["programs"] = programs
        return SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    program_name=name,
                    entity="benunit",
                    is_tax=config["is_tax"],
                    baseline_total=1.0,
                    reform_total=2.0,
                    change=1.0,
                    baseline_count=1.0,
                    reform_count=1.0,
                    winners=1.0,
                    losers=0.0,
                )
                for name, config in programs.items()
            ]
        )

    _install_policyengine_outputs(
        monkeypatch,
        build_program_statistics=build_program_statistics,
    )
    tax_benefit_models = ModuleType("policyengine.tax_benefit_models")
    uk = ModuleType("policyengine.tax_benefit_models.uk")
    analysis = ModuleType("policyengine.tax_benefit_models.uk.analysis")
    analysis.UK_PROGRAMS = {
        "income_tax": {"is_tax": True},
        "tax_credits": {"is_tax": False},
        "working_tax_credit": {"is_tax": False},
        "child_tax_credit": {"is_tax": False},
    }
    monkeypatch.setitem(
        sys.modules,
        "policyengine.tax_benefit_models",
        tax_benefit_models,
    )
    monkeypatch.setitem(sys.modules, "policyengine.tax_benefit_models.uk", uk)
    monkeypatch.setitem(
        sys.modules,
        "policyengine.tax_benefit_models.uk.analysis",
        analysis,
    )
    monkeypatch.setattr(
        derivatives,
        "budgetary_impact",
        lambda _run: {"net_budgetary_impact": 3.0},
    )

    result = derivatives.program_breakdown(_run())

    assert "tax_credits" not in captured["programs"]
    assert {"working_tax_credit", "child_tax_credit"}.issubset(captured["programs"])
    assert result["net_budgetary_impact"] == 3.0

    with pytest.raises(ValueError, match="overlaps"):
        derivatives.program_breakdown(
            _run(),
            programs=["tax_credits", "working_tax_credit"],
        )


def test_decile_and_winners_losers_use_official_output_rows(monkeypatch):
    decile_calls = []
    winners_calls = []

    class FakeDecileImpact:
        def __init__(self, **kwargs):
            decile_calls.append(kwargs)
            self.decile = kwargs["decile"]
            self.baseline_mean = 100
            self.reform_mean = 110
            self.absolute_change = 10
            self.relative_change = 10
            self.count_better_off = 50
            self.count_worse_off = 5
            self.count_no_change = 45

        def run(self):
            return None

    def compute_intra_decile_impacts(**kwargs):
        winners_calls.append(kwargs)
        return SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    decile=1,
                    lose_more_than_5pct=0.1,
                    lose_less_than_5pct=0.2,
                    no_change=0.3,
                    gain_less_than_5pct=0.2,
                    gain_more_than_5pct=0.2,
                )
            ]
        )

    _install_policyengine_outputs(
        monkeypatch,
        DecileImpact=FakeDecileImpact,
        compute_intra_decile_impacts=compute_intra_decile_impacts,
    )

    deciles = derivatives.decile_impacts(_run(), basis="income")
    winners = derivatives.winners_losers(_run(), basis="wealth")

    assert deciles["deciles"][0]["relative_change"] == 10
    assert len(decile_calls) == 10
    assert decile_calls[0]["baseline_simulation"] == "baseline"
    assert winners["deciles"][0]["gain_more_than_5pct"] == 0.2
    assert winners_calls[0]["decile_variable"] == "household_wealth_decile"


def test_poverty_and_inequality_compare_official_outputs(monkeypatch):
    def poverty_rates(simulation):
        rate = 0.2 if simulation == "baseline" else 0.18
        return SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    poverty_type="relative_bhc",
                    filter_group=None,
                    rate=rate,
                    headcount=rate * 1_000,
                )
            ]
        )

    def poverty_by_age(simulation):
        rate = 0.3 if simulation == "baseline" else 0.27
        return SimpleNamespace(
            outputs=[
                SimpleNamespace(
                    poverty_type="relative_bhc",
                    filter_group="child",
                    rate=rate,
                    headcount=rate * 500,
                )
            ]
        )

    def inequality(simulation):
        value = 0.4 if simulation == "baseline" else 0.38
        return SimpleNamespace(
            gini=value,
            top_10_share=value,
            top_1_share=value,
            bottom_50_share=value,
        )

    _install_policyengine_outputs(
        monkeypatch,
        calculate_uk_poverty_rates=poverty_rates,
        calculate_uk_poverty_by_age=poverty_by_age,
        calculate_uk_inequality=inequality,
    )

    poverty = derivatives.poverty_metrics(_run())
    inequality_result = derivatives.inequality_metrics(_run())

    assert poverty["rates"][0]["relative_change"] == pytest.approx(-0.1)
    assert {row["group"] for row in poverty["rates"]} == {"all", "child"}
    assert inequality_result["metrics"]["gini"]["change"] == pytest.approx(-0.02)
