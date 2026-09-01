"""Validation and durable projection for society-wide aggregate outputs."""

from __future__ import annotations

from collections.abc import Callable
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from capabilities.artifacts import AggregateDimension, AggregateValue


NonNegativeFloat = Annotated[FiniteFloat, Field(ge=0)]
Share = Annotated[FiniteFloat, Field(ge=0, le=1)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PayloadModel(BaseModel):
    # The typed tool boundary owns operational fields such as status and
    # request-local identifiers; projections validate only durable aggregates.
    model_config = ConfigDict(extra="ignore")


class FiscalValues(StrictModel):
    baseline: FiniteFloat
    reform: FiniteFloat
    change: FiniteFloat

    @model_validator(mode="after")
    def change_reconciles(self) -> FiscalValues:
        if not _matches(self.change, self.reform - self.baseline):
            raise ValueError("fiscal change must equal reform minus baseline")
        return self


class BudgetaryImpact(PayloadModel):
    tax_revenue: FiscalValues
    benefit_spending: FiscalValues
    net_budgetary_impact: FiniteFloat

    @model_validator(mode="after")
    def net_impact_reconciles(self) -> BudgetaryImpact:
        expected = self.tax_revenue.change - self.benefit_spending.change
        if not _matches(self.net_budgetary_impact, expected):
            raise ValueError(
                "net budgetary impact must equal tax change minus benefit change"
            )
        return self


class ProgramRow(StrictModel):
    program: str = Field(min_length=1)
    entity: Literal["person", "benunit", "household"]
    is_tax: bool
    baseline_total: FiniteFloat
    reform_total: FiniteFloat
    change: FiniteFloat
    baseline_count: NonNegativeFloat
    reform_count: NonNegativeFloat
    winners: NonNegativeFloat
    losers: NonNegativeFloat

    @model_validator(mode="after")
    def change_reconciles(self) -> ProgramRow:
        if not _matches(self.change, self.reform_total - self.baseline_total):
            raise ValueError("programme change must equal reform minus baseline")
        return self


class ProgramBreakdown(PayloadModel):
    programs: tuple[ProgramRow, ...] = Field(min_length=1)
    net_budgetary_impact: FiniteFloat

    @model_validator(mode="after")
    def programs_are_unique(self) -> ProgramBreakdown:
        programs = [row.program for row in self.programs]
        if len(programs) != len(set(programs)):
            raise ValueError("program rows must have unique program identifiers")
        return self


class DecileImpactRow(StrictModel):
    decile: int = Field(ge=1, le=10, strict=True)
    baseline_mean: FiniteFloat
    reform_mean: FiniteFloat
    absolute_change: FiniteFloat
    relative_change: FiniteFloat
    count_better_off: NonNegativeFloat
    count_worse_off: NonNegativeFloat
    count_no_change: NonNegativeFloat

    @model_validator(mode="after")
    def changes_reconcile(self) -> DecileImpactRow:
        if not _matches(self.absolute_change, self.reform_mean - self.baseline_mean):
            raise ValueError("decile absolute change must equal reform minus baseline")
        if self.baseline_mean == 0:
            if self.relative_change != 0:
                raise ValueError(
                    "decile relative change must be zero when baseline mean is zero"
                )
        elif not _matches(
            self.relative_change,
            self.absolute_change / self.baseline_mean * 100,
        ):
            raise ValueError(
                "decile relative change must be the percentage change from baseline"
            )
        return self


class DecileImpacts(PayloadModel):
    decile_concept: Literal[
        "household_net_income",
        "equivalised_hbai_net_income",
        "wealth",
    ]
    basis: Literal["income", "wealth"]
    income_variable: str = Field(min_length=1)
    decile_variable: str | None
    grouping_variable: str = Field(min_length=1)
    entity: Literal["household"]
    quantiles: Literal[10]
    measure_label: str = Field(min_length=1)
    grouping_label: str = Field(min_length=1)
    deciles: tuple[DecileImpactRow, ...]

    @model_validator(mode="after")
    def contains_every_decile_once(self) -> DecileImpacts:
        deciles = [row.decile for row in self.deciles]
        if sorted(deciles) != list(range(1, 11)):
            raise ValueError(
                "decile impacts must contain each decile from 1 through 10"
            )
        return self


class WinnersLosersRow(StrictModel):
    decile: int = Field(ge=0, le=10, strict=True)
    lose_more_than_5pct: Share
    lose_less_than_5pct: Share
    no_change: Share
    gain_less_than_5pct: Share
    gain_more_than_5pct: Share

    @model_validator(mode="after")
    def shares_sum_to_one(self) -> WinnersLosersRow:
        total = (
            self.lose_more_than_5pct
            + self.lose_less_than_5pct
            + self.no_change
            + self.gain_less_than_5pct
            + self.gain_more_than_5pct
        )
        if abs(total - 1) > 1e-6:
            raise ValueError("winner and loser shares must sum to one")
        return self


class WinnersLosers(PayloadModel):
    basis: Literal["income", "wealth"]
    grouping_label: str = Field(min_length=1)
    deciles: tuple[WinnersLosersRow, ...]

    @model_validator(mode="after")
    def contains_every_group_once(self) -> WinnersLosers:
        deciles = [row.decile for row in self.deciles]
        if sorted(deciles) != list(range(11)):
            raise ValueError(
                "winner and loser impacts must contain the overall row and "
                "deciles 1 through 10"
            )
        return self


class PovertyRateRow(StrictModel):
    poverty_type: Literal[
        "absolute_ahc",
        "absolute_bhc",
        "relative_ahc",
        "relative_bhc",
    ]
    group: Literal["adult", "all", "child", "senior"]
    baseline_rate: Share
    reform_rate: Share
    rate_change: FiniteFloat
    relative_change: FiniteFloat | None
    baseline_headcount: NonNegativeFloat
    reform_headcount: NonNegativeFloat

    @model_validator(mode="after")
    def changes_reconcile(self) -> PovertyRateRow:
        expected_change = self.reform_rate - self.baseline_rate
        if not _matches(self.rate_change, expected_change):
            raise ValueError("poverty rate change must equal reform minus baseline")
        if self.baseline_rate == 0:
            if self.relative_change is not None:
                raise ValueError(
                    "poverty relative change must be absent when baseline rate is zero"
                )
        elif self.relative_change is None or not _matches(
            self.relative_change,
            expected_change / self.baseline_rate,
        ):
            raise ValueError(
                "poverty relative change must reconcile with the baseline rate"
            )
        return self


class PovertyMetrics(PayloadModel):
    rates: tuple[PovertyRateRow, ...]

    @model_validator(mode="after")
    def contains_every_rate_once(self) -> PovertyMetrics:
        expected = {
            (poverty_type, group)
            for poverty_type in (
                "absolute_ahc",
                "absolute_bhc",
                "relative_ahc",
                "relative_bhc",
            )
            for group in ("adult", "all", "child", "senior")
        }
        actual = {(row.poverty_type, row.group) for row in self.rates}
        if actual != expected or len(self.rates) != len(expected):
            raise ValueError(
                "poverty metrics must contain every supported type and group"
            )
        return self


class InequalityValues(StrictModel):
    baseline: Share
    reform: Share
    change: FiniteFloat
    relative_change: FiniteFloat | None

    @model_validator(mode="after")
    def changes_reconcile(self) -> InequalityValues:
        expected_change = self.reform - self.baseline
        if not _matches(self.change, expected_change):
            raise ValueError("inequality change must equal reform minus baseline")
        if self.baseline == 0:
            if self.relative_change is not None:
                raise ValueError(
                    "inequality relative change must be absent when baseline is zero"
                )
        elif self.relative_change is None or not _matches(
            self.relative_change,
            expected_change / self.baseline,
        ):
            raise ValueError(
                "inequality relative change must reconcile with the baseline"
            )
        return self


class InequalityMetrics(PayloadModel):
    metrics: dict[
        Literal["gini", "top_10_share", "top_1_share", "bottom_50_share"],
        InequalityValues,
    ]

    @model_validator(mode="after")
    def contains_every_metric_once(self) -> InequalityMetrics:
        expected = {"gini", "top_10_share", "top_1_share", "bottom_50_share"}
        if set(self.metrics) != expected:
            raise ValueError("inequality output must contain every supported metric")
        return self


def _dimension(name: str, value: object) -> AggregateDimension:
    return AggregateDimension(name=name, value=str(value))


def _matches(actual: float, expected: float) -> bool:
    return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)


