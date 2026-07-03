"""
Agent tools for the microsim chatbot.

This module owns the public LLM-facing tool functions and dispatcher.
Tool schemas live in backend/tools/definitions.py; shared deterministic helpers
live under backend/engine.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from tools.definitions import (
    ANALYSE_MICRODATA_DESCRIPTION,
    ANALYSE_MICRODATA_INPUT_SCHEMA,
    CALCULATE_HOUSEHOLD_DESCRIPTION,
    CALCULATE_HOUSEHOLD_INPUT_SCHEMA,
    DEFAULT_SIMULATION_YEAR,
    GENERATE_CHART_DESCRIPTION,
    GENERATE_CHART_INPUT_SCHEMA,
    LOOKUP_PARAMETER_DESCRIPTION,
    LOOKUP_PARAMETER_INPUT_SCHEMA,
    RUN_ECONOMY_SIMULATION_DESCRIPTION,
    RUN_ECONOMY_SIMULATION_INPUT_SCHEMA,
    RUN_PYTHON_DESCRIPTION,
    RUN_PYTHON_INPUT_SCHEMA,
    VALIDATE_REFORM_DESCRIPTION,
    VALIDATE_REFORM_INPUT_SCHEMA,
)
from engine.households import build_household_frames
from engine.lookups import lookup_parameter_metadata
from engine.microdata import analyse_microdata_result, get_cached_microdata, hash_reform
from engine.reforms import build_compiled_policy, validate_reform_dict
from engine.sandbox import (
    run_generator,
    run_python_code,
    safe_import,
)
from engine.serialization import dataframe_to_records, explore_tabular_data, json_safe
from engine.simulations import DATASET_LABELS, build_simulation, ensure_compiled_package_importable
from engine.simulations import get_capabilities as _engine_capabilities
from tools.registry import register_tool, tool_definitions, tool_handlers

logger = logging.getLogger(__name__)

# Compatibility aliases for tests and existing imports. They remain internal
# unless registered with @register_tool.
_ensure_compiled_package_importable = ensure_compiled_package_importable
_safe_import = safe_import
_json_safe = json_safe
_hash_reform = hash_reform
_get_cached_microdata = get_cached_microdata
_build_compiled_policy = build_compiled_policy
_build_simulation = build_simulation
_run_generator = run_generator

__all__ = [
    "TOOL_DEFINITIONS",
    "TOOL_HANDLERS",
    "analyse_microdata",
    "calculate_household",
    "execute_tool",
    "explore_tabular_data",
    "generate_chart",
    "get_baseline_parameters",
    "get_capabilities",
    "lookup_parameter",
    "run_economy_simulation",
    "run_python",
    "validate_reform",
]


def get_capabilities() -> Dict[str, Any]:
    try:
        return _engine_capabilities()
    except Exception as exc:
        logger.error(f"Error getting capabilities: {exc}")
        return {"error": str(exc)}


def get_baseline_parameters(year: int = DEFAULT_SIMULATION_YEAR) -> Dict[str, Any]:
    try:
        _ensure_compiled_package_importable()
        from policyengine_uk_compiled import Simulation

        # The engine requires an explicit data source since 0.45, but
        # get_baseline_params() only shells out for parameters; no data loads.
        sim = Simulation(year=year, dataset="frs")
        return {"year": year, "parameters": sim.get_baseline_params()}
    except Exception as exc:
        logger.error(f"Error getting baseline parameters: {exc}")
        return {"error": str(exc)}


@register_tool(
    name="validate_reform",
    description=VALIDATE_REFORM_DESCRIPTION,
    input_schema=VALIDATE_REFORM_INPUT_SCHEMA,
)
def validate_reform(reform: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate parametric reform JSON without running a simulation."""
    return validate_reform_dict(reform)


