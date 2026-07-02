# The chat agent

The chat experience is an LLM agent that answers policy questions by *computing*,
never by recalling numbers. This page describes the streaming agent loop, how a
model is chosen for each turn, and how the system prompt is assembled. The tools
the agent calls are documented separately in [Tools](tools.md); the pre-pass
that routes each message is in [The gateway](gateway.md).

## The agent loop

`backend/chat/orchestrator.py` drives the turn. `stream_chat(request, chat_request)`
returns a `StreamingResponse` wrapping an async generator that:

1. runs the [gateway](gateway.md) classification (`route="compute"` vs the
   lightweight no-tool route),
2. selects a model (below) and builds the system blocks,
3. enters a streaming tool-use loop, up to `MAX_ITERATIONS = 30` iterations.

On each iteration the model may emit text (streamed to the UI as `chunk`
events) or request tool calls. **Tool calls within a turn are dispatched
concurrently** — each runs as an `execute_tool_async` task and results stream
back via `asyncio.as_completed`, while the conversation transcript is rebuilt in
the original tool-call order. The loop ends when the model produces a final
answer, emitting a `done` event with the stop reason, token usage, GBP cost,
remaining balance, model, and the gateway route/outcome.

If the loop hits the iteration cap, it emits a fallback message that summarises
the tool attempts and stops with `stop_reason="iteration_cap"`, so a runaway
turn always terminates cleanly.

The compute agent's context includes the version-stamped API reference
(`reference.md`) so it knows the engine's signatures, reform keys, and dataset
descriptions without guessing.

## Model selection

`backend/chat/model_selection.py` picks the model per turn, matching cost to
difficulty:

1. **Reasoning signal** → the reasoning model. Triggered when the gateway's plan
   targets a simulation tool (`run_economy_simulation`, `calculate_household`,
   `analyse_microdata`) with a grounded `reform` slot, or when a text scan
   detects distributional/reform intent.
2. **Charts mode** → the reasoning model.
3. **Otherwise**, an input-size estimate (messages + system prompt + reference
   doc) decides: above `ANTHROPIC_FAST_MODEL_MAX_INPUT_TOKENS` (default
   `120000`) uses the complex model, else the fast model.

The model IDs come from `backend/config/models.py`:

| Env var | Default | Used for |
| --- | --- | --- |
| `ANTHROPIC_FAST_MODEL` | `claude-haiku-4-5` | gateway, titles, suggestions, small compute turns |
| `ANTHROPIC_COMPLEX_MODEL` | `claude-sonnet-4-6` | large-context compute turns |
| `ANTHROPIC_REASONING_MODEL` | `claude-opus-4-5` | reform/distributional analysis, charts mode |
| `ANTHROPIC_TITLE_MODEL` | fast model | naming saved conversations |
| `ANTHROPIC_SUGGESTION_MODEL` | fast model | follow-up suggestion chips |

Sampling (`backend/config/sampling.py`) is deterministic by default:
`ANTHROPIC_TEMPERATURE` defaults to `0` for compute, gateway, and titles;
follow-up suggestions use `ANTHROPIC_SUGGESTION_TEMPERATURE` (default `1`).

## System prompt principles

The system prompt (`backend/prompts/system.py`, assembled into `SYSTEM_PROMPT`)
encodes the product's core guarantees:

- **Always compute with tools.** Never answer a quantitative question from
  memory — every number must come from a tool result. Prefer the typed tools
  (`calculate_household`, `run_economy_simulation`, `analyse_microdata`), use
  `lookup_parameter` for static parameter values, `validate_reform` to check
  reform JSON, and `run_python` for structural reforms, historical questions, or
  novel aggregations.
- **Read capabilities first.** When using `run_python`, call `capabilities()`
  before simulating to ground in the modelled datasets, years, and programmes.
- **Use the official interface.** The Python environment preloads the
  PolicyEngine UK objects — prefer them over re-implementing policy logic.
- **Reproducibility.** Write clear code another developer could copy and run;
  prefer one substantial call over many tiny ones; put the answer in `result`.
- **Microdata privacy.** Never display row-level microdata or real households;
  `analyse_microdata` must not sample the FRS/eFRS; build illustrative synthetic
  households instead.
- **Analytical care & neutrality.** Decile impacts are decile-level averages;
  poverty outputs are already percentage rates; use British English; stay
  factually neutral and avoid value-labelling policies.

## How the system blocks are assembled

`backend/chat/system_blocks.py` builds the Anthropic request content so prompt
caching stays effective:

- `_build_system_blocks(...)` (compute route) emits a cached `SYSTEM_PROMPT`
  block, a cached `reference.md` block, then **per-turn** directives appended
  *after* the cache breakpoints — the charts-mode directive and the gateway plan
  — so toggling them never invalidates the cache.
- `_build_lightweight_system_blocks(verdict)` (non-`ready` routes) emits a lean,
  no-computation prompt parameterised by the scope descriptor, plus the
  per-outcome writer directive.
- `_tool_defs_for_anthropic()` converts the registry's tool definitions to the
  Anthropic format and stamps `cache_control` on the **last** tool only, so the
  whole tool array caches as one block.

## Titles and suggestions

Two cheap fast-model passes round out the experience: `backend/chat/titles.py`
names a saved conversation (`POST /chat/title`), and `backend/chat/suggestions.py`
produces the follow-up suggestion chips streamed as the `suggestions` SSE event.