def _value(
    *,
    output_id: str,
    metric_id: str,
    label: str,
    value: float | int | None,
    unit: str,
    dimensions: tuple[AggregateDimension, ...] = (),
) -> AggregateValue:
    return AggregateValue(
        output_id=output_id,
        metric_id=metric_id,
        label=label,
        value=value,
        unit=unit,
        dimensions=dimensions,
    )


def _budgetary_values(payload: dict[str, object]) -> tuple[AggregateValue, ...]:
    result = BudgetaryImpact.model_validate(payload)
    values = []
    for metric_id, label, fiscal_values in (
        ("tax_revenue", "Tax revenue", result.tax_revenue),
        ("benefit_spending", "Benefit spending", result.benefit_spending),
    ):
        for scenario in ("baseline", "reform", "change"):
            values.append(
                _value(
                    output_id="budgetary_impact",
                    metric_id=f"{metric_id}.{scenario}",
                    label=f"{label}: {scenario}",
                    value=getattr(fiscal_values, scenario),
                    unit="GBP/year",
                )
            )
    values.append(
        _value(
            output_id="budgetary_impact",
            metric_id="net_budgetary_impact",
            label="Net budgetary impact",
            value=result.net_budgetary_impact,
            unit="GBP/year",
        )
    )
    return tuple(values)


