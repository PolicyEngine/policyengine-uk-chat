"""Typed adapters over policyengine.py society-wide output classes."""

from __future__ import annotations

from typing import Any, Literal

from engine.decile_concepts import (
    DEFAULT_DECILE_CONCEPT,
    DecileConcept,
    resolve_decile_concept,
)
from engine.simulations import SocietySimulationRun


AggregateOperation = Literal["sum", "mean", "count"]
SimulationTarget = Literal["baseline", "reform", "change"]

_TAX_CREDIT_AGGREGATE = "tax_credits"
_TAX_CREDIT_COMPONENTS = frozenset({"working_tax_credit", "child_tax_credit"})


def aggregate_result(
    run: SocietySimulationRun,
    *,
    target: SimulationTarget,
    entity: str,
    variable: str,
    operation: AggregateOperation,
    filter_variable: str | None = None,
    filter_variable_eq: Any = None,
    filter_variable_leq: Any = None,
    filter_variable_geq: Any = None,
) -> dict[str, Any]:
    """Run policyengine.py's weighted Aggregate or ChangeAggregate output."""

    from policyengine.outputs import (
        Aggregate,
        AggregateType,
        ChangeAggregate,
        ChangeAggregateType,
    )

    filter_kwargs = {
        "filter_variable": filter_variable,
        "filter_variable_eq": filter_variable_eq,
        "filter_variable_leq": filter_variable_leq,
        "filter_variable_geq": filter_variable_geq,
    }
    filter_kwargs = {
        key: value for key, value in filter_kwargs.items() if value is not None
    }

    if target == "change":
        output = ChangeAggregate(
            baseline_simulation=run.baseline,
            reform_simulation=run.reform_simulation,
            variable=variable,
            aggregate_type=ChangeAggregateType(operation),
            entity=entity,
            **filter_kwargs,
        )
    else:
        simulation = run.baseline if target == "baseline" else run.reform_simulation
        output = Aggregate(
            simulation=simulation,
            variable=variable,
            aggregate_type=AggregateType(operation),
            entity=entity,
            **filter_kwargs,
        )
    output.run()
    return {
        "target": target,
        "entity": entity,
        "variable": variable,
        "operation": operation,
        **filter_kwargs,
        "value": float(output.result),
    }


def _total(run: SocietySimulationRun, target: SimulationTarget, variable: str) -> float:
    return aggregate_result(
        run,
        target=target,
        entity="household",
        variable=variable,
        operation="sum",
    )["value"]


def budgetary_impact(run: SocietySimulationRun) -> dict[str, Any]:
    """Build a fiscal summary from official weighted aggregate outputs."""

    baseline_tax = _total(run, "baseline", "household_tax")
    reform_tax = _total(run, "reform", "household_tax")
    tax_change = _total(run, "change", "household_tax")
    baseline_benefits = _total(run, "baseline", "household_benefits")
    reform_benefits = _total(run, "reform", "household_benefits")
    benefit_change = _total(run, "change", "household_benefits")
    return {
        "tax_revenue": {
            "baseline": baseline_tax,
            "reform": reform_tax,
            "change": tax_change,
        },
        "benefit_spending": {
            "baseline": baseline_benefits,
            "reform": reform_benefits,
            "change": benefit_change,
        },
        "net_budgetary_impact": tax_change - benefit_change,
    }


def program_breakdown(
    run: SocietySimulationRun,
    *,
    programs: list[str] | None = None,
) -> dict[str, Any]:
    """Return policyengine.py UK ProgramStatistics rows."""

    from policyengine.outputs import build_program_statistics
    from policyengine.tax_benefit_models.uk.analysis import UK_PROGRAMS

    requested = set(programs) if programs is not None else None
    unknown = sorted((requested or set()) - set(UK_PROGRAMS))
    if unknown:
        raise ValueError(f"Unknown UK programme(s): {', '.join(unknown)}")
    if (
        requested
        and _TAX_CREDIT_AGGREGATE in requested
        and requested.intersection(_TAX_CREDIT_COMPONENTS)
    ):
        raise ValueError(
            "tax_credits overlaps with working_tax_credit and child_tax_credit; "
            "request the aggregate row or its component rows, not both."
        )

    # Prefer the detailed, non-overlapping rows for the default programme view.
    if requested is None:
        requested = set(UK_PROGRAMS) - {_TAX_CREDIT_AGGREGATE}
    selected = {
        name: config
        for name, config in UK_PROGRAMS.items()
        if name in requested
    }
    collection = build_program_statistics(
        selected,
        run.baseline,
        run.reform_simulation,
    )
    rows = [
        {
            "program": output.program_name,
            "entity": output.entity,
            "is_tax": output.is_tax,
            "baseline_total": output.baseline_total,
            "reform_total": output.reform_total,
            "change": output.change,
            "baseline_count": output.baseline_count,
            "reform_count": output.reform_count,
            "winners": output.winners,
            "losers": output.losers,
        }
        for output in collection.outputs
    ]
    return {
        "programs": rows,
        "net_budgetary_impact": budgetary_impact(run)["net_budgetary_impact"],
    }


