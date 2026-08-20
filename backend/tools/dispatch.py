"""Model-facing tools for the UK chat runtime."""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Dict, List, Optional

from engine import derivatives, discovery
from engine.decile_concepts import DEFAULT_DECILE_CONCEPT
from engine.households import calculate_household, validate_household_dict
from engine.reforms import normalize_reform_dict, validate_reform_dict
from engine.serialization import explore_tabular_data, json_safe
from engine.simulations import (
    SocietySimulationRun,
    build_society_simulation,
)
from tools.context import ToolExecutionContext
from tools.definitions import (
    AGGREGATE_RESULT_INPUT_SCHEMA,
    COMPUTE_BUDGETARY_IMPACT_INPUT_SCHEMA,
    COMPUTE_DECILE_IMPACTS_INPUT_SCHEMA,
    COMPUTE_INEQUALITY_METRICS_INPUT_SCHEMA,
    COMPUTE_POVERTY_METRICS_INPUT_SCHEMA,
    COMPUTE_PROGRAM_BREAKDOWN_INPUT_SCHEMA,
    COMPUTE_WINNERS_LOSERS_INPUT_SCHEMA,
    CHART_EXPLICIT_DATA_KINDS,
    CHART_GENERIC_KINDS,
    CHART_PRESET_SOURCES,
    DECILE_IMPACTS_DESCRIPTION,
    DEFAULT_SIMULATION_YEAR,
    DERIVATIVE_DESCRIPTION,
    DISCOVERY_DESCRIPTION,
    GENERATE_CHART_DESCRIPTION,
    GENERATE_CHART_INPUT_SCHEMA,
    GET_PARAMETER_INPUT_SCHEMA,
    GET_VARIABLE_DESCRIPTION,
    GET_VARIABLE_INPUT_SCHEMA,
    LIST_ENTITIES_INPUT_SCHEMA,
    LIST_HOUSEHOLD_INPUT_VARIABLES_INPUT_SCHEMA,
    LIST_REFORM_TARGETS_INPUT_SCHEMA,
    LIST_SOCIETY_OUTPUT_VARIABLES_DESCRIPTION,
    LIST_SOCIETY_OUTPUT_VARIABLES_INPUT_SCHEMA,
    LIST_SUPPORTED_OUTPUTS_INPUT_SCHEMA,
    RUN_HOUSEHOLD_SIMULATION_DESCRIPTION,
    RUN_HOUSEHOLD_SIMULATION_INPUT_SCHEMA,
    RUN_SOCIETY_SIMULATION_DESCRIPTION,
    RUN_SOCIETY_SIMULATION_INPUT_SCHEMA,
    SEARCH_PARAMETERS_INPUT_SCHEMA,
    SEARCH_VARIABLES_DESCRIPTION,
    SEARCH_VARIABLES_INPUT_SCHEMA,
    VALIDATE_HOUSEHOLD_DESCRIPTION,
    VALIDATE_HOUSEHOLD_INPUT_SCHEMA,
    VALIDATE_REFORM_DESCRIPTION,
    VALIDATE_REFORM_INPUT_SCHEMA,
)
from tools.registry import (
    AggregateResultOutput,
    BudgetaryImpactOutput,
    ChartOutput,
    DecileImpactsOutput,
    HouseholdValidationOutput,
    HouseholdSimulationOutput,
    InequalityMetricsOutput,
    ParameterOutput,
    PovertyMetricsOutput,
    ProgramBreakdownOutput,
    ReformValidationOutput,
    SocietySimulationOutput,
    WinnersLosersOutput,
    parameter_fact_extractor,
    register_tool,
    tool_definitions,
    tool_handlers,
)

logger = logging.getLogger(__name__)

_json_safe = json_safe

__all__ = [
    "TOOL_DEFINITIONS",
    "TOOL_HANDLERS",
    "aggregate_result",
    "compute_budgetary_impact",
    "compute_decile_impacts",
    "compute_inequality_metrics",
    "compute_poverty_metrics",
    "compute_program_breakdown",
    "compute_winners_losers",
    "execute_tool",
    "explore_tabular_data",
    "generate_chart",
    "get_parameter",
    "get_variable",
    "list_entities",
    "list_household_input_variables",
    "list_reform_targets",
    "list_society_output_variables",
    "list_supported_outputs",
    "run_household_simulation",
    "run_society_simulation",
    "search_parameters",
    "search_variables",
    "validate_household",
    "validate_reform",
]


