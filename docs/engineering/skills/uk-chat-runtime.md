# UK Chat Runtime

Use this skill when changing the UK chat model pathway, system prompts, exposed
tools, calculation behavior, or AI-facing runtime boundaries.

## Source Boundaries

- `backend/prompts.py` owns product runtime prompt text. Keep prompts modular and
  declarative there.
- `backend/routes/chatbot.py` owns application orchestration: request parsing,
  system block assembly, model calls, SSE streaming, tool-loop handling,
  usage/billing, title generation, and follow-up suggestions.
- `backend/agent_tools.py` owns deterministic tool implementations and model
  tool schemas.
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

- `run_python`: execute reproducible PolicyEngine UK Python code.
- `generate_chart`: return frontend-renderable chart JSON markdown.

Helper functions in `backend/agent_tools.py` are implementation details unless
they are added to both the tool definitions and dispatcher.

## Deterministic And Non-Deterministic Segments

- Non-deterministic: user text interpretation, model planning, tool selection,
  prose generation, follow-up suggestions, and title generation.
- Deterministic: request validation, plan-mode tool omission, tool dispatch,
  Python execution, chart JSON construction, result truncation/summarisation,
  billing calculation, and database writes.

Plan mode must remain structurally enforced by omitting tools from the model
request, not only by prompting the model not to call tools.

## Policy Analysis Rules

- Be factually neutral. Do not call UK tax or benefit choices good, bad, fair,
  unfair, regressive, progressive, generous, punitive, or similar.
- Quantitative policy answers should be computed with `run_python`; do not
  answer tax, benefit, reform, poverty, decile, or distributional questions from
  memory.
- Do not access, display, quote, or imply access to row-level survey microdata
  or real households.
- Use aggregate microdata interfaces only for aggregate outputs.
- If a user asks for household examples, construct illustrative synthetic
  households with the public `Simulation` API. Prefer
  `Simulation.single_person()` for single-person examples, and label examples as
  illustrative, synthetic, or hypothetical.
