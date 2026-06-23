"""Model-facing tool definitions for the UK chat runtime."""

from engine.reforms import REFORM_SCHEMA


# Default simulation/policy year — the single source of truth for the year the
# engine models when a request doesn't name one. Referenced by YEAR_SCHEMA (so
# the tool schemas advertise it), the tool implementations' defaults in
# dispatch.py, and the gateway's non-default-year detection (gateway/policy.py),
# so they can't drift apart. NB this is the modelled policy year, which
# deliberately lags the calendar year.
DEFAULT_SIMULATION_YEAR = 2025

YEAR_SCHEMA = {"type": "integer", "default": DEFAULT_SIMULATION_YEAR}

REFORM_PROPERTY = REFORM_SCHEMA

STRING_ARRAY_SCHEMA = {"type": "array", "items": {"type": "string"}}

ALL_DATASET_SCHEMA = {
    "type": "string",
    "enum": ["frs", "efrs", "spi", "lcfs", "was"],
    "default": "frs",
    "description": "Microdata source for aggregate simulation. FRS is the default for aggregate outputs.",
}

NON_FRS_DATASET_SCHEMA = {
    "type": "string",
    "enum": ["efrs", "spi", "lcfs", "was"],
    "default": "efrs",
    "description": "FRS is not available for analyse_microdata.",
}

FILTERS_SCHEMA = {
    "type": "object",
    "description": (
        "Column to predicate map. Predicate can be a scalar, a list, or a "
        "dict with min, max, gt, lt, gte, lte, or ne."
    ),
}

CHART_FORMAT_SCHEMA = {
    "type": "string",
    "enum": ["currency", "percent", "percent_decimal", "number", "compact", "year"],
    "description": (
        "Number format for axis ticks and tooltips. Use `currency` for GBP "
        "amounts, `percent` for values already on a 0-100 scale, "
        "`percent_decimal` for 0-1 shares, `compact` for large counts (1.2k), "
        "`year` for calendar years."
    ),
}

CHART_DATA_SCHEMA = {
    "type": "array",
    "description": "List of row objects. Each row must contain the `x_field` key and every key listed in `y_fields`.",
    "items": {"type": "object"},
}


VALIDATE_REFORM_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "reform": REFORM_PROPERTY,
    },
    "required": ["reform"],
}

HOUSEHOLD_RECORD_SCHEMA = {"type": "array", "items": {"type": "object"}}

CALCULATE_HOUSEHOLD_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "person": {
            **HOUSEHOLD_RECORD_SCHEMA,
            "description": (
                "Person records. Each should include person_id, benunit_id, "
                "household_id, and age. Common optional fields include "
                "employment_income, self_employment_income, and pension_income."
            ),
        },
        "benunit": {
            **HOUSEHOLD_RECORD_SCHEMA,
            "description": "Benefit-unit records, each with benunit_id and household_id.",
        },
        "household": {
            **HOUSEHOLD_RECORD_SCHEMA,
            "description": (
                "Household records, each with household_id. Add location fields "
                "when relevant, for example region or is_in_scotland."
            ),
        },
        "year": YEAR_SCHEMA,
        "reform": REFORM_PROPERTY,
    },
    "required": ["person", "benunit", "household"],
}

RUN_ECONOMY_SIMULATION_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "year": YEAR_SCHEMA,
        "reform": REFORM_PROPERTY,
        "dataset": ALL_DATASET_SCHEMA,
    },
    "required": [],
}

ANALYSE_MICRODATA_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "entity": {"type": "string", "enum": ["persons", "benunits", "households"]},
        "operation": {
            "type": "string",
            "enum": ["sample", "mean", "sum", "count", "group_by", "describe"],
            "description": (
                "`sample` is not available for the FRS-derived `efrs` dataset; "
                "use aggregate operations for `efrs`."
            ),
        },
        "year": YEAR_SCHEMA,
        "reform": REFORM_PROPERTY,
        "filters": FILTERS_SCHEMA,
        "columns": STRING_ARRAY_SCHEMA,
        "group_by": STRING_ARRAY_SCHEMA,
        "n": {"type": "integer", "default": 5, "description": "Sample size when operation is sample."},
        "dataset": NON_FRS_DATASET_SCHEMA,
    },
    "required": ["entity", "operation"],
}