@register_tool(
    name="calculate_household",
    description=CALCULATE_HOUSEHOLD_DESCRIPTION,
    input_schema=CALCULATE_HOUSEHOLD_INPUT_SCHEMA,
)
def calculate_household(
    person: List[Dict[str, Any]],
    benunit: List[Dict[str, Any]],
    household: List[Dict[str, Any]],
    year: int = DEFAULT_SIMULATION_YEAR,
    reform: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        _ensure_compiled_package_importable()
        from policyengine_uk_compiled import Simulation

        persons_df, benunits_df, households_df = build_household_frames(person, benunit, household)
        sim = Simulation(year=year, persons=persons_df, benunits=benunits_df, households=households_df)
        policy = _build_compiled_policy(reform)
        result = sim.run_microdata(policy=policy)

        return {
            "status": "success",
            "year": year,
            "reform_applied": reform is not None,
            "person": dataframe_to_records(result.persons),
            "benunit": dataframe_to_records(result.benunits),
            "household": dataframe_to_records(result.households),
        }
    except Exception as exc:
        logger.error(f"Error in calculate_household: {exc}")
        import traceback

        logger.error(traceback.format_exc())
        return {"error": str(exc)}


@register_tool(
    name="run_economy_simulation",
    description=RUN_ECONOMY_SIMULATION_DESCRIPTION,
    input_schema=RUN_ECONOMY_SIMULATION_INPUT_SCHEMA,
)
def run_economy_simulation(
    year: int = DEFAULT_SIMULATION_YEAR,
    reform: Optional[Dict[str, Any]] = None,
    dataset: str = "frs",
) -> Dict[str, Any]:
    # Structural reforms are intentionally run_python-only; this tool covers
    # parametric reforms.
    try:
        policy = _build_compiled_policy(reform)
        sim = _build_simulation(year, dataset)
        baseline_result = sim.run()
        reform_result = sim.run(policy=policy) if policy is not None else baseline_result

        baseline_breakdown = baseline_result.program_breakdown.model_dump()
        reform_breakdown = reform_result.program_breakdown.model_dump()
        program_changes = {
            key: {
                "baseline": baseline_breakdown[key],
                "reform": reform_breakdown[key],
                "change": reform_breakdown[key] - baseline_breakdown[key],
            }
            for key in baseline_breakdown
        }

        return {
            "fiscal_year": reform_result.fiscal_year,
            "dataset": DATASET_LABELS.get(dataset, dataset),
            "budgetary_impact": reform_result.budgetary_impact.model_dump(),
            "program_breakdown_changes": program_changes,
            "decile_impacts": [d.model_dump() for d in reform_result.decile_impacts],
            "winners_losers": reform_result.winners_losers.model_dump(),
            "caseloads": reform_result.caseloads.model_dump(),
            "baseline_hbai_incomes": baseline_result.baseline_hbai_incomes.model_dump(),
            "reform_hbai_incomes": reform_result.reform_hbai_incomes.model_dump(),
            "baseline_poverty": baseline_result.baseline_poverty.model_dump(),
            "reform_poverty": reform_result.reform_poverty.model_dump(),
        }
    except FileNotFoundError as exc:
        return {
            "error": f"{dataset.upper()} microdata not available",
            "detail": str(exc),
            "hint": "Ensure POLICYENGINE_UK_DATA_TOKEN is set.",
        }
    except Exception as exc:
        logger.error(f"Error in run_economy_simulation: {exc}")
        import traceback

        logger.error(traceback.format_exc())
        return {"error": str(exc)}


@register_tool(
    name="analyse_microdata",
    description=ANALYSE_MICRODATA_DESCRIPTION,
    input_schema=ANALYSE_MICRODATA_INPUT_SCHEMA,
)
def analyse_microdata(
    entity: str,
    operation: str,
    year: int = DEFAULT_SIMULATION_YEAR,
    reform: Optional[Dict[str, Any]] = None,
    filters: Optional[Dict[str, Any]] = None,
    columns: Optional[List[str]] = None,
    group_by: Optional[List[str]] = None,
    n: int = 5,
    dataset: str = "efrs",
) -> Dict[str, Any]:
    try:
        dataset_key = (dataset or "").lower()
        if dataset_key == "frs":
            return {
                "error": "analyse_microdata does not support FRS row-level access",
                "hint": (
                    "Use run_economy_simulation for aggregate FRS outputs, or choose "
                    "a non-FRS dataset for analyse_microdata."
                ),
            }
        # Enhanced FRS rows derive from FRS respondents, so the FRS row-level
        # restriction extends to sampling efrs.
        if dataset_key == "efrs" and operation == "sample":
            return {
                "error": "analyse_microdata does not support row-level sampling of the Enhanced FRS",
                "hint": (
                    "Use aggregate operations (mean, sum, count, group_by, describe) "
                    "on efrs, or sample a non-FRS-derived dataset (spi, lcfs, was)."
                ),
            }

        microdata = _get_cached_microdata(year, reform, dataset_key)

        return analyse_microdata_result(
            microdata=microdata,
            entity=entity,
            operation=operation,
            year=year,
            dataset_key=dataset_key,
            reform_applied=reform is not None,
            filters=filters,
            columns=columns,
            group_by=group_by,
            n=n,
        )
    except Exception as exc:
        logger.error(f"Error in analyse_microdata: {exc}")
        import traceback

        logger.error(traceback.format_exc())
        return {"error": str(exc)}


@register_tool(
    name="lookup_parameter",
    description=LOOKUP_PARAMETER_DESCRIPTION,
    input_schema=LOOKUP_PARAMETER_INPUT_SCHEMA,
)
def lookup_parameter(
    query: str,
    year: int = DEFAULT_SIMULATION_YEAR,
    limit: int = 5,
) -> Dict[str, Any]:
    """Look up baseline model parameter values by path or natural query."""
    try:
        _ensure_compiled_package_importable()
        from policyengine_uk_compiled import Simulation

        sim = Simulation(year=year, dataset="frs")
        return lookup_parameter_metadata(
            parameters=sim.get_baseline_params(),
            query=query,
            year=year,
            limit=limit,
        )
    except Exception as exc:
        logger.error(f"Error looking up parameter: {exc}")
        return {"error": str(exc)}


@register_tool(
    name="run_python",
    description=RUN_PYTHON_DESCRIPTION,
    input_schema=RUN_PYTHON_INPUT_SCHEMA,
)
def run_python(code: str) -> Dict[str, Any]:
    """Execute Python code with the PolicyEngine UK compiled interface preloaded."""
    return run_python_code(code)


@register_tool(
    name="generate_chart",
    description=GENERATE_CHART_DESCRIPTION,
    input_schema=GENERATE_CHART_INPUT_SCHEMA,
)
def generate_chart(
    chart_type: str,
    title: str,
    data: List[Dict[str, Any]],
    x_field: str,
    y_fields: List[str],
    x_label: Optional[str] = None,
    y_label: Optional[str] = None,
    x_format: Optional[str] = None,
    y_format: Optional[str] = None,
    x_min: Optional[float] = None,
    x_max: Optional[float] = None,
    y_min: Optional[float] = None,
    y_max: Optional[float] = None,
    series_labels: Optional[List[str]] = None,
    series_styles: Optional[List[str]] = None,
    series_curves: Optional[List[str]] = None,
    subtitle: Optional[str] = None,
    source: Optional[str] = None,
    arrangement: Optional[str] = None,
    area_fill: Optional[bool] = None,
) -> Dict[str, Any]:
    try:
        series = []
        for index, y_field in enumerate(y_fields):
            item = {"field": y_field, "label": series_labels[index] if series_labels and index < len(series_labels) else y_field}
            if series_styles and index < len(series_styles):
                item["lineStyle"] = series_styles[index]
            if series_curves and index < len(series_curves):
                item["curve"] = series_curves[index]
            series.append(item)

        spec = {
            "type": chart_type,
            "title": title,
            "x": {"field": x_field, "label": x_label or x_field},
            "y": {
                "field": y_fields[0] if len(y_fields) == 1 else "value",
                "label": y_label or (y_fields[0] if len(y_fields) == 1 else "Value"),
            },
            "series": series,
            "data": data,
            "showLegend": len(y_fields) > 1,
            "showGrid": True,
        }
        if x_format:
            spec["x"]["format"] = x_format
        if y_format:
            spec["y"]["format"] = y_format
        if x_min is not None:
            spec["x"]["min"] = x_min
        if x_max is not None:
            spec["x"]["max"] = x_max
        if y_min is not None:
            spec["y"]["min"] = y_min
        if y_max is not None:
            spec["y"]["max"] = y_max
        if subtitle:
            spec["subtitle"] = subtitle
        if source:
            spec["source"] = source
        if arrangement and chart_type == "bar":
            spec["arrangement"] = arrangement
        if area_fill and chart_type == "line":
            spec["areaFill"] = area_fill

        return {
            "status": "success",
            "chart_markdown": f"```chart\n{json.dumps(spec, indent=2)}\n```",
            "message": "Chart generated. Include the chart_markdown in your response to display it.",
        }
    except Exception as exc:
        return {"error": str(exc)}


TOOL_DEFINITIONS = tool_definitions()
TOOL_HANDLERS = tool_handlers()


def execute_tool(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"[TOOLS] Executing {tool_name}")
    if tool_name not in TOOL_HANDLERS:
        return {"error": f"Unknown tool: {tool_name}"}
    try:
        if "generator" in tool_input:
            logger.info(f"[TOOLS] Running generator for {tool_name}")
            tool_input = _run_generator(tool_input["generator"])
            logger.info(f"[TOOLS] Generator produced keys: {list(tool_input.keys())}")
        result = TOOL_HANDLERS[tool_name](**tool_input)
        logger.info(f"[TOOLS] {tool_name} completed")
        return result
    except Exception as exc:
        logger.error(f"[TOOLS] Error in {tool_name}: {exc}")
        return {"error": str(exc)}
