"""
Agent tools for the microsim chatbot.

This module owns the public LLM-facing tool functions, dispatcher, and schemas.
Shared deterministic helpers live under backend/tooling.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from tooling.households import build_household_frames
from tooling.microdata import analyse_microdata_result, get_cached_microdata, hash_reform
from tooling.reforms import REFORM_SCHEMA, build_compiled_policy, validate_reform_dict
from tooling.sandbox import (
    build_structural_reform,
    compile_structural_hook,
    run_generator,
    run_python_code,
    safe_import,
)
from tooling.serialization import dataframe_to_records, explore_tabular_data, json_safe
from tooling.simulations import DATASET_LABELS, build_simulation, ensure_compiled_package_importable

logger = logging.getLogger(__name__)

# Compatibility aliases for tests and existing imports. They remain internal
# unless also listed in TOOL_DEFINITIONS and execute_tool().
_ensure_compiled_package_importable = ensure_compiled_package_importable
_safe_import = safe_import
_json_safe = json_safe
_hash_reform = hash_reform
_get_cached_microdata = get_cached_microdata
_build_compiled_policy = build_compiled_policy
_build_simulation = build_simulation
_compile_structural_hook = compile_structural_hook
_build_structural_reform = build_structural_reform
_run_generator = run_generator

__all__ = [
    "TOOL_DEFINITIONS",
    "analyse_microdata",
    "calculate_household",
    "execute_tool",
    "explore_tabular_data",
    "generate_chart",
    "get_baseline_parameters",
    "get_capabilities",
    "run_economy_simulation",
    "run_python",
    "validate_reform",
]


def get_capabilities() -> Dict[str, Any]:
    try:
        _ensure_compiled_package_importable()
        from policyengine_uk_compiled import capabilities

        return capabilities()
    except Exception as exc:
        logger.error(f"Error getting capabilities: {exc}")
        return {"error": str(exc)}


def get_baseline_parameters(year: int = 2025) -> Dict[str, Any]:
    try:
        _ensure_compiled_package_importable()
        from policyengine_uk_compiled import Simulation

        sim = Simulation(year=year)
        return {"year": year, "parameters": sim.get_baseline_params()}
    except Exception as exc:
        logger.error(f"Error getting baseline parameters: {exc}")
        return {"error": str(exc)}


def validate_reform(reform: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate parametric reform JSON without running a simulation."""
    return validate_reform_dict(reform)


