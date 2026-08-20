"""Single-source registry for model-facing chat tools.

This keeps registration explicit for now: handlers still pass hand-written
schemas and descriptions to `@register_tool`. A fuller automated registration
system could derive more from typed signatures or richer tool specs, but this
small layer only removes TOOL_DEFINITIONS / TOOL_HANDLERS drift.

Tool registration happens as an import side effect of `tools.dispatch`. Public
read accessors lazily import that module before deriving caller-owned snapshots,
so direct `tools.registry` callers see the same registered tools as the chat
runtime without being able to mutate the canonical registry by accident.
"""

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
    TypeAdapter,
    model_validator,
)


ToolHandler = Callable[..., dict[str, Any]]
ToolDefinition = dict[str, Any]


class MappingOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class ContractOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SuccessfulStatusOutput(ContractOutput):
    status: Literal["success"]


class ParameterOutput(SuccessfulStatusOutput):
    parameter: dict[str, Any]


class ReformValidationOutput(ContractOutput):
    valid: Literal[True]
    normalized_reform: dict[str, Any] = Field(exclude=True)
    reform_object: dict[str, Any] | None = Field(exclude=True)
    parameter_paths: list[StrictStr]
    warnings: list[Any]


class HouseholdValidationOutput(ContractOutput):
    valid: Literal[True]
    year: StrictInt
    people_count: StrictInt
    extra_variables_by_entity: dict[str, list[StrictStr]]
    normalized_reform: dict[str, Any] = Field(exclude=True)
    reform_object: dict[str, Any] | None = Field(exclude=True)
    warnings: list[Any]


class ResultHandleOutput(SuccessfulStatusOutput):
    result_id: str = Field(min_length=1)
    simulation_id: str | None = Field(default=None, exclude=True)


class HouseholdCalculationOutput(ContractOutput):
    person: list[dict[str, Any]]
    benunit: dict[str, Any]
    household: dict[str, Any]


class HouseholdSimulationOutput(ResultHandleOutput):
    year: StrictInt
    reform_applied: StrictBool
    person: list[dict[str, Any]] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    benunit: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    household: dict[str, Any] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    baseline: HouseholdCalculationOutput | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    reform: HouseholdCalculationOutput | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def validate_result_shape(self):
        direct = (self.person, self.benunit, self.household)
        if self.reform_applied:
            if any(value is not None for value in direct):
                raise ValueError(
                    "reformed household output cannot contain direct entity results"
                )
            if self.baseline is None or self.reform is None:
                raise ValueError(
                    "reformed household output requires baseline and reform results"
                )
        elif any(value is None for value in direct):
            raise ValueError(
                "baseline household output requires person, benunit, and household results"
            )
        elif self.baseline is not None or self.reform is not None:
            raise ValueError(
                "baseline household output cannot contain reform result containers"
            )
        return self


class SocietySimulationOutput(ResultHandleOutput):
    fiscal_year: StrictStr
    year: StrictInt
    dataset: dict[str, Any]
    reform_applied: StrictBool


StrictNumber = StrictInt | StrictFloat


class FiscalValues(ContractOutput):
    baseline: StrictNumber
    reform: StrictNumber
    change: StrictNumber


class BudgetaryImpactOutput(ResultHandleOutput):
    tax_revenue: FiscalValues
    benefit_spending: FiscalValues
    net_budgetary_impact: StrictNumber


class ProgramRow(ContractOutput):
    program: StrictStr
    entity: StrictStr
    is_tax: StrictBool
    baseline_total: StrictNumber
    reform_total: StrictNumber
    change: StrictNumber
    baseline_count: StrictNumber
    reform_count: StrictNumber
    winners: StrictNumber
    losers: StrictNumber


class ProgramBreakdownOutput(ResultHandleOutput):
    programs: list[ProgramRow]
    net_budgetary_impact: StrictNumber


class DecileImpactRow(ContractOutput):
    decile: StrictInt
    baseline_mean: StrictNumber
    reform_mean: StrictNumber
    absolute_change: StrictNumber
    relative_change: StrictNumber | None
    count_better_off: StrictNumber
    count_worse_off: StrictNumber
    count_no_change: StrictNumber


class DecileImpactsOutput(ResultHandleOutput):
    decile_concept: StrictStr
    basis: StrictStr
    income_variable: StrictStr
    decile_variable: StrictStr | None
    grouping_variable: StrictStr
    entity: StrictStr
    quantiles: StrictInt
    measure_label: StrictStr
    grouping_label: StrictStr
    deciles: list[DecileImpactRow]


class WinnersLosersRow(ContractOutput):
    decile: StrictInt
    lose_more_than_5pct: StrictNumber
    lose_less_than_5pct: StrictNumber
    no_change: StrictNumber
    gain_less_than_5pct: StrictNumber
    gain_more_than_5pct: StrictNumber


class WinnersLosersOutput(ResultHandleOutput):
    basis: Literal["income", "wealth"]
    grouping_label: StrictStr
    deciles: list[WinnersLosersRow]


class PovertyRateRow(ContractOutput):
    poverty_type: StrictStr
    group: StrictStr
    baseline_rate: StrictNumber
    reform_rate: StrictNumber
    rate_change: StrictNumber
    relative_change: StrictNumber | None
    baseline_headcount: StrictNumber
    reform_headcount: StrictNumber


class PovertyMetricsOutput(ResultHandleOutput):
    rates: list[PovertyRateRow]


class InequalityValues(ContractOutput):
    baseline: StrictNumber
    reform: StrictNumber
    change: StrictNumber
    relative_change: StrictNumber | None


