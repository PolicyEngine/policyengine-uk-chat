# The agent and its tools

The chat experience is an LLM agent that answers policy questions by *writing and
running code*, never by recalling numbers. This page describes the agent loop,
its system prompt principles, and the two tools it can call.

## The agent loop

`routes/chatbot.py` drives an Anthropic Claude model through a streaming
tool-use loop. On each turn the model may:

1. emit text (streamed to the UI), or
2. call a tool (`run_python` or `generate_chart`), whose result is fed back into
   the conversation for the next turn.

The loop continues until the model produces a final answer. The agent's context
includes the version-stamped API reference (`reference.md`) so it knows the
engine's signatures, reform keys, and dataset descriptions without guessing.

```{note}
The model uses `claude-sonnet-4-6` for analysis by default and
`claude-haiku-4-5` for lighter work (titles, follow-up suggestion chips). All
model IDs are configurable via environment variables.
```

## System prompt principles

The system prompt (in `routes/chatbot.py`) encodes the product's core
guarantees. The important rules:

- **Always compute with Python.** Never answer quantitative questions from
  memory — every number in an answer must come from a `run_python` result.
- **Start by reading capabilities.** At the start of a new line of analysis,
  inspect `capabilities()` to ground in the modelled datasets, years, and
  programmes before simulating.
- **Use the official interface.** The Python environment preloads the
  PolicyEngine UK objects (below) — prefer them over re-implementing policy
  logic.
- **Reproducibility.** Write clear code another developer could copy and run;
  prefer one substantial `run_python` call over many tiny ones; put the answer in
  `result`.
- **Honest scope.** If something isn't modelled well enough for a quantitative
  answer, say so — don't fabricate estimates.
- **Analytical care.** Decile impacts are decile-level averages; poverty outputs
  are already percentage rates; results use British English and stay neutral.

## Tool: `run_python`

Executes reproducible Python against the compiled PolicyEngine UK engine. The
environment preloads:

| Name | What it is |
| --- | --- |
| `pe` | the `policyengine_uk_compiled` module |
| `Simulation` | construct and run a simulation, e.g. `Simulation(year=2025)` |
| `Parameters` | policy parameter model (`Parameters.model_validate({...})`) |
| `StructuralReform` | structural reform construction |
| `aggregate_microdata`, `combine_microdata` | microdata aggregation helpers |
| `capabilities` | snapshot of modelled datasets/years/programmes |
| `ensure_dataset` | ensure a dataset is available locally |
| `pd`, `np`, `json`, `math` | common libraries |

**Contract:** the code must assign its final answer to `result`; `print()` is for
short diagnostics only. The single required argument is `code` (a string).

## Tool: `generate_chart`

Turns computed data into a chart the frontend renders. The agent first computes a
list of row dicts with `run_python`, then calls `generate_chart`.

Key arguments:

- `chart_type` — one of `line`, `bar`, `area`, `scatter`.
- `title`, `data` (list of row objects), `x_field`, `y_fields` (one or more
  series), `x_label`, `y_label`.
- `*_format` — `currency`, `percent`, `percent_decimal`, `number`, `compact`,
  `year` — so axis ticks and tooltips render correctly.

```{important}
`generate_chart` returns a `chart_markdown` field containing a fenced
` ```chart ` JSON block. The agent **must paste that block verbatim** into its
next text response — the frontend parses it to render the chart. Matplotlib
output from inside `run_python` is discarded by the UI, so charts must go through
this tool.
```

## Adding or changing tools

Both tools are declared in `agent_tools.py`:

- `TOOL_DEFINITIONS` — the JSON schema the model sees.
- `execute_tool(tool_name, tool_input)` — dispatches to the Python
  implementation (`run_python`, `generate_chart`).

To add a tool, append a definition to `TOOL_DEFINITIONS` and register its
implementation in the `tools` dict inside `execute_tool`. Cover it with a test in
`backend/tests/`.
