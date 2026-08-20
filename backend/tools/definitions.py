"""Model-facing tool definitions for the UK chat runtime."""

from datetime import date
from types import MappingProxyType

from engine.constants import HOUSEHOLD_COUNTRY_IDS
from engine.decile_concepts import (
    DECILE_CONCEPT_VALUES,
    DEFAULT_DECILE_CONCEPT,
)


def current_simulation_year() -> int:
    """Return the calendar year used when a user does not specify one."""
    return date.today().year


DEFAULT_SIMULATION_YEAR = current_simulation_year()

YEAR_SCHEMA = {"type": "integer", "default": DEFAULT_SIMULATION_YEAR}

REFORM_SCHEMA = {
    "type": "object",
    "description": (
        "Flat policyengine.py UK reform mapping from canonical parameter paths "
        "to scalar values or date-to-value maps, for example "
        "`gov.hmrc.income_tax.allowances.personal_allowance.amount: 15000`."
    ),
    "additionalProperties": True,
}

STRING_ARRAY_SCHEMA = {"type": "array", "items": {"type": "string"}}
HOUSEHOLD_SCHEMA = {
    "type": "object",
    "properties": {
        "country": {
            "type": "string",
            "enum": list(HOUSEHOLD_COUNTRY_IDS),
            "description": "Exact PolicyEngine categorical country ID.",
        }
    },
    "additionalProperties": True,
}
FILTER_VALUE_SCHEMA = {
    "type": ["number", "string", "boolean"],
    "description": "Comparison value interpreted using the filter variable's type.",
}

FILTER_LIMIT_SCHEMA = {
    "type": "integer",
    "default": 25,
    "minimum": 1,
    "maximum": 100,
}

CHART_FORMAT_SCHEMA = {
    "type": "string",
    "enum": ["currency", "percent", "percent_decimal", "number", "compact", "year"],
}

CHART_PRESET_SOURCES = MappingProxyType(
    {
        "budget_waterfall": (
            "budgetary_impact",
            "compute_budgetary_impact",
            "budgetary_impact",
        ),
        "program_budget_waterfall": (
            "program_breakdown",
            "compute_program_breakdown",
            "program_breakdown",
        ),
        "decile_absolute_bar": (
            "decile_impact",
            "compute_decile_impacts",
            "decile_impacts",
        ),
        "decile_relative_bar": (
            "decile_impact",
            "compute_decile_impacts",
            "decile_impacts",
        ),
        "winners_losers_stacked_bar": (
            "winners_losers",
            "compute_winners_losers",
            "winners_losers",
        ),
        "poverty_relative_bar": (
            "poverty_impact",
            "compute_poverty_metrics",
            "poverty_metrics",
        ),
        "inequality_relative_bar": (
            "inequality_impact",
            "compute_inequality_metrics",
            "inequality_metrics",
        ),
    }
)
CHART_EXPLICIT_DATA_KINDS = ("earnings_variation_line",)
CHART_GENERIC_KINDS = (
    "generic_line",
    "generic_bar",
    "generic_area",
    "generic_scatter",
)
CHART_KIND_VALUES = (
    *CHART_PRESET_SOURCES,
    *CHART_EXPLICIT_DATA_KINDS,
    *CHART_GENERIC_KINDS,
)

CHART_KIND_SCHEMA = {
    "type": "string",
    "enum": list(CHART_KIND_VALUES),
    "description": "Deterministic chart layout preset.",
}

CHART_DATA_SCHEMA = {
    "type": ["array", "object"],
    "description": "Rows or structured derivative output for chart generation.",
}


def _object_schema(properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": False,
    }


LIST_ENTITIES_INPUT_SCHEMA = _object_schema({})
SEARCH_VARIABLES_INPUT_SCHEMA = _object_schema(
    {
        "query": {"type": "string", "default": ""},
        "entity": {"type": "string"},
        "limit": FILTER_LIMIT_SCHEMA,
    }
)
GET_VARIABLE_INPUT_SCHEMA = _object_schema({"name": {"type": "string"}}, ["name"])
SEARCH_PARAMETERS_INPUT_SCHEMA = _object_schema(
    {"query": {"type": "string", "default": ""}, "limit": FILTER_LIMIT_SCHEMA}
)
GET_PARAMETER_INPUT_SCHEMA = _object_schema(
    {"path": {"type": "string"}, "year": YEAR_SCHEMA},
    ["path"],
)
LIST_REFORM_TARGETS_INPUT_SCHEMA = _object_schema(
    {"query": {"type": "string", "default": ""}, "limit": FILTER_LIMIT_SCHEMA}
)
LIST_HOUSEHOLD_INPUT_VARIABLES_INPUT_SCHEMA = _object_schema({"entity": {"type": "string"}})
LIST_SOCIETY_OUTPUT_VARIABLES_INPUT_SCHEMA = _object_schema(
    {
        "entity": {
            "type": "string",
            "enum": ["person", "benunit", "household"],
            "description": "Optional output entity to inspect.",
        }
    }
)
LIST_SUPPORTED_OUTPUTS_INPUT_SCHEMA = _object_schema(
    {
        "scope": {
            "type": "string",
            "enum": ["household", "society", "derivative", "artifact"],
        }
    }
)