class InequalityMetricsOutput(ResultHandleOutput):
    metrics: dict[str, InequalityValues]


class AggregateValue(ContractOutput):
    target: Literal["baseline", "reform", "change"]
    entity: StrictStr
    variable: StrictStr
    operation: Literal["sum", "mean", "count"]
    value: StrictNumber


class AggregateResultOutput(ResultHandleOutput):
    result: AggregateValue
    privacy: StrictStr


class ChartOutput(ContractOutput):
    status: Literal["success"]
    chart_markdown: str
    spec: dict[str, Any]
    message: StrictStr


def default_fact_extractor(value: dict[str, Any]) -> dict[str, Any]:
    return default_public_summary(value)


def parameter_fact_extractor(value: dict[str, Any]) -> dict[str, Any]:
    """Project only a parameter's current numerical value into narration facts."""

    parameter = value.get("parameter")
    if not isinstance(parameter, dict):
        return {}
    current_value = parameter.get("value")
    if isinstance(current_value, bool) or not isinstance(
        current_value, (int, float)
    ):
        return {}
    path = str(parameter.get("path") or "")
    label = str(parameter.get("label") or path.rsplit(".", 1)[-1] or "parameter")
    fact_name = re.sub(r"[^a-z0-9]+", "_", label.casefold()).strip("_")
    unit = str(parameter.get("unit") or "").casefold()
    if "currency" in unit or path.endswith(".amount"):
        suffix = "amount"
    elif "percent" in unit or path.endswith(".rate"):
        suffix = "rate"
    else:
        suffix = "value"
    if not fact_name.endswith(f"_{suffix}") and fact_name != suffix:
        fact_name = f"{fact_name}_{suffix}"
    return {fact_name: current_value}


def default_public_summary(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key
        not in {
            "result_id",
            "simulation_id",
            "normalized_reform",
            "reform_object",
        }
    }


@dataclass(frozen=True)
class RegisteredTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    input_adapter: TypeAdapter[Any]
    handler: ToolHandler
    output_adapter: TypeAdapter[Any]
    result_type: str
    fact_extractor: Callable[[dict[str, Any]], dict[str, Any]]
    public_summary_builder: Callable[[dict[str, Any]], dict[str, Any]]
    permitted_dependency_types: tuple[str, ...]


_TOOL_SPECS: list[RegisteredTool] = []


def _input_adapter(
    name: str,
    input_schema: dict[str, Any],
) -> TypeAdapter[dict[str, Any]]:
    """Build the registered typed adapter from the complete public JSON schema."""

    Draft202012Validator.check_schema(input_schema)
    validator = Draft202012Validator(input_schema)

    def validate(value: Any) -> dict[str, Any]:
        errors = sorted(
            validator.iter_errors(value),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            error = errors[0]
            location = ".".join(str(part) for part in error.absolute_path)
            path = f"{name}.{location}" if location else name
            raise ValueError(f"{path}: {error.message}")
        return value

    return TypeAdapter(
        Annotated[dict[str, Any], BeforeValidator(validate)]
    )


def register_tool(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    output_model: Any = MappingOutput,
    result_type: str | None = None,
    fact_extractor: Callable[[dict[str, Any]], dict[str, Any]] = default_fact_extractor,
    public_summary_builder: Callable[
        [dict[str, Any]], dict[str, Any]
    ] = default_public_summary,
    permitted_dependency_types: tuple[str, ...] = (),
) -> Callable[[ToolHandler], ToolHandler]:
    """Register one model-facing tool and derive schema + dispatch views."""

    def decorator(handler: ToolHandler) -> ToolHandler:
        if any(spec.name == name for spec in _TOOL_SPECS):
            raise ValueError(f"Duplicate tool registration: {name}")

        _TOOL_SPECS.append(
            RegisteredTool(
                name=name,
                description=description,
                input_schema=deepcopy(input_schema),
                input_adapter=_input_adapter(name, input_schema),
                handler=handler,
                output_adapter=TypeAdapter(output_model),
                result_type=result_type or name,
                fact_extractor=fact_extractor,
                public_summary_builder=public_summary_builder,
                permitted_dependency_types=permitted_dependency_types,
            )
        )
        return handler

    return decorator


def _ensure_registered() -> None:
    # Importing dispatch runs the decorators that populate this registry.
    import tools.dispatch  # noqa: F401


def tool_specs() -> tuple[RegisteredTool, ...]:
    """Return caller-owned snapshots of registered tool specs in model order."""

    _ensure_registered()
    return tuple(
        RegisteredTool(
            name=spec.name,
            description=spec.description,
            input_schema=deepcopy(spec.input_schema),
            input_adapter=spec.input_adapter,
            handler=spec.handler,
            output_adapter=spec.output_adapter,
            result_type=spec.result_type,
            fact_extractor=spec.fact_extractor,
            public_summary_builder=spec.public_summary_builder,
            permitted_dependency_types=spec.permitted_dependency_types,
        )
        for spec in _TOOL_SPECS
    )


def tool_definitions() -> list[ToolDefinition]:
    """Return fresh JSON-like tool-definition snapshots in model order.

    Callers may mutate the returned list/dicts for a local model/eval request,
    but mutation is not a registration mechanism and does not affect future
    registry reads. Register or remove model-facing tools only through
    `@register_tool`.
    """

    _ensure_registered()
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": deepcopy(spec.input_schema),
        }
        for spec in _TOOL_SPECS
    ]


def tool_handlers() -> Mapping[str, ToolHandler]:
    """Return a read-only handler mapping in model order."""

    _ensure_registered()
    return MappingProxyType({spec.name: spec.handler for spec in _TOOL_SPECS})