def decile_impacts(
    run: SocietySimulationRun,
    *,
    decile_concept: DecileConcept | str = DEFAULT_DECILE_CONCEPT,
) -> dict[str, Any]:
    """Return official policyengine.py decile-impact rows."""

    concept, config = resolve_decile_concept(decile_concept)
    from policyengine.outputs import DecileImpact

    # Keep all three arguments explicit. Changes to policyengine.py defaults
    # must not silently change the analytical meaning of a public concept.
    kwargs = {
        "income_variable": config.income_variable,
        "decile_variable": config.decile_variable,
        "entity": config.entity,
    }
    outputs = []
    for decile in range(1, 11):
        output = DecileImpact(
            baseline_simulation=run.baseline,
            reform_simulation=run.reform_simulation,
            decile=decile,
            **kwargs,
        )
        output.run()
        outputs.append(output)
    rows = [
        {
            "decile": output.decile,
            "baseline_mean": output.baseline_mean,
            "reform_mean": output.reform_mean,
            "absolute_change": output.absolute_change,
            "relative_change": output.relative_change,
            "count_better_off": output.count_better_off,
            "count_worse_off": output.count_worse_off,
            "count_no_change": output.count_no_change,
        }
        for output in outputs
    ]
    return {
        "decile_concept": concept.value,
        "basis": config.basis,
        "income_variable": kwargs["income_variable"],
        "decile_variable": kwargs["decile_variable"],
        "grouping_variable": (
            kwargs["decile_variable"] or kwargs["income_variable"]
        ),
        "entity": kwargs["entity"],
        "deciles": rows,
    }


def winners_losers(
    run: SocietySimulationRun,
    *,
    basis: Literal["income", "wealth"] = "income",
) -> dict[str, Any]:
    """Return official people-weighted intra-decile impact rows."""

    from policyengine.outputs import compute_intra_decile_impacts

    decile_variable = (
        "household_income_decile"
        if basis == "income"
        else "household_wealth_decile"
    )
    collection = compute_intra_decile_impacts(
        baseline_simulation=run.baseline,
        reform_simulation=run.reform_simulation,
        income_variable="household_net_income",
        decile_variable=decile_variable,
        entity="household",
    )
    rows = [
        {
            "decile": output.decile,
            "lose_more_than_5pct": output.lose_more_than_5pct,
            "lose_less_than_5pct": output.lose_less_than_5pct,
            "no_change": output.no_change,
            "gain_less_than_5pct": output.gain_less_than_5pct,
            "gain_more_than_5pct": output.gain_more_than_5pct,
        }
        for output in collection.outputs
    ]
    return {"basis": basis, "deciles": rows}


def _poverty_rows(collection: Any, group: str) -> dict[tuple[str, str], Any]:
    return {
        (output.poverty_type, output.filter_group or group): output
        for output in collection.outputs
    }


def poverty_metrics(run: SocietySimulationRun) -> dict[str, Any]:
    """Return official UK poverty rates overall and by age."""

    from policyengine.outputs import (
        calculate_uk_poverty_by_age,
        calculate_uk_poverty_rates,
    )

    baseline = {
        **_poverty_rows(calculate_uk_poverty_rates(run.baseline), "all"),
        **_poverty_rows(calculate_uk_poverty_by_age(run.baseline), "all"),
    }
    reform = {
        **_poverty_rows(calculate_uk_poverty_rates(run.reform_simulation), "all"),
        **_poverty_rows(
            calculate_uk_poverty_by_age(run.reform_simulation),
            "all",
        ),
    }
    rows = []
    for key in sorted(baseline):
        baseline_output = baseline[key]
        reform_output = reform[key]
        baseline_rate = float(baseline_output.rate)
        reform_rate = float(reform_output.rate)
        rows.append(
            {
                "poverty_type": key[0],
                "group": key[1],
                "baseline_rate": baseline_rate,
                "reform_rate": reform_rate,
                "rate_change": reform_rate - baseline_rate,
                "relative_change": (
                    (reform_rate - baseline_rate) / baseline_rate
                    if baseline_rate
                    else None
                ),
                "baseline_headcount": float(baseline_output.headcount),
                "reform_headcount": float(reform_output.headcount),
            }
        )
    return {"rates": rows}


def inequality_metrics(run: SocietySimulationRun) -> dict[str, Any]:
    """Return official UK inequality metrics for baseline and reform."""

    from policyengine.outputs import calculate_uk_inequality

    baseline = calculate_uk_inequality(run.baseline)
    reform = calculate_uk_inequality(run.reform_simulation)
    metrics = {}
    for name in ("gini", "top_10_share", "top_1_share", "bottom_50_share"):
        baseline_value = float(getattr(baseline, name))
        reform_value = float(getattr(reform, name))
        metrics[name] = {
            "baseline": baseline_value,
            "reform": reform_value,
            "change": reform_value - baseline_value,
            "relative_change": (
                (reform_value - baseline_value) / baseline_value
                if baseline_value
                else None
            ),
        }
    return {"metrics": metrics}