def _program_values(payload: dict[str, object]) -> tuple[AggregateValue, ...]:
    result = ProgramBreakdown.model_validate(payload)
    values = []
    units = {
        "baseline_total": "GBP/year",
        "reform_total": "GBP/year",
        "change": "GBP/year",
        "baseline_count": "people",
        "reform_count": "people",
        "winners": "people",
        "losers": "people",
    }
    for row in result.programs:
        dimensions = (
            _dimension("program", row.program),
            _dimension("entity", row.entity),
            _dimension("kind", "tax" if row.is_tax else "benefit"),
        )
        for metric_id, unit in units.items():
            values.append(
                _value(
                    output_id="program_statistics",
                    metric_id=f"programs.{metric_id}",
                    label=metric_id.replace("_", " ").title(),
                    value=getattr(row, metric_id),
                    unit=unit,
                    dimensions=dimensions,
                )
            )
    values.append(
        _value(
            output_id="program_statistics",
            metric_id="net_budgetary_impact",
            label="Net budgetary impact",
            value=result.net_budgetary_impact,
            unit="GBP/year",
        )
    )
    return tuple(values)


def _decile_values(payload: dict[str, object]) -> tuple[AggregateValue, ...]:
    result = DecileImpacts.model_validate(payload)
    values = []
    units = {
        "baseline_mean": "GBP/year",
        "reform_mean": "GBP/year",
        "absolute_change": "GBP/year",
        "relative_change": "percent",
        "count_better_off": "people",
        "count_worse_off": "people",
        "count_no_change": "people",
    }
    labels = {
        "baseline_mean": f"Baseline mean {result.measure_label}",
        "reform_mean": f"Reform mean {result.measure_label}",
        "absolute_change": f"Change in mean {result.measure_label}",
        "relative_change": f"Percentage change in mean {result.measure_label}",
        "count_better_off": "People better off",
        "count_worse_off": "People worse off",
        "count_no_change": "People with no change",
    }
    for row in result.deciles:
        dimensions = (_dimension("decile", row.decile),)
        for metric_id, unit in units.items():
            values.append(
                _value(
                    output_id="decile_impacts",
                    metric_id=f"deciles.{metric_id}",
                    label=labels[metric_id],
                    value=getattr(row, metric_id),
                    unit=unit,
                    dimensions=dimensions,
                )
            )
    return tuple(values)


