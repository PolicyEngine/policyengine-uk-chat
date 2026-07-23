# UK Chat Runtime

Use this skill when changing the UK chat model pathway, system prompts, exposed
tools, calculation behavior, or AI-facing runtime boundaries.

## Source Boundaries

The backend is organized by topic — one package per concern:

- `backend/chat/` owns the chat turn: `orchestrator.py` (request parsing, SSE
  streaming, the tool loop), `system_blocks.py` (system-block assembly),
  `model_selection.py`, `schemas.py`, `titles.py`,
  `suggestions.py`, and `routes.py` (the `/chat` router).
- `backend/gateway/` owns the opening-turn pre-pass: `runtime.py` (the
  forced-tool classifier) and `policy.py` (the deterministic criticality + gate).
- `backend/prompts/` owns all product prompt text: `system.py` (the compute
  system prompt), `gateway.py` (gateway + lightweight prompts), `meta.py` (title
  + suggestion prompts). Keep prompts modular and declarative there.
- `backend/tools/` owns the model-facing tool seam: `definitions.py` (schemas),
  `registry.py` (the `@register_tool` decorator), and `dispatch.py` (the
  `execute_tool` dispatcher + tool functions). Register model-facing tools once
  with `@register_tool`; `TOOL_DEFINITIONS` and `TOOL_HANDLERS` are derived from
  that registry. Reuse shared schema fragments in `definitions.py` rather than
  duplicating object/array/dataset/format shapes.
- `backend/engine/` owns the deterministic PolicyEngine compute helpers
  (policyengine.py runtime loading, catalog discovery, households, official
  derivative adapters, reforms, simulations, and serialization).
- `backend/config/` owns model-call configuration (model ids, temperatures, the
  Anthropic client factories, and environment settings).
- `backend/api/` owns the HTTP surface (`main.py` app + router mounting,
  `errors.py`, `rate_limit.py`).

Do not spread prompt strings back into route handlers. If runtime prompt rules
change, update `backend/prompts/` and the prompt contract tests together.

## Model Harness

The current chat runtime is application-specific rather than a generic model
harness. It calls the Anthropic SDK directly for streaming control. Pydantic AI
imports/comments may exist, but they should not be treated as the active
orchestration layer unless the code is deliberately refactored.

Keep model/provider-specific code at the orchestration edge. Durable guidance
for agents belongs in `docs/engineering/skills/`; product behavior prompts
belong in `backend/prompts/`.

## Tool Boundary

Only tools registered with `@register_tool` are exposed to the model and
dispatched by `execute_tool()`. At present, the exposed tools are:

- Discovery: `list_datasets`, `list_entities`, `search_variables`,
  `get_variable`, `search_parameters`, `get_parameter`,
  `list_reform_targets`, `list_household_input_variables`,
  `list_society_output_variables`, and `list_supported_outputs`.
- Validation: `validate_reform` and `validate_household`.
- Simulation: `run_household_simulation` for illustrative synthetic households
  and `run_society_simulation` for aggregate society-wide simulations.
- Derivatives: `compute_budgetary_impact`, `compute_program_breakdown`,
  `compute_decile_impacts`, `compute_winners_losers`,
  `compute_poverty_metrics`, `compute_inequality_metrics`, and
  `aggregate_result`. These tools must delegate aggregation and derivation to
  policyengine.py output classes; runtime code must not aggregate MicroSeries,
  NumPy arrays, or survey weights itself.
- Artifacts: `generate_chart`, including deterministic app-v2-style presets for
  budget waterfalls, programme waterfalls, decile bars, winners/losers stacks,
  poverty/inequality relative bars, and earnings lines.

Helper functions in `backend/engine/` are implementation details unless they
are exposed through `@register_tool`.

`tools.registry.tool_definitions()` returns caller-owned JSON-like snapshots for
model/eval requests. Mutating those snapshots is only a local per-call edit and
does not register, remove, or mutate canonical tools. Use `@register_tool` for
tool registration.

The runtime uses policyengine.py with the UK country package. The default year
is `2026`. Society-wide tools default to `enhanced_frs_2023_24`, resolved
through policyengine.py's dataset manifest. The standard certified UK dataset
exposed by policyengine.py is `populace_uk_2023`; keep the mapping in
`backend/engine/constants.py` documented so the default can be switched if
needed.

The public runtime does not expose row-level survey records or a broad
model-facing Python execution tool. Use discovery and derivative tools rather
than asking the model to write arbitrary code.

Before a society simulation uses variable-level outputs, inspect the model
version's authoritative `entity_variables` defaults through
`list_society_output_variables`. Verify every required non-default variable
with `search_variables` or `get_variable`, then pass only those non-default
names through `extra_variables` under the entity reported by discovery.
`extra_variables` materializes existing model variables; it does not create
expressions, aliases, filters, or derived variables. Use `aggregate_result`'s
official policyengine.py filter arguments for conditional weighted aggregates.

## Deterministic And Non-Deterministic Segments

- Non-deterministic: user text interpretation, model planning, tool selection,
  prose generation, follow-up suggestions, and title generation.
- Deterministic: request validation, the gateway gate (criticality + outcome),
  lightweight-route tool omission, tool dispatch, typed tool execution after
  selection, derivative calculation, chart JSON construction,
  result truncation/summarisation, billing calculation, and database writes.

The gateway's non-`ready` (lightweight) outcomes must remain structurally
enforced by omitting tools from the model request, not only by prompting the
model not to call tools.

Tool choice is model-mediated unless the route layer deliberately forces a
specific tool. Prompt and schema guidance improve selection consistency, but
they are not deterministic controls. Every model call sets its temperature from
`backend/config/`: `DEFAULT_TEMPERATURE` (0, deterministic) for the
compute loop, titling, the gateway classifier, and evals; `SUGGESTION_TEMPERATURE`
for follow-up suggestion chips, which deliberately sample with variety.

## Policy Analysis Rules

- Be factually neutral. Do not call UK tax or benefit choices good, bad, fair,
  unfair, regressive, progressive, generous, punitive, or similar.
- Quantitative policy answers should be computed with the lifecycle tools; do
  not answer tax, benefit, reform, poverty, decile, or distributional questions
  from memory.
- Static parameter questions should use `search_parameters` to discover the
  canonical path, then `get_parameter` to retrieve its value. Do not run
  household or society simulations just to infer a parameter value.
- Use `validate_reform` only when the user is drafting, debugging, or asking
  whether reform JSON is valid. Do not use it as a routine preflight before
  every simulation.
- Do not access, display, quote, or imply access to row-level survey microdata
  or real households.
- Use aggregate simulation and derivative tools only for aggregate outputs.
- Do not row-sample FRS-derived datasets, including Enhanced FRS.
- If a user asks for household examples, construct illustrative synthetic
  households through `run_household_simulation`, and label examples as
  illustrative, synthetic, or hypothetical.
- The household tool supports one household containing one benefit unit. Do not
  combine unrelated adults or multiple benefit units into one tool call.
