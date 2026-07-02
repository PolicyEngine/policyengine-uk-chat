# Tools

Tools are the only way the agent produces numbers. The model never recalls
figures from memory — it calls a tool, the backend runs it against the compiled
engine, and the result flows back into the conversation. This page describes the
registry that wires tools up, the seven tools the model can call, and the
restricted `run_python` sandbox. The agent loop that dispatches them is
documented in [The chat agent](chat.md).

## The registry

Tools are registered with a decorator. `backend/tools/registry.py` exposes
`@register_tool(name=..., description=..., input_schema=...)`, which appends each
handler to an internal `_TOOL_SPECS` list. Registering two tools under the same
name raises `ValueError`.

The registry exposes three read-only accessors:

```{list-table}
:header-rows: 1
:widths: 30 70

* - Accessor
  - Returns
* - `tool_specs()`
  - The registered specs (name, description, schema, handler).
* - `tool_definitions()`
  - The JSON-schema dicts the model sees (name, description, `input_schema`).
* - `tool_handlers()`
  - A read-only `MappingProxyType` mapping tool name → handler function.
```

Each accessor lazily imports `tools.dispatch` so the `@register_tool` decorators
run (their side effects populate `_TOOL_SPECS`) before the registry is read.

The code is split by concern:

- `backend/tools/definitions.py` holds the JSON schemas and descriptions (data
  only).
- `backend/tools/dispatch.py` holds the `@register_tool`-decorated
  implementations.

```{note}
To add a tool, write a function decorated with `@register_tool(...)` in
`dispatch.py`. The registry picks it up automatically — no central list to edit.
```

## Dispatch

Dispatch is synchronous. `execute_tool(tool_name, tool_input)` looks the name up
in `TOOL_HANDLERS` (sourced from `tool_handlers()`), calls
`TOOL_HANDLERS[name](**tool_input)`, and returns a dict:

- an unknown tool name returns `{"error": ...}`;
- any exception raised by a handler is caught and returned as
  `{"error": str(exc)}`.

The chat orchestrator runs these synchronous handlers concurrently by wrapping
each in an async task, so several tool calls within one turn execute in
parallel. See [The chat agent](chat.md) for that loop.

## The seven tools

```{list-table}
:header-rows: 1
:widths: 24 46 30

* - Tool
  - Purpose
  - Key inputs
* - `validate_reform`
  - Validate a parametric reform's JSON against the schema without running a
    simulation.
  - `reform` (dict, optional)
* - `calculate_household`
  - Compute taxes, benefits, and net income for an illustrative household.
  - `person`, `benunit`, `household` arrays, `year`, optional `reform`
* - `run_economy_simulation`
  - Run a UK economy-wide microsimulation; returns budgetary impact, deciles,
    and poverty.
  - `year`, optional `reform`, `dataset` (`frs`/`efrs`/`spi`/`lcfs`/`was`)
* - `analyse_microdata`
  - Slice, filter, sample, or aggregate microdata with an optional reform.
  - `entity` (`persons`/`benunits`/`households`), `operation`
    (`sample`/`mean`/`sum`/`count`/`group_by`/`describe`), `year`, optional
    `reform`, `filters`, `columns`, `group_by`, `n`, `dataset` (non-FRS)
* - `lookup_parameter`
  - Look up a baseline parameter by path or natural-language query (not a
    simulation).
  - `query` (string), optional `year`, `limit` (1–10)
* - `run_python`
  - Execute reproducible Python against the compiled engine.
  - `code` (string; must assign to `result`)
* - `generate_chart`
  - Turn computed rows into a chart spec the frontend renders.
  - `chart_type` (`line`/`bar`/`area`/`scatter`), `title`, `data` (array of row
    dicts), `x_field`, `y_fields`, plus optional formatting args
```

`lookup_parameter` is implemented via `backend/engine/lookup/` — see
[The engine](engine.md).

## The `run_python` sandbox

`run_python` executes model-authored Python against the compiled engine inside a
restricted environment (`backend/engine/sandbox.py`, function
`run_python_code()`). It exposes only a curated set of safe builtins, and an
import whitelist allowing just `json`, `math`, `numpy`, and `pandas`.

The namespace is preloaded so the agent uses the official interface rather than
re-implementing policy logic:

```{list-table}
:header-rows: 1
:widths: 28 72

* - Name
  - What it is
* - `pe`
  - A `SafeCompiledModule` wrapper over `policyengine_uk_compiled`.
* - `Simulation`
  - A safe wrapper that blocks FRS row-level access from `run_python`.
* - `Parameters`
  - Access to baseline parameters.
* - `StructuralReform`
  - Build structural (non-parametric) reforms.
* - `aggregate_microdata`
  - Aggregate a microdata column.
* - `combine_microdata`
  - Combine microdata across entities.
* - `capabilities`
  - Report the modelled datasets, years, and programmes.
* - `ensure_dataset`
  - Make a dataset available before use.
* - `pd`
  - pandas.
* - `np`
  - numpy (if available).
* - `json`, `math`
  - Standard-library modules.
```

**Result contract.** The code must assign its final answer to a variable named
`result`. `print()` output is captured separately as short diagnostics. The tool
returns a dict with `result` and/or `output` — or a `note` if neither was
produced.

```{important}
The system prompt requires calling `capabilities()` first when using
`run_python`, so the agent grounds in the modelled datasets, years, and
programmes before simulating. Microdata privacy rules forbid row-level access:
the `Simulation` wrapper blocks FRS row-level reads from inside the sandbox.
```

## Charts

`generate_chart` does not draw anything itself — it returns a `chart_markdown`
field containing a fenced ```` ```chart ```` JSON block.

```{important}
The agent must paste the `chart_markdown` block **verbatim** into its next text
response. The [Frontend](../frontend.md) parses that block to render the chart;
Matplotlib output produced inside `run_python` is discarded. If the block is not
echoed exactly, no chart appears.
```

## Adding or changing tools

1. Decorate a new function with `@register_tool(...)` in
   `backend/tools/dispatch.py`. The registry and `definitions.py` wire it through
   automatically.
2. Add a test under `backend/tests/` (e.g. `test_agent_tools.py`).

```{note}
Prompt caching: the Anthropic tool array is cached as one block with
`cache_control` stamped on the **last** tool only. Keep tool ordering stable so
the cache stays valid.
```