def _store(
    context: ToolExecutionContext | None,
    kind: str,
    payload: Any,
    summary: dict[str, Any],
) -> str:
    if context is None:
        raise RuntimeError("Result-producing tools require a shared tool execution context.")
    return context.result_store.put(
        kind,
        payload,
        summary,
        execution_id=context.execution_id,
        source_step_id=context.active_step_id,
    )


def _get_stored(context: ToolExecutionContext | None, result_id: str, expected: str | tuple[str, ...]):
    if context is None:
        raise KeyError("Tool result handles are only available within a chat turn.")
    return context.result_store.get(
        result_id,
        expected,
        execution_id=context.execution_id,
    )


@register_tool(name="list_entities", description=DISCOVERY_DESCRIPTION, input_schema=LIST_ENTITIES_INPUT_SCHEMA)
def list_entities() -> Dict[str, Any]:
    return discovery.list_entities()


@register_tool(
    name="search_variables",
    description=SEARCH_VARIABLES_DESCRIPTION,
    input_schema=SEARCH_VARIABLES_INPUT_SCHEMA,
)
def search_variables(query: str = "", entity: str | None = None, limit: int = 25) -> Dict[str, Any]:
    return discovery.search_variables(query=query, entity=entity, limit=limit)


@register_tool(
    name="get_variable",
    description=GET_VARIABLE_DESCRIPTION,
    input_schema=GET_VARIABLE_INPUT_SCHEMA,
)
def get_variable(name: str) -> Dict[str, Any]:
    return discovery.get_variable(name)


@register_tool(name="search_parameters", description=DISCOVERY_DESCRIPTION, input_schema=SEARCH_PARAMETERS_INPUT_SCHEMA)
def search_parameters(query: str = "", limit: int = 25) -> Dict[str, Any]:
    return discovery.search_parameters(query=query, limit=limit)


@register_tool(
    name="get_parameter",
    description=DISCOVERY_DESCRIPTION,
    input_schema=GET_PARAMETER_INPUT_SCHEMA,
    output_model=ParameterOutput,
    result_type="parameter",
    fact_extractor=parameter_fact_extractor,
)
def get_parameter(path: str, year: int = DEFAULT_SIMULATION_YEAR) -> Dict[str, Any]:
    return discovery.get_parameter(path=path, year=year)


@register_tool(name="list_reform_targets", description=DISCOVERY_DESCRIPTION, input_schema=LIST_REFORM_TARGETS_INPUT_SCHEMA)
def list_reform_targets(query: str = "", limit: int = 25) -> Dict[str, Any]:
    return discovery.list_reform_targets(query=query, limit=limit)


@register_tool(name="list_household_input_variables", description=DISCOVERY_DESCRIPTION, input_schema=LIST_HOUSEHOLD_INPUT_VARIABLES_INPUT_SCHEMA)
def list_household_input_variables(entity: str | None = None) -> Dict[str, Any]:
    return discovery.list_household_input_variables(entity=entity)


@register_tool(
    name="list_society_output_variables",
    description=LIST_SOCIETY_OUTPUT_VARIABLES_DESCRIPTION,
    input_schema=LIST_SOCIETY_OUTPUT_VARIABLES_INPUT_SCHEMA,
)
def list_society_output_variables(entity: str | None = None) -> Dict[str, Any]:
    return discovery.list_society_output_variables(entity=entity)


@register_tool(name="list_supported_outputs", description=DISCOVERY_DESCRIPTION, input_schema=LIST_SUPPORTED_OUTPUTS_INPUT_SCHEMA)
def list_supported_outputs(scope: str | None = None) -> Dict[str, Any]:
    return discovery.list_supported_outputs(scope=scope)


@register_tool(
    name="validate_reform",
    description=VALIDATE_REFORM_DESCRIPTION,
    input_schema=VALIDATE_REFORM_INPUT_SCHEMA,
    output_model=ReformValidationOutput,
    result_type="reform_validation",
)
def validate_reform(reform: Optional[Dict[str, Any]] = None, year: int = DEFAULT_SIMULATION_YEAR) -> Dict[str, Any]:
    return validate_reform_dict(reform, year=year)


