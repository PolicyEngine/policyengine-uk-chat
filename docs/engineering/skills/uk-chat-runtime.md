# UK Chat Runtime

Use this skill when changing the UK chat model pathway, system prompts, exposed
tools, calculation behavior, or AI-facing runtime boundaries.

## Source Boundaries

The backend is organized by topic — one package per concern:

- `backend/chat/` owns the chat turn: `orchestrator.py` (request parsing, SSE
  streaming, the tool loop), `system_blocks.py` (system-block assembly + the
  reference doc), `model_selection.py`, `schemas.py`, `titles.py`,
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
  (households, microdata, reforms, simulations, sandbox, serialization);
  `backend/engine/reference.py` builds the API reference attached to the chat
  system prompt.
- `backend/config/` owns model-call configuration (model ids, temperatures, the
  Anthropic client factories, the scope-descriptor loader).
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

- `validate_reform`: validate parametric reform JSON without running a
  simulation.
- `calculate_household`: calculate illustrative synthetic household outcomes.
- `run_economy_simulation`: calculate aggregate society-wide impacts for
  parametric reforms.
- `analyse_microdata`: analyse allowed non-FRS model microdata through bounded
  filtering, sampling, grouping, and aggregation operations.
- `lookup_parameter`: look up baseline model parameter values by exact path or
  natural-language query.
- `lookup_variable`: look up PolicyEngine UK variable metadata and formula
  source where available.
- `run_python`: execute reproducible PolicyEngine UK Python code for fallback
  cases that do not fit the typed tools.
- `generate_chart`: return frontend-renderable chart JSON markdown.

Helper functions in `backend/engine/` are implementation details unless they
are exposed through `@register_tool`.

`tools.registry.tool_definitions()` returns caller-owned JSON-like snapshots for
model/eval requests. Mutating those snapshots is only a local per-call edit and
does not register, remove, or mutate canonical tools. Use `@register_tool` for
tool registration.

`policyengine-uk-compiled` 0.44.0 is the minimum supported output contract for
microdata-backed tools. When reform is omitted, `run_microdata()`,
`calculate_household`, and `analyse_microdata` outputs use plain calculated
column names such as `income_tax`, `universal_credit`, and `net_income`. When a
reform is supplied, including an empty no-op object (`{}`), calculations use
side-by-side `baseline_*` and `reform_*` comparison columns. Do not normalize
omitted-reform outputs back to the older prefixed shape.

The default columns for `analyse_microdata` are manually enumerated in
`backend/engine/microdata.py` because `policyengine-uk-compiled` does not yet
expose a programmatic way to query output columns or recommended defaults for
plain versus reform-comparison mode. Replace those lists with upstream metadata
when it becomes available.

## Deterministic And Non-Deterministic Segments

- Non-deterministic: user text interpretation, model planning, tool selection,
  prose generation, follow-up suggestions, and title generation.
- Deterministic: request validation, the gateway gate (criticality + outcome),
  lightweight-route tool omission, tool dispatch, typed tool execution after
  selection, Python execution, chart JSON construction,
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
- Quantitative policy answers should be computed with the typed calculation
  tools when they fit the request, or with `run_python` as a fallback; do not
  answer tax, benefit, reform, poverty, decile, or distributional questions from
  memory.
- Static parameter questions should use `lookup_parameter`; variable definition
  or formula questions should use `lookup_variable`. Do not run household or
  economy simulations just to infer a parameter value.
- If a lookup tool returns `status: "needs_confirmation"`, ask the user to pick
  one of the returned options before presenting a value or formula.
- Use `validate_reform` only when the user is drafting, debugging, or asking
  whether reform JSON is valid. Do not use it as a routine preflight before
  every simulation.
- Do not access, display, quote, or imply access to row-level survey microdata
  or real households.
- Use aggregate microdata interfaces only for aggregate outputs.
- Do not use `analyse_microdata` with FRS. For FRS-backed questions, use
  aggregate outputs such as `run_economy_simulation`.
- Do not row-sample the Enhanced FRS: the `sample` operation of
  `analyse_microdata` is unavailable for `efrs` because its rows derive from
  FRS respondents. Use aggregate operations for `efrs`.
- If a user asks for household examples, construct illustrative synthetic
  households with the public `Simulation` API. Prefer
  `Simulation.single_person()` for single-person examples, and label examples as
  illustrative, synthetic, or hypothetical.