LOOKUP_LIMIT_SCHEMA = {
    "type": "integer",
    "default": 5,
    "minimum": 1,
    "maximum": 10,
    "description": (
        "Maximum number of success matches or error suggestions to return. "
        "Confirmation responses return the full bounded option set even when "
        "this limit is lower."
    ),
}

LOOKUP_PARAMETER_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Parameter path or natural-language query, for example "
                "`income_tax.personal_allowance` or `basic rate threshold`."
            ),
        },
        "year": YEAR_SCHEMA,
        "limit": LOOKUP_LIMIT_SCHEMA,
    },
    "required": ["query"],
}

LOOKUP_VARIABLE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": (
                "Variable name, label, or natural-language query, for example "
                "`income_tax`, `employment_income`, or `income tax liability`."
            ),
        },
        "include_formula": {
            "type": "boolean",
            "default": True,
            "description": "Whether to include formula source when the variable has formulas.",
        },
        "limit": LOOKUP_LIMIT_SCHEMA,
    },
    "required": ["query"],
}

RUN_PYTHON_INPUT_SCHEMA = {
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
}

GENERATE_CHART_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "chart_type": {
            "type": "string",
            "enum": ["line", "bar", "area", "scatter"],
            "description": (
                "Chart type. Use `line` for schedules/curves over a continuous x, "
                "`bar` for category comparisons (e.g. deciles), `area` for "
                "stacked compositions, `scatter` for point clouds."
            ),
        },
        "title": {"type": "string", "description": "Factually neutral chart title shown above the plot."},
        "data": CHART_DATA_SCHEMA,
        "x_field": {"type": "string", "description": "Key in each data row to use as the x value."},
        "y_fields": {
            **STRING_ARRAY_SCHEMA,
            "description": (
                "Keys in each data row to plot as y series. Provide multiple for "
                "multi-series charts (e.g. baseline vs reform)."
            ),
        },
        "x_label": {"type": "string", "description": "Axis label for x (defaults to `x_field`)."},
        "y_label": {"type": "string", "description": "Axis label for y (defaults to first y field or 'Value')."},
        "x_format": {**CHART_FORMAT_SCHEMA, "description": f"X-axis {CHART_FORMAT_SCHEMA['description']}"},
        "y_format": {**CHART_FORMAT_SCHEMA, "description": f"Y-axis {CHART_FORMAT_SCHEMA['description']}"},
        "x_min": {"type": "number", "description": "Optional fixed minimum for the x axis."},
        "x_max": {"type": "number", "description": "Optional fixed maximum for the x axis."},
        "y_min": {"type": "number", "description": "Optional fixed minimum for the y axis."},
        "y_max": {"type": "number", "description": "Optional fixed maximum for the y axis."},
        "series_labels": {
            **STRING_ARRAY_SCHEMA,
            "description": "Display labels for each y series, in the same order as `y_fields`.",
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
}

VALIDATE_REFORM_DESCRIPTION = (
    "Validate parametric reform JSON without running a simulation. "
    "Use this when the user is drafting, debugging, or asking whether "
    "a reform object is valid. Do not call it as a routine preflight "
    "before every simulation; calculation tools validate reforms internally."
)

CALCULATE_HOUSEHOLD_DESCRIPTION = (
    "Compute taxes, benefits, and net income for an illustrative "
    "specific household described with person, benefit-unit, and "
    "household records. Prefer this over run_python for household-level "
    "questions with a defined household composition. These inputs are "
    "synthetic examples, not real households. When reform is omitted, "
    "output calculated columns use plain names such as income_tax and "
    "net_income. When reform is supplied, including an empty no-op "
    "object, output includes baseline_* and reform_* comparison columns."
)

