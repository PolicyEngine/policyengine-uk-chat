# Tools

The chat runtime exposes typed tools from `backend/tools/definitions.py`. Tool
implementations are registered in `backend/tools/dispatch.py`; there is no
model-authored Python execution tool.

## Lifecycle

Society-wide analysis is a multi-step lifecycle:

1. Discover datasets, entities, variables, parameters, reform targets, household
   inputs, or supported outputs.
2. Validate the reform or synthetic household.
3. Run a household simulation, create an axes handle, or create a society
   simulation handle.
4. Retrieve one complete axes series, or pass the society handle to a
   derivative tool.
5. Pass a derivative result handle to a deterministic preset chart.

`ToolExecutionContext` owns the result store for one chat turn. Handles are
opaque and turn-local. The model cannot inspect or serialize the underlying
`policyengine.core.Simulation` objects.

## Discovery tools

| Tool | Purpose |
| --- | --- |
| `list_datasets` | List the UK datasets deliberately exposed by the app. |
| `list_entities` | List model entities and variable counts. |
| `search_variables` | Search variables, optionally by entity. |
| `get_variable` | Return one variable's metadata. |
| `search_parameters` | Search policy parameter paths. |
| `get_parameter` | Return one parameter and its value for a year. |
| `list_reform_targets` | Search reformable parameter paths and common aliases. |
| `list_household_input_variables` | List variables accepted in synthetic household input. |
| `list_supported_outputs` | List supported household, society, derivative, and artifact outputs. |

The discovery surface is intentionally split by catalog area so the model asks
for only the metadata it needs.

## Validation and simulation

| Tool | Purpose |
| --- | --- |
| `validate_reform` | Validate a flat policyengine.py parameter-path reform. |
| `validate_household` | Validate synthetic household structure, variables, and reform. |
| `run_household_simulation` | Run baseline and reform values for one synthetic household. |
| `run_axes_simulation` | Run one numeric synthetic-household sweep and return a turn-local handle. |
| `get_axes_series` | Retrieve every point for one selected axes output and target. |
| `run_society_simulation` | Materialize baseline and reform simulations and return metadata plus a handle. |

`run_society_simulation` does not eagerly calculate fiscal, poverty, decile, or
inequality results. Its `result_id` is consumed by the derivative tools below.
`run_household_simulation` models one household containing one benefit unit;
multiple benefit units or unrelated households require separate calls.

`run_axes_simulation` accepts one verified numeric axis with 2–101 points and
1–5 verified numeric outputs. It returns metadata and a turn-local
`simulation_id`, not the full calculation. `get_axes_series` then retrieves one
baseline or reform output with no pagination. An abbreviated response is:

```json
{
  "household_input": {
    "people": [{"age": 30}],
    "benunit": {},
    "household": {},
    "year": 2026
  },
  "axis": {"name": "employment_income", "index": 0},
  "series": {
    "name": "household_net_income",
    "index": 0,
    "target": "baseline"
  },
  "x": [0, 100000],
  "y": [4939.75, 68398.35]
}
```

Parallel arrays keep a complete 101-point series compact and preserve coordinate
alignment. If the complete series exceeds the 12,000-character axes JSON limit,
retrieval returns a descriptive error instead of partial x/y arrays. The stored
multi-output run stays outside model context, but each successfully retrieved
series enters it. Handles expire after the current chat turn. Request baseline
and reform separately. Axes handles are deliberately not accepted by chart
tools.

## Derivative tools

| Tool | policyengine.py implementation |
| --- | --- |
| `compute_budgetary_impact` | Weighted `Aggregate` totals and `ChangeAggregate` changes for household tax and benefits. |
| `compute_program_breakdown` | `build_program_statistics` with the UK program configuration. |
| `compute_decile_impacts` | Official `DecileImpact` outputs for deciles 1-10. |
| `compute_winners_losers` | `compute_intra_decile_impacts`. |
| `compute_poverty_metrics` | `calculate_uk_poverty_rates` and `calculate_uk_poverty_by_age`. |
| `compute_inequality_metrics` | `calculate_uk_inequality`. |
| `aggregate_result` | Weighted `Aggregate` or `ChangeAggregate` for `sum`, `mean`, or `count`. |

These adapters may reshape official outputs for the tool contract, but they must
not call the country microsimulation directly, convert `MicroSeries` to NumPy,
or implement survey weighting themselves. Row-level society data is never a
tool result.

## Charts

`generate_chart` supports generic line, bar, area, and scatter specs for data
already present in a tool result. It also supports deterministic presets:

- `budget_waterfall`
- `program_budget_waterfall`
- `decile_absolute_bar`
- `decile_relative_bar`
- `winners_losers_stacked_bar`
- `poverty_relative_bar`
- `inequality_relative_bar`
- `earnings_variation_line`

Except for the explicit earnings-series preset, policy-result presets require a
matching derivative `result_id`. The backend maps the typed derivative output to
the fixed chart rows; the frontend only renders those rows with the preset
layout.

## Privacy boundary

The Enhanced FRS, the standard certified UK dataset, and the standard FRS are
aggregate-only in chat. `json_safe` rejects pandas `DataFrame` and `Series`
objects so an accidental raw society payload fails closed instead of becoming a
tool response.