VALIDATE_REFORM_INPUT_SCHEMA = _object_schema(
    {"reform": REFORM_SCHEMA, "year": YEAR_SCHEMA},
    ["reform"],
)

VALIDATE_HOUSEHOLD_INPUT_SCHEMA = _object_schema(
    {
        "people": {"type": "array", "items": {"type": "object"}},
        "benunit": {"type": "object"},
        "household": HOUSEHOLD_SCHEMA,
        "year": YEAR_SCHEMA,
        "reform": REFORM_SCHEMA,
        "extra_variables": STRING_ARRAY_SCHEMA,
    },
    ["people"],
)

RUN_HOUSEHOLD_SIMULATION_INPUT_SCHEMA = VALIDATE_HOUSEHOLD_INPUT_SCHEMA

RUN_SOCIETY_SIMULATION_INPUT_SCHEMA = _object_schema(
    {
        "year": YEAR_SCHEMA,
        "reform": REFORM_SCHEMA,
        "extra_variables": {
            "type": "object",
            "description": (
                "Existing policyengine-uk variables to materialize in addition to "
                "the defaults reported by list_society_output_variables. Before "
                "using this field, verify every exact variable name with "
                "search_variables or get_variable and place it under the entity "
                "reported by discovery. Do not put default variables, invented "
                "names, expressions, aliases, or derived concepts here."
            ),
            "properties": {
                "person": STRING_ARRAY_SCHEMA,
                "benunit": STRING_ARRAY_SCHEMA,
                "household": STRING_ARRAY_SCHEMA,
            },
            "additionalProperties": False,
        },
    }
)

RESULT_ID_INPUT_SCHEMA = {"type": "string", "description": "Result handle returned by a prior tool in this turn."}

COMPUTE_BUDGETARY_IMPACT_INPUT_SCHEMA = _object_schema({"simulation_id": RESULT_ID_INPUT_SCHEMA}, ["simulation_id"])
COMPUTE_PROGRAM_BREAKDOWN_INPUT_SCHEMA = _object_schema(
    {"simulation_id": RESULT_ID_INPUT_SCHEMA, "programs": STRING_ARRAY_SCHEMA},
    ["simulation_id"],
)
COMPUTE_DECILE_IMPACTS_INPUT_SCHEMA = _object_schema(
    {
        "simulation_id": RESULT_ID_INPUT_SCHEMA,
        "decile_concept": {
            "type": "string",
            "enum": list(DECILE_CONCEPT_VALUES),
            "default": DEFAULT_DECILE_CONCEPT.value,
            "description": (
                "Select exactly one analytical concept. Use "
                "`household_net_income` for ordinary income-decile requests, "
                "`equivalised_hbai_net_income` only when the user explicitly "
                "requests equivalised HBAI net income, or `wealth` when the user "
                "requests wealth deciles."
            ),
        },
    },
    ["simulation_id"],
)
COMPUTE_WINNERS_LOSERS_INPUT_SCHEMA = _object_schema(
    {
        "simulation_id": RESULT_ID_INPUT_SCHEMA,
        "basis": {"type": "string", "enum": ["income", "wealth"], "default": "income"},
    },
    ["simulation_id"],
)
COMPUTE_POVERTY_METRICS_INPUT_SCHEMA = _object_schema(
    {"simulation_id": RESULT_ID_INPUT_SCHEMA},
    ["simulation_id"],
)
COMPUTE_INEQUALITY_METRICS_INPUT_SCHEMA = _object_schema(
    {"simulation_id": RESULT_ID_INPUT_SCHEMA},
    ["simulation_id"],
)
AGGREGATE_RESULT_INPUT_SCHEMA = _object_schema(
    {
        "simulation_id": RESULT_ID_INPUT_SCHEMA,
        "target": {
            "type": "string",
            "enum": ["baseline", "reform", "change"],
            "default": "reform",
        },
        "entity": {"type": "string", "enum": ["person", "benunit", "household"]},
        "variable": {"type": "string"},
        "operation": {"type": "string", "enum": ["sum", "mean", "count"]},
        "filter_variable": {
            "type": "string",
            "description": (
                "Optional verified model variable used to filter the weighted "
                "aggregate. It must be materialized by the simulation."
            ),
        },
        "filter_variable_eq": FILTER_VALUE_SCHEMA,
        "filter_variable_leq": FILTER_VALUE_SCHEMA,
        "filter_variable_geq": FILTER_VALUE_SCHEMA,
    },
    ["simulation_id", "entity", "variable", "operation"],
)