RUN_ECONOMY_SIMULATION_DESCRIPTION = (
    "Run a UK economy-wide microsimulation comparing baseline current "
    "law to a parametric reform. Returns aggregate outputs including "
    "budgetary impact, programme breakdown, decile impacts, "
    "winners/losers, caseloads, HBAI incomes, and poverty metrics. "
    "Prefer this over run_python for society-wide reform analysis. "
    "Use run_python for structural reforms."
)

ANALYSE_MICRODATA_DESCRIPTION = (
    "Slice, filter, sample, or aggregate non-FRS model microdata for a "
    "given year and optional parametric reform. Use this for allowed "
    "non-FRS microdata follow-ups such as subset means, counts, group "
    "breakdowns, descriptions, or small model-record samples. This tool "
    "explicitly does not support FRS; use run_economy_simulation for "
    "aggregate FRS outputs. Row-level `sample` is also not supported "
    "for the FRS-derived `efrs` dataset; use aggregate operations there. "
    "When reform is omitted, calculated columns use plain names; when "
    "reform is supplied, including an empty no-op object, use "
    "baseline_* and reform_* comparison columns."
)

LOOKUP_PARAMETER_DESCRIPTION = (
    "Look up baseline UK model parameter values for a year by exact path "
    "or natural-language query. Use this for static parameter questions "
    "such as the personal allowance, tax rates, thresholds, limits, and "
    "amounts. Prefer this over run_python for parameter introspection, "
    "and do not run household or economy simulations just to infer a "
    "parameter value. Returns canonical model paths and values, plus "
    "context for common threshold interpretations when deterministic. "
    "`match_certainty` is deterministic string parsing certainty, not "
    "factual confidence in the parameter value. "
    "If several matches are plausible, returns status `needs_confirmation`; "
    "ask the user to choose before answering, using `confirmation_reason` "
    "to explain whether the string match is low-certainty or closely tied."
)

LOOKUP_VARIABLE_DESCRIPTION = (
    "Look up PolicyEngine UK variable definitions, metadata, and formula "
    "source when available. Use this for questions about what a model "
    "variable represents, which entity or period it belongs to, or how "
    "it is calculated. Prefer this over run_python for variable "
    "definition and formula lookups. `match_certainty` is deterministic "
    "string parsing certainty, not factual confidence in the variable "
    "metadata or formula. If several matches are plausible, returns "
    "status `needs_confirmation`; ask the user to choose before answering, "
    "using `confirmation_reason` to explain whether the string match is "
    "low-certainty or closely tied."
)

RUN_PYTHON_DESCRIPTION = (
    "Execute reproducible Python code using the official PolicyEngine UK compiled interface. "
    "Prefer the typed tools (`calculate_household`, `run_economy_simulation`, `analyse_microdata`, "
    "`lookup_parameter`, `lookup_variable`) "
    "when the question fits their shape; use `run_python` as a fallback for structural reforms, "
    "novel aggregations, historical lookups, or unsupported cases. "
    "The environment preloads `policyengine_uk_compiled` as `pe`, plus `Simulation`, `Parameters`, "
    "`StructuralReform`, `aggregate_microdata`, `combine_microdata`, `capabilities`, "
    "`ensure_dataset`, `pd`, `np`, `json`, and `math`. Assign the final answer to `result` and "
    "use `print()` for intermediate output. Do not inspect or return row-level survey microdata, "
    "including FRS data. For household examples, create illustrative synthetic households, prefer "
    "`Simulation.single_person()` for single-person examples, and label them as illustrative rather "
    "than real households."
)

GENERATE_CHART_DESCRIPTION = (
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
)


def get_tool_definitions():
    from tools.registry import tool_definitions

    return tool_definitions()


def __getattr__(name: str):
    if name == "TOOL_DEFINITIONS":
        return get_tool_definitions()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