@register_tool(
    name="validate_household",
    description=VALIDATE_HOUSEHOLD_DESCRIPTION,
    input_schema=VALIDATE_HOUSEHOLD_INPUT_SCHEMA,
    output_model=HouseholdValidationOutput,
    result_type="household_validation",
)
def validate_household(
    people: list[dict[str, Any]],
    benunit: Optional[Dict[str, Any]] = None,
    household: Optional[Dict[str, Any]] = None,
    year: int = DEFAULT_SIMULATION_YEAR,
    reform: Optional[Dict[str, Any]] = None,
    extra_variables: Optional[list[str]] = None,
) -> Dict[str, Any]:
    return validate_household_dict(
        people=people,
        benunit=benunit,
        household=household,
        year=year,
        reform=reform,
        extra_variables=extra_variables,
    )


@register_tool(
    name="run_household_simulation",
    description=RUN_HOUSEHOLD_SIMULATION_DESCRIPTION,
    input_schema=RUN_HOUSEHOLD_SIMULATION_INPUT_SCHEMA,
    output_model=HouseholdSimulationOutput,
    result_type="household_simulation",
)
def run_household_simulation(
    people: list[dict[str, Any]],
    benunit: Optional[Dict[str, Any]] = None,
    household: Optional[Dict[str, Any]] = None,
    year: int = DEFAULT_SIMULATION_YEAR,
    reform: Optional[Dict[str, Any]] = None,
    extra_variables: Optional[list[str]] = None,
    _context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    result = calculate_household(
        people=people,
        benunit=benunit,
        household=household,
        year=year,
        reform=reform,
        extra_variables=extra_variables,
    )
    if "error" not in result:
        result_id = _store(_context, "household_simulation", result, result)
        result["result_id"] = result_id
    return result


@register_tool(
    name="run_society_simulation",
    description=RUN_SOCIETY_SIMULATION_DESCRIPTION,
    input_schema=RUN_SOCIETY_SIMULATION_INPUT_SCHEMA,
    output_model=SocietySimulationOutput,
    result_type="society_simulation",
)
def run_society_simulation(
    year: int = DEFAULT_SIMULATION_YEAR,
    reform: Optional[Dict[str, Any]] = None,
    extra_variables: Optional[Dict[str, List[str]]] = None,
    _context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    if _context is not None and _context.require_approved_reform:
        try:
            submitted = normalize_reform_dict(reform)
            approved = normalize_reform_dict(_context.approved_reform)
        except Exception as exc:
            return {
                "error": "Plan-approved reform mismatch",
                "detail": str(exc),
            }
        if submitted != approved:
            return {
                "error": "Plan-approved reform mismatch",
                "detail": (
                    "The submitted society reform did not exactly match the "
                    "validated reform recorded in the current execution plan."
                ),
            }
    try:
        payload = build_society_simulation(
            year=year,
            reform=reform,
            extra_variables=extra_variables,
        )
    except FileNotFoundError as exc:
        return {"error": "Dataset file not available", "detail": str(exc)}
    except Exception as exc:
        logger.exception("Error in run_society_simulation")
        return {"error": f"{type(exc).__name__}: {exc}"}
    result = payload.metadata()
    result_id = _store(_context, "society_simulation", payload, result)
    result["result_id"] = result_id
    return result


def _society_payload(context: ToolExecutionContext | None, simulation_id: str) -> SocietySimulationRun:
    return _get_stored(context, simulation_id, "society_simulation").payload


def _derivative_result(
    context: ToolExecutionContext | None,
    kind: str,
    summary: dict[str, Any],
) -> dict[str, Any]:
    result = dict(summary)
    result["result_id"] = _store(context, kind, result, summary)
    return result


@register_tool(
    name="compute_budgetary_impact",
    description=DERIVATIVE_DESCRIPTION,
    input_schema=COMPUTE_BUDGETARY_IMPACT_INPUT_SCHEMA,
    output_model=BudgetaryImpactOutput,
    result_type="budgetary_impact",
    permitted_dependency_types=("society_simulation",),
)
def compute_budgetary_impact(simulation_id: str, _context: ToolExecutionContext | None = None) -> Dict[str, Any]:
    payload = _society_payload(_context, simulation_id)
    summary = {
        "status": "success",
        "simulation_id": simulation_id,
        **derivatives.budgetary_impact(payload),
    }
    return _derivative_result(_context, "budgetary_impact", summary)


@register_tool(
    name="compute_program_breakdown",
    description=DERIVATIVE_DESCRIPTION,
    input_schema=COMPUTE_PROGRAM_BREAKDOWN_INPUT_SCHEMA,
    output_model=ProgramBreakdownOutput,
    result_type="program_breakdown",
    permitted_dependency_types=("society_simulation",),
)
def compute_program_breakdown(
    simulation_id: str,
    programs: Optional[list[str]] = None,
    _context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    payload = _society_payload(_context, simulation_id)
    summary = {
        "status": "success",
        "simulation_id": simulation_id,
        **derivatives.program_breakdown(payload, programs=programs),
    }
    return _derivative_result(_context, "program_breakdown", summary)


@register_tool(
    name="compute_decile_impacts",
    description=DECILE_IMPACTS_DESCRIPTION,
    input_schema=COMPUTE_DECILE_IMPACTS_INPUT_SCHEMA,
    output_model=DecileImpactsOutput,
    result_type="decile_impacts",
    permitted_dependency_types=("society_simulation",),
)
def compute_decile_impacts(
    simulation_id: str,
    decile_concept: str = DEFAULT_DECILE_CONCEPT.value,
    _context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    payload = _society_payload(_context, simulation_id)
    summary = {
        "status": "success",
        "simulation_id": simulation_id,
        **derivatives.decile_impacts(
            payload,
            decile_concept=decile_concept,
        ),
    }
    return _derivative_result(_context, "decile_impacts", summary)


@register_tool(
    name="compute_winners_losers",
    description=DERIVATIVE_DESCRIPTION,
    input_schema=COMPUTE_WINNERS_LOSERS_INPUT_SCHEMA,
    output_model=WinnersLosersOutput,
    result_type="winners_losers",
    permitted_dependency_types=("society_simulation",),
)
def compute_winners_losers(
    simulation_id: str,
    basis: str = "income",
    _context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    payload = _society_payload(_context, simulation_id)
    summary = {
        "status": "success",
        "simulation_id": simulation_id,
        **derivatives.winners_losers(payload, basis=basis),
    }
    return _derivative_result(_context, "winners_losers", summary)


@register_tool(
    name="compute_poverty_metrics",
    description=DERIVATIVE_DESCRIPTION,
    input_schema=COMPUTE_POVERTY_METRICS_INPUT_SCHEMA,
    output_model=PovertyMetricsOutput,
    result_type="poverty_metrics",
    permitted_dependency_types=("society_simulation",),
)
def compute_poverty_metrics(simulation_id: str, _context: ToolExecutionContext | None = None) -> Dict[str, Any]:
    payload = _society_payload(_context, simulation_id)
    summary = {
        "status": "success",
        "simulation_id": simulation_id,
        **derivatives.poverty_metrics(payload),
    }
    return _derivative_result(_context, "poverty_metrics", summary)


@register_tool(
    name="compute_inequality_metrics",
    description=DERIVATIVE_DESCRIPTION,
    input_schema=COMPUTE_INEQUALITY_METRICS_INPUT_SCHEMA,
    output_model=InequalityMetricsOutput,
    result_type="inequality_metrics",
    permitted_dependency_types=("society_simulation",),
)
def compute_inequality_metrics(simulation_id: str, _context: ToolExecutionContext | None = None) -> Dict[str, Any]:
    payload = _society_payload(_context, simulation_id)
    summary = {
        "status": "success",
        "simulation_id": simulation_id,
        **derivatives.inequality_metrics(payload),
    }
    return _derivative_result(_context, "inequality_metrics", summary)


@register_tool(
    name="aggregate_result",
    description=DERIVATIVE_DESCRIPTION,
    input_schema=AGGREGATE_RESULT_INPUT_SCHEMA,
    output_model=AggregateResultOutput,
    result_type="aggregate_result",
    permitted_dependency_types=("society_simulation",),
)
def aggregate_result(
    simulation_id: str,
    entity: str,
    variable: str,
    operation: str,
    target: str = "reform",
    filter_variable: str | None = None,
    filter_variable_eq: Any = None,
    filter_variable_leq: Any = None,
    filter_variable_geq: Any = None,
    _context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    payload = _society_payload(_context, simulation_id)
    summary = {
        "status": "success",
        "simulation_id": simulation_id,
        "result": derivatives.aggregate_result(
            payload,
            target=target,
            entity=entity,
            variable=variable,
            operation=operation,
            filter_variable=filter_variable,
            filter_variable_eq=filter_variable_eq,
            filter_variable_leq=filter_variable_leq,
            filter_variable_geq=filter_variable_geq,
        ),
        "privacy": "Aggregate only; no row-level records returned.",
    }
    return _derivative_result(_context, "aggregate", summary)


def _chart_markdown(spec: dict[str, Any]) -> Dict[str, Any]:
    return {
        "status": "success",
        "chart_markdown": f"```chart\n{json.dumps(json_safe(spec), indent=2)}\n```",
        "spec": json_safe(spec),
        "message": "Chart generated. Include chart_markdown verbatim in the response.",
    }


def _generic_chart_spec(
    chart_kind: str,
    title: str | None,
    subtitle: str | None,
    source: str | None,
    data: Any,
    x_field: str | None,
    y_fields: list[str] | None,
    x_label: str | None,
    y_label: str | None,
    x_format: str | None,
    y_format: str | None,
    x_min: float | None,
    x_max: float | None,
    y_min: float | None,
    y_max: float | None,
    series_labels: list[str] | None,
    series_styles: list[str] | None,
    series_curves: list[str] | None,
    arrangement: str | None,
) -> dict[str, Any]:
    chart_type = chart_kind.removeprefix("generic_")
    y_fields = y_fields or []
    return {
        "type": chart_type,
        "title": title,
        "subtitle": subtitle,
        "source": source,
        "x": {
            "field": x_field,
            "label": x_label or x_field,
            **({"format": x_format} if x_format else {}),
            **({"min": x_min} if x_min is not None else {}),
            **({"max": x_max} if x_max is not None else {}),
        },
        "y": {
            "field": y_fields[0] if len(y_fields) == 1 else "value",
            "label": y_label or (y_fields[0] if y_fields else "Value"),
            **({"format": y_format} if y_format else {}),
            **({"min": y_min} if y_min is not None else {}),
            **({"max": y_max} if y_max is not None else {}),
        },
        "series": [
            {
                "field": field,
                "label": series_labels[index] if series_labels and index < len(series_labels) else field,
                **(
                    {"lineStyle": series_styles[index]}
                    if series_styles and index < len(series_styles)
                    else {}
                ),
                **(
                    {"curve": series_curves[index]}
                    if series_curves and index < len(series_curves)
                    else {}
                ),
            }
            for index, field in enumerate(y_fields)
        ],
        "data": data or [],
        "showLegend": len(y_fields) > 1,
        "showGrid": True,
        **({"arrangement": arrangement} if arrangement else {}),
    }


def _preset_chart_data(chart_kind: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    if chart_kind == "budget_waterfall":
        return [
            {"label": "Tax revenue", "value": result["tax_revenue"]["change"]},
            {"label": "Benefit spending", "value": -result["benefit_spending"]["change"]},
            {
                "label": "Net impact",
                "value": result["net_budgetary_impact"],
                "total": True,
            },
        ]
    if chart_kind == "program_budget_waterfall":
        return [
            {
                "label": row["program"].replace("_", " ").title(),
                "value": row["change"] if row["is_tax"] else -row["change"],
            }
            for row in result["programs"]
            if row["change"] != 0
        ] + [
            {
                "label": "Total",
                "value": result["net_budgetary_impact"],
                "total": True,
            }
        ]
    if chart_kind in {"decile_absolute_bar", "decile_relative_bar"}:
        field = (
            "absolute_change"
            if chart_kind == "decile_absolute_bar"
            else "relative_change"
        )
        return [
            {"label": str(row["decile"]), "value": row[field]}
            for row in result["deciles"]
        ]
    if chart_kind == "winners_losers_stacked_bar":
        return list(result["deciles"])
    if chart_kind == "poverty_relative_bar":
        return [
            {"label": row["group"], "value": row["relative_change"] * 100}
            for row in result["rates"]
            if row["poverty_type"] == "relative_bhc"
            and row["relative_change"] is not None
        ]
    if chart_kind == "inequality_relative_bar":
        return [
            {
                "label": metric.replace("_share", "").replace("_", " ").title(),
                "value": values["relative_change"] * 100,
            }
            for metric, values in result["metrics"].items()
            if values["relative_change"] is not None
        ]
    return result if isinstance(result, list) else []


@register_tool(
    name="generate_chart",
    description=GENERATE_CHART_DESCRIPTION,
    input_schema=GENERATE_CHART_INPUT_SCHEMA,
    output_model=ChartOutput,
    result_type="chart",
    permitted_dependency_types=(
        "budgetary_impact",
        "program_breakdown",
        "decile_impacts",
        "winners_losers",
        "poverty_metrics",
        "inequality_metrics",
        "aggregate_result",
    ),
)
def generate_chart(
    chart_kind: str,
    result_id: str | None = None,
    data: Any = None,
    title: str | None = None,
    subtitle: str | None = None,
    source: str | None = None,
    x_field: str | None = None,
    y_fields: Optional[list[str]] = None,
    x_label: str | None = None,
    y_label: str | None = None,
    x_format: str | None = None,
    y_format: str | None = None,
    x_min: float | None = None,
    x_max: float | None = None,
    y_min: float | None = None,
    y_max: float | None = None,
    series_labels: Optional[list[str]] = None,
    series_styles: Optional[list[str]] = None,
    series_curves: Optional[list[str]] = None,
    arrangement: str | None = None,
    _context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    if chart_kind not in (
        set(CHART_GENERIC_KINDS)
        | set(CHART_PRESET_SOURCES)
        | set(CHART_EXPLICIT_DATA_KINDS)
    ):
        return {"error": f"Unknown chart kind: {chart_kind}"}
    if chart_kind in CHART_GENERIC_KINDS:
        if result_id:
            stored = _get_stored(
                _context,
                result_id,
                (
                    "aggregate",
                    "budgetary_impact",
                    "program_breakdown",
                    "decile_impacts",
                    "winners_losers",
                    "poverty_metrics",
                    "inequality_metrics",
                    "household_simulation",
                ),
            )
            data = data if data is not None else stored.summary
        if not x_field or not y_fields:
            return {"error": "Generic charts require x_field and y_fields."}
        if not isinstance(data, list) or not data:
            return {"error": "Generic chart data must contain at least one row."}
        return _chart_markdown(
            _generic_chart_spec(
                chart_kind,
                title,
                subtitle,
                source,
                data,
                x_field,
                y_fields,
                x_label,
                y_label,
                x_format,
                y_format,
                x_min,
                x_max,
                y_min,
                y_max,
                series_labels,
                series_styles,
                series_curves,
                arrangement,
            )
        )
    preset_source = CHART_PRESET_SOURCES.get(chart_kind)
    expected_kind = preset_source[2] if preset_source is not None else None
    preset_metadata: dict[str, Any] = {}
    if expected_kind is not None:
        if not result_id:
            return {
                "error": (
                    f"{chart_kind} requires a result_id from a "
                    f"{expected_kind} derivative tool."
                )
            }
        stored = _get_stored(_context, result_id, expected_kind)
        data = _preset_chart_data(chart_kind, stored.summary)
        if expected_kind == "decile_impacts":
            preset_metadata = {
                "measureLabel": stored.summary["measure_label"],
                "groupLabel": stored.summary["grouping_label"],
            }
        elif expected_kind == "winners_losers":
            preset_metadata = {
                "groupLabel": stored.summary["grouping_label"],
            }
    elif data is None:
        return {"error": f"{chart_kind} requires chart data."}
    spec = {
        "type": "preset",
        "preset": chart_kind,
        "title": title,
        "subtitle": subtitle,
        "data": data,
        "source": source or "PolicyEngine UK via policyengine.py",
        **preset_metadata,
    }
    return _chart_markdown(spec)


TOOL_DEFINITIONS = tool_definitions()
TOOL_HANDLERS = tool_handlers()


def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    context: ToolExecutionContext | None = None,
) -> Dict[str, Any]:
    logger.info(f"[TOOLS] Executing {tool_name}")
    if tool_name not in TOOL_HANDLERS:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        handler = TOOL_HANDLERS[tool_name]
        if "_context" in inspect.signature(handler).parameters:
            result = handler(**tool_input, _context=context)
        else:
            result = handler(**tool_input)
        logger.info(f"[TOOLS] {tool_name} completed")
        return result
    except Exception as exc:
        logger.error(f"[TOOLS] Error in {tool_name}: {exc}")
        return {"error": str(exc)}