def _winner_loser_values(payload: dict[str, object]) -> tuple[AggregateValue, ...]:
    result = WinnersLosers.model_validate(payload)
    values = []
    labels = {
        "lose_more_than_5pct": "Share losing more than 5%",
        "lose_less_than_5pct": "Share losing less than 5%",
        "no_change": "Share with no change",
        "gain_less_than_5pct": "Share gaining less than 5%",
        "gain_more_than_5pct": "Share gaining more than 5%",
    }
    for row in result.deciles:
        dimensions = (
            _dimension("decile", "overall" if row.decile == 0 else row.decile),
        )
        for metric_id, label in labels.items():
            values.append(
                _value(
                    output_id="winners_losers",
                    metric_id=f"deciles.{metric_id}",
                    label=label,
                    value=getattr(row, metric_id),
                    unit="ratio",
                    dimensions=dimensions,
                )
            )
    return tuple(values)


def _poverty_values(payload: dict[str, object]) -> tuple[AggregateValue, ...]:
    result = PovertyMetrics.model_validate(payload)
    values = []
    units = {
        "baseline_rate": "ratio",
        "reform_rate": "ratio",
        "rate_change": "ratio",
        "relative_change": "ratio",
        "baseline_headcount": "people",
        "reform_headcount": "people",
    }
    for row in result.rates:
        dimensions = (
            _dimension("group", row.group),
            _dimension("poverty_type", row.poverty_type),
        )
        for metric_id, unit in units.items():
            value = getattr(row, metric_id)
            if value is None:
                continue
            values.append(
                _value(
                    output_id="poverty",
                    metric_id=f"rates.{metric_id}",
                    label=metric_id.replace("_", " ").title(),
                    value=value,
                    unit=unit,
                    dimensions=dimensions,
                )
            )
    return tuple(values)


def _inequality_values(payload: dict[str, object]) -> tuple[AggregateValue, ...]:
    result = InequalityMetrics.model_validate(payload)
    values = []
    for metric, metric_values in result.metrics.items():
        unit = "number" if metric == "gini" else "ratio"
        for scenario in ("baseline", "reform", "change", "relative_change"):
            value = getattr(metric_values, scenario)
            if value is None:
                continue
            values.append(
                _value(
                    output_id="inequality",
                    metric_id=f"metrics.{metric}.{scenario}",
                    label=(
                        f"{metric.replace('_', ' ').title()}: "
                        f"{scenario.replace('_', ' ')}"
                    ),
                    value=value,
                    unit="ratio" if scenario == "relative_change" else unit,
                )
            )
    return tuple(values)


_PROJECTORS: dict[
    str,
    Callable[[dict[str, object]], tuple[AggregateValue, ...]],
] = {
    "budgetary_impact": _budgetary_values,
    "program_statistics": _program_values,
    "decile_impacts": _decile_values,
    "winners_losers": _winner_loser_values,
    "poverty": _poverty_values,
    "inequality": _inequality_values,
}


def validated_aggregate_values(
    output_id: str,
    payload: dict[str, object],
) -> tuple[AggregateValue, ...]:
    """Validate one derivative result and project its public aggregate values."""

    try:
        projector = _PROJECTORS[output_id]
    except KeyError as error:
        raise ValueError(f"Unsupported society output: {output_id}") from error
    return projector(payload)