GENERATE_CHART_INPUT_SCHEMA = _object_schema(
    {
        "chart_kind": CHART_KIND_SCHEMA,
        "result_id": RESULT_ID_INPUT_SCHEMA,
        "data": CHART_DATA_SCHEMA,
        "title": {"type": "string", "description": "Optional neutral chart title."},
        "subtitle": {"type": "string"},
        "source": {"type": "string"},
        "x_field": {"type": "string"},
        "y_fields": STRING_ARRAY_SCHEMA,
        "x_label": {"type": "string"},
        "y_label": {"type": "string"},
        "x_format": CHART_FORMAT_SCHEMA,
        "y_format": CHART_FORMAT_SCHEMA,
        "x_min": {"type": "number"},
        "x_max": {"type": "number"},
        "y_min": {"type": "number"},
        "y_max": {"type": "number"},
        "series_labels": STRING_ARRAY_SCHEMA,
        "series_styles": {
            "type": "array",
            "items": {"type": "string", "enum": ["solid", "dashed", "dotted"]},
        },
        "series_curves": {
            "type": "array",
            "items": {"type": "string", "enum": ["smooth", "step", "linear"]},
        },
        "arrangement": {"type": "string", "enum": ["grouped", "stacked"]},
    },
    ["chart_kind"],
)

DISCOVERY_DESCRIPTION = "Discover model metadata from policyengine.py without running a simulation."
SEARCH_VARIABLES_DESCRIPTION = (
    "Search the policyengine.py UK variable registry. Use this before passing a "
    "variable to run_society_simulation.extra_variables or aggregate_result; "
    "never invent variable names. Results identify default society outputs."
)
GET_VARIABLE_DESCRIPTION = (
    "Verify one exact policyengine.py UK variable and inspect its entity and "
    "whether it is a default society output."
)
LIST_SOCIETY_OUTPUT_VARIABLES_DESCRIPTION = (
    "List the policyengine.py UK variables automatically materialized by a "
    "society simulation, grouped by output entity. Call this before choosing "
    "run_society_simulation.extra_variables."
)
VALIDATE_REFORM_DESCRIPTION = (
    "Validate flat policyengine.py reform JSON without running a simulation. "
    "Use for drafting or debugging reform parameter paths, not as routine preflight."
)
VALIDATE_HOUSEHOLD_DESCRIPTION = (
    "Validate an illustrative synthetic UK household against policyengine.py variable metadata."
)
RUN_HOUSEHOLD_SIMULATION_DESCRIPTION = (
    "Run an illustrative synthetic UK household simulation through policyengine.py."
)
RUN_SOCIETY_SIMULATION_DESCRIPTION = (
    "Run baseline and reform policyengine.py UK simulations and return metadata plus a turn-local result handle. "
    "Use derivative tools to calculate outputs from that handle. Before adding "
    "extra_variables, inspect the default set with list_society_output_variables "
    "and verify every non-default name with search_variables or get_variable."
)
DERIVATIVE_DESCRIPTION = (
    "Compute an official policyengine.py aggregate or derivative output from a prior simulation result handle. "
    "This never returns row-level survey records."
)
DECILE_IMPACTS_DESCRIPTION = (
    "Compute policyengine.py changes in mean income for exactly one of three "
    "decile concepts. Household net income is the default; equivalised HBAI net "
    "income is used only when explicitly requested. Computed household income "
    "groups use policyengine.py's person-weighted ranks and exclude negative or "
    "non-finite ranking incomes from reported deciles. Wealth deciles rank "
    "households by wealth and measure household net income. Empty groups return "
    "null impacts, not zero."
)
GENERATE_CHART_DESCRIPTION = (
    "Generate frontend-renderable chart markdown. For preset chart kinds, the layout, "
    "colours, axes, labels, and legend are deterministic app-v2-style choices and "
    "result_id must identify the matching derivative output. Explicit data is accepted "
    "for generic chart kinds and the earnings-variation preset."
)


def get_tool_definitions():
    from tools.registry import tool_definitions

    return tool_definitions()


def __getattr__(name: str):
    if name == "TOOL_DEFINITIONS":
        return get_tool_definitions()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