def calculate_household(
    person: List[Dict[str, Any]],
    benunit: List[Dict[str, Any]],
    household: List[Dict[str, Any]],
    year: int = 2025,
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


def run_economy_simulation(
    year: int = 2025,
    reform: Optional[Dict[str, Any]] = None,
    dataset: str = "frs",
    structural_reform: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        policy = _build_compiled_policy(reform)
        structural = _build_structural_reform(structural_reform)
        sim = _build_simulation(year, dataset)
        baseline_result = sim.run()
        if structural is not None:
            from policyengine_uk_compiled import aggregate_microdata, combine_microdata

            baseline_microdata = sim.run_microdata()
            reform_microdata = sim.run_microdata(policy=policy, structural=structural)
            combined_microdata = combine_microdata(baseline_microdata, reform_microdata)
            reform_result = aggregate_microdata(
                combined_microdata.persons,
                combined_microdata.benunits,
                combined_microdata.households,
                year,
            )
        else:
            reform_result = sim.run(policy=policy) if policy else baseline_result

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
            "structural_reform_applied": structural is not None,
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


def analyse_microdata(
    entity: str,
    operation: str,
    year: int = 2025,
    reform: Optional[Dict[str, Any]] = None,
    structural_reform: Optional[Dict[str, Any]] = None,
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

        policy = _build_compiled_policy(reform)
        structural = _build_structural_reform(structural_reform)
        if structural is not None:
            from policyengine_uk_compiled import combine_microdata

            sim = _build_simulation(year, dataset_key)
            baseline_microdata = sim.run_microdata()
            reform_microdata = sim.run_microdata(policy=policy, structural=structural)
            microdata = combine_microdata(baseline_microdata, reform_microdata)
        else:
            microdata = _get_cached_microdata(year, reform, dataset_key)

        return analyse_microdata_result(
            microdata=microdata,
            entity=entity,
            operation=operation,
            year=year,
            dataset_key=dataset_key,
            reform_applied=reform is not None,
            structural_reform_applied=structural is not None,
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


def run_python(code: str) -> Dict[str, Any]:
    """Execute Python code with the PolicyEngine UK compiled interface preloaded."""
    return run_python_code(code)


TOOL_HANDLERS = {
    "validate_reform": validate_reform,
    "calculate_household": calculate_household,
    "run_economy_simulation": run_economy_simulation,
    "analyse_microdata": analyse_microdata,
    "run_python": run_python,
    "generate_chart": generate_chart,
}


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


TOOL_DEFINITIONS = [
    {
        "name": "validate_reform",
        "description": (
            "Validate parametric reform JSON without running a simulation. "
            "Use this when the user is drafting, debugging, or asking whether "
            "a reform object is valid. Do not call it as a routine preflight "
            "before every simulation; calculation tools validate reforms internally."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "reform": REFORM_SCHEMA,
            },
            "required": ["reform"],
        },
    },
    {
        "name": "calculate_household",
        "description": (
            "Compute taxes, benefits, and net income for an illustrative "
            "specific household described with person, benefit-unit, and "
            "household records. Prefer this over run_python for household-level "
            "questions with a defined household composition. These inputs are "
            "synthetic examples, not real households."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "person": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Person records. Each should include person_id, "
                        "benunit_id, household_id, and age. Common optional "
                        "fields include employment_income, "
                        "self_employment_income, and pension_income."
                    ),
                },
                "benunit": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Benefit-unit records, each with benunit_id and household_id.",
                },
                "household": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": (
                        "Household records, each with household_id. Add "
                        "location fields when relevant, for example region or "
                        "is_in_scotland."
                    ),
                },
                "year": {"type": "integer", "default": 2025},
                "reform": REFORM_SCHEMA,
            },
            "required": ["person", "benunit", "household"],
        },
    },
    {
        "name": "run_economy_simulation",
        "description": (
            "Run a UK economy-wide microsimulation comparing baseline current "
            "law to a parametric reform. Returns aggregate outputs including "
            "budgetary impact, programme breakdown, decile impacts, "
            "winners/losers, caseloads, HBAI incomes, and poverty metrics. "
            "Prefer this over run_python for society-wide reform analysis. "
            "Use run_python for structural reforms."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "year": {"type": "integer", "default": 2025},
                "reform": REFORM_SCHEMA,
                "dataset": {
                    "type": "string",
                    "enum": ["frs", "efrs", "spi", "lcfs", "was"],
                    "default": "frs",
                    "description": "Microdata source for aggregate simulation. FRS is the default for aggregate outputs.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "analyse_microdata",
        "description": (
            "Slice, filter, sample, or aggregate non-FRS model microdata for a "
            "given year and optional parametric reform. Use this for allowed "
            "non-FRS microdata follow-ups such as subset means, counts, group "
            "breakdowns, descriptions, or small model-record samples. This tool "
            "explicitly does not support FRS; use run_economy_simulation for "
            "aggregate FRS outputs."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "enum": ["persons", "benunits", "households"]},
                "operation": {"type": "string", "enum": ["sample", "mean", "sum", "count", "group_by", "describe"]},
                "year": {"type": "integer", "default": 2025},
                "reform": REFORM_SCHEMA,
                "filters": {
                    "type": "object",
                    "description": "Column to predicate map. Predicate can be a scalar, a list, or a dict with min, max, gt, lt, gte, lte, or ne.",
                },
                "columns": {"type": "array", "items": {"type": "string"}},
                "group_by": {"type": "array", "items": {"type": "string"}},
                "n": {"type": "integer", "default": 5, "description": "Sample size when operation is sample."},
                "dataset": {
                    "type": "string",
                    "enum": ["efrs", "spi", "lcfs", "was"],
                    "default": "efrs",
                    "description": "FRS is not available for analyse_microdata.",
                },
            },
            "required": ["entity", "operation"],
        },
    },
    {
        "name": "run_python",
        "description": (
            "Execute reproducible Python code using the official PolicyEngine UK compiled interface. "
            "Prefer the typed tools (`calculate_household`, `run_economy_simulation`, `analyse_microdata`) "
            "when the question fits their shape; use `run_python` as a fallback for structural reforms, "
            "novel aggregations, parameter introspection, historical lookups, or unsupported cases. "
            "The environment preloads `policyengine_uk_compiled` as `pe`, plus `Simulation`, `Parameters`, "
            "`StructuralReform`, `aggregate_microdata`, `combine_microdata`, `capabilities`, "
            "`ensure_dataset`, `pd`, `np`, `json`, and `math`. Assign the final answer to `result` and "
            "use `print()` for intermediate output. Do not inspect or return row-level survey microdata, "
            "including FRS data. For household examples, create illustrative synthetic households, prefer "
            "`Simulation.single_person()` for single-person examples, and label them as illustrative rather "
            "than real households."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": (
                        "Python code to execute. Must assign the final answer to `result`. "
                        "Use the preloaded PolicyEngine interface directly, for example: "
                        "`sim = Simulation(year=2025)` or `policy = Parameters.model_validate({...})`."
                    ),
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "generate_chart",
        "description": (
            "Generate a chart JSON block for the frontend to render. "
            "Use this for visualisations such as income distributions, marginal-rate or tax-schedule curves, "
            "decile impact comparisons, and trends over time or income. "
            "Use factually neutral titles, subtitles, labels, and captions; do not call policies good, bad, fair, unfair, "
            "regressive, progressive, generous, or punitive. "
            "The tool returns a `chart_markdown` field containing a ```chart fenced JSON block - you MUST paste that "
            "string verbatim into your next text response, otherwise the chart will not appear to the user. "
            "Do not attempt to render charts with matplotlib inside `run_python`; the UI cannot display matplotlib output. "
            "Compute the data first with a typed calculation tool or `run_python` "
            "(returning a list of row dicts), then pass it to this tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chart_type": {
                    "type": "string",
                    "enum": ["line", "bar", "area", "scatter"],
                    "description": "Chart type. Use `line` for schedules/curves over a continuous x, `bar` for category comparisons (e.g. deciles), `area` for stacked compositions, `scatter` for point clouds.",
                },
                "title": {"type": "string", "description": "Factually neutral chart title shown above the plot."},
                "data": {
                    "type": "array",
                    "description": "List of row objects. Each row must contain the `x_field` key and every key listed in `y_fields`.",
                    "items": {"type": "object"},
                },
                "x_field": {"type": "string", "description": "Key in each data row to use as the x value."},
                "y_fields": {
                    "type": "array",
                    "description": "Keys in each data row to plot as y series. Provide multiple for multi-series charts (e.g. baseline vs reform).",
                    "items": {"type": "string"},
                },
                "x_label": {"type": "string", "description": "Axis label for x (defaults to `x_field`)."},
                "y_label": {"type": "string", "description": "Axis label for y (defaults to first y field or 'Value')."},
                "x_format": {
                    "type": "string",
                    "enum": ["currency", "percent", "percent_decimal", "number", "compact", "year"],
                    "description": "Number format for x-axis ticks and tooltips. Use `currency` for GBP amounts, `percent` for values already on a 0-100 scale, `percent_decimal` for 0-1 shares, `compact` for large counts (1.2k), `year` for calendar years.",
                },
                "y_format": {
                    "type": "string",
                    "enum": ["currency", "percent", "percent_decimal", "number", "compact", "year"],
                    "description": "Number format for y-axis ticks and tooltips. Same options as `x_format`.",
                },
                "x_min": {"type": "number", "description": "Optional fixed minimum for the x axis."},
                "x_max": {"type": "number", "description": "Optional fixed maximum for the x axis."},
                "y_min": {"type": "number", "description": "Optional fixed minimum for the y axis."},
                "y_max": {"type": "number", "description": "Optional fixed maximum for the y axis."},
                "series_labels": {
                    "type": "array",
                    "description": "Display labels for each y series, in the same order as `y_fields`.",
                    "items": {"type": "string"},
                },
                "series_styles": {
                    "type": "array",
                    "description": "Line style per series (line/area charts).",
                    "items": {"type": "string", "enum": ["solid", "dashed", "dotted"]},
                },
                "series_curves": {
                    "type": "array",
                    "description": "Curve interpolation per series (line/area charts).",
                    "items": {"type": "string", "enum": ["smooth", "step", "linear"]},
                },
                "subtitle": {"type": "string", "description": "Optional subtitle shown under the title."},
                "source": {"type": "string", "description": "Optional source/caption shown beneath the chart."},
                "arrangement": {
                    "type": "string",
                    "enum": ["grouped", "stacked"],
                    "description": "For bar charts only: `grouped` side-by-side or `stacked`.",
                },
                "area_fill": {"type": "boolean", "description": "For line charts only: fill the area under the line."},
            },
            "required": ["chart_type", "title", "data", "x_field", "y_fields"],
        },
    },
]
