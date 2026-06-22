# UK Chat Runtime

Use this skill when changing the UK chat model pathway, system prompts, exposed
tools, calculation behavior, or AI-facing runtime boundaries.

## Source Boundaries

- `backend/prompts.py` owns product runtime prompt text. Keep prompts modular and
  declarative there.
- `backend/routes/chatbot.py` owns application orchestration: request parsing,
  system block assembly, model calls, SSE streaming, tool-loop handling,
  usage/billing, title generation, and follow-up suggestions.
- `backend/agent_tools.py` owns the model-facing tool functions, dispatcher,
  and compatibility exports.
- `backend/tool_definitions.py` owns model-facing tool schemas and
  descriptions. Reuse shared schema fragments there rather than duplicating
  object/array/dataset/format shapes.
- Shared deterministic tool helpers live under `backend/tooling/`.
- `backend/scripts/build_reference.py` builds the API reference that is attached
  to the chat system prompt.

Do not spread prompt strings back into route handlers. If runtime prompt rules
change, update `backend/prompts.py` and the prompt contract tests together.

## Model Harness

The current chat runtime is application-specific rather than a generic model
harness. It calls the Anthropic SDK directly for streaming control. Pydantic AI
imports/comments may exist, but they should not be treated as the active
orchestration layer unless the code is deliberately refactored.

Keep model/provider-specific code at the orchestration edge. Durable guidance
for agents belongs in `docs/engineering/skills/`; product behavior prompts
belong in `backend/prompts.py`.

## Tool Boundary

Only tools listed in `TOOL_DEFINITIONS` and dispatched by `execute_tool()` are
exposed to the model. At present, the exposed tools are:

- `calculate_household`: calculate illustrative synthetic household outcomes.
- `validate_reform`: validate parametric reform JSON without running a
  simulation.
- `run_economy_simulation`: calculate aggregate society-wide impacts for
  parametric reforms.
- `analyse_microdata`: analyse allowed non-FRS model microdata through bounded
  filtering, sampling, grouping, and aggregation operations.
- `run_python`: execute reproducible PolicyEngine UK Python code for fallback
  cases that do not fit the typed tools.
- `generate_chart`: return frontend-renderable chart JSON markdown.

Helper functions in `backend/tooling/` are implementation details unless they
are added to both the tool definitions and dispatcher.

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
`backend/model_config.py`: `DEFAULT_TEMPERATURE` (0, deterministic) for the
compute loop, titling, the gateway classifier, and evals; `SUGGESTION_TEMPERATURE`
for follow-up suggestion chips, which deliberately sample with variety.

## Policy Analysis Rules

- Be factually neutral. Do not call UK tax or benefit choices good, bad, fair,
  unfair, regressive, progressive, generous, punitive, or similar.
- Quantitative policy answers should be computed with the typed calculation
  tools when they fit the request, or with `run_python` as a fallback; do not
  answer tax, benefit, reform, poverty, decile, or distributional questions from
  memory.
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
