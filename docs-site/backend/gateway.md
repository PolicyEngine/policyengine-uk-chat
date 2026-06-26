# The gateway

The **gateway** (`backend/gateway/`) is a cheap per-turn pre-pass that runs
*before* the heavy compute model. It classifies the latest user message, grounds
a structured plan, and decides how the turn should be answered — so the
expensive model and tools only run when the question is actually in scope and
fully specified.

## What it does

`run_gateway(last_user_message)` (`gateway/runtime.py`) makes a single
**forced-tool** call to an internal `emit_plan` tool on the fast model
(`POLICYENGINE_CHAT_GATEWAY_MODEL`, defaulting to the fast model
`claude-haiku-4-5`). The model fills a structured plan:

- `in_domain` — is this a UK tax/benefit question at all?
- `tool` — the best-fit tool (one of the registered tool names, or `none`).
- `slots` — a list of `SlotFact(name, kind, value, source)`, where `kind` is
  `tool_input` or `output` and `source ∈ {prompt, default, assumed}`.
- `unmodellable_outputs` — requested outputs the engine cannot produce.
- `rationale`.

```{note}
The `emit_plan` tool exists only inside the gateway. It is deliberately **not**
part of the agent's `TOOL_DEFINITIONS`, so it never appears in the compute tool
array (and never busts that array's prompt cache).
```

## The five outcomes

A pure policy function, `gate()` (`gateway/policy.py`), maps the plan to one of
five outcomes:

```{list-table}
:header-rows: 1
:widths: 22 18 60

* - Outcome
  - Route
  - When
* - `ready`
  - compute
  - In domain, a tool fits, and every load-bearing input slot is grounded.
* - `needs_plan`
  - lightweight
  - In domain and a tool fits, but a critical input slot is `assumed`/missing —
    ask 1–3 clarifying questions.
* - `partial`
  - lightweight
  - In domain and a tool fits, but some requested outputs are unmodellable —
    state what *can* be modelled and ask whether to proceed.
* - `out_of_scope`
  - lightweight
  - In domain but no tool fits — offer the closest modelled angle.
* - `irrelevant`
  - lightweight
  - Not a UK tax/benefit question — decline briefly.
```

A slot *gates* (forces `needs_plan`) only when its `source` is `assumed`, its
criticality is high or medium, and it is not model-inferable — so the gateway
avoids over-asking for values it can reasonably infer.

## Routing and the writer

- On **`ready`**, the orchestrator takes `route="compute"` and injects the
  resolved plan into the compute system prompt via `serialise_plan_for_system`
  (a compact "GATEWAY PLAN … Resolved inputs: …" block, listing only
  `prompt`/`default`-sourced slots). The full tool set is available.
- On every **other** outcome, the orchestrator takes the lightweight route: a
  no-tool reply built from the scope descriptor plus the per-outcome directive
  from `backend/prompts/gateway.py` (`gateway_writer_directive`). The agent can
  decline, scope, or ask its clarifying question without running any simulation.

## Fail-safe

The gateway is defensive: an empty message, any API error, or a missing plan
block falls back to `GatewayVerdict(outcome="ready", route="compute")`. A
gateway failure therefore degrades to "just run the full agent", never to a
dropped turn. The gateway outcome, route, and tool are recorded as observability
metric attributes.
