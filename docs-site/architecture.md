# Architecture

PolicyEngine UK Chat is a two-tier application: a Next.js frontend and a FastAPI
backend that hosts an LLM agent. The agent answers policy questions by calling
typed simulation tools (or writing Python) against the compiled PolicyEngine UK
engine.

## High-level flow

```text
   ┌──────────────┐   SSE / JSON    ┌───────────────────────────────────────┐
   │  Next.js UI  │ ──────────────▶ │             FastAPI backend            │
   │  (port 3006) │ ◀────────────── │              (Modal / 8001)            │
   └──────┬───────┘   SSE stream     │                                       │
          │                          │  ┌─────────────┐  ┌────────────────┐  │
          │ Supabase auth            │  │   gateway   │─▶│  chat agent     │  │
          ▼                          │  │ classify +  │  │ (Claude, tools) │  │
   ┌──────────────┐                  │  │   route     │  └───────┬────────┘  │
   │   Supabase   │ ◀────────────────┤  └─────────────┘          │ executes  │
   │  auth + DB   │  billing/credits │                           ▼           │
   └──────────────┘                  │  ┌─────────────────────────────────┐  │
   ┌──────────────┐                  │  │   policyengine_uk_compiled       │  │
   │   Postgres   │ ◀────────────────┤  │   (Rust-backed UK model)         │  │
   │ conversations│                  │  └─────────────────────────────────┘  │
   └──────────────┘                  └───────────────────────────────────────┘
```

## Request lifecycle

1. **User sends a message.** The frontend posts to `POST /chat/message`
   (proxied in production through a Next.js route handler at
   `/api/proxy/[...slug]`).
2. **The gateway pre-pass runs.** Before the heavy model, a cheap forced-tool
   call (`run_gateway`, on the fast model) classifies the latest message into
   one of five outcomes — `ready`, `needs_plan`, `partial`, `out_of_scope`,
   `irrelevant` — and produces a structured plan. `ready` routes to the full
   compute agent; everything else routes to a lightweight no-tool reply (ask a
   clarifying question, decline politely, etc.). See [The gateway](backend/gateway.md).
3. **Model selection.** On a compute turn, the input size, charts mode, and the
   gateway's plan choose between the fast, complex, and reasoning models.
4. **The agent loop.** A streaming tool-use loop drives the Claude model. Its
   system prompt instructs it to *always compute*, and its context includes the
   version-stamped API reference (`reference.md`) so it knows the engine's
   signatures, reform keys, and dataset descriptions without guessing.
5. **Tool use.** The agent calls typed tools (`calculate_household`,
   `run_economy_simulation`, `analyse_microdata`, `validate_reform`,
   `lookup_parameter`), `run_python` for novel work, and `generate_chart` to
   render results. Tool calls in one turn are dispatched concurrently.
6. **Streaming response.** Text and tool activity stream back to the UI over
   Server-Sent Events; a final `done` event carries usage, cost, and balance.
7. **Persistence & billing.** The conversation is saved to Postgres via the
   `conversations` package; token usage is priced and debited by the `billing`
   package.

## Backend modules

The backend is organised by topic package, each owning one concern.

| Package | Responsibility |
| --- | --- |
| `api/` | FastAPI app, CORS, `NaNSafeJSONResponse`, rate-limit handler, `/health`, `/version` |
| `chat/` | The streaming agent loop (`orchestrator.py`), model selection, system-block assembly, titles, follow-up suggestions (`/chat/*`) |
| `gateway/` | The cheap per-turn classify-and-route pre-pass and its outcome policy |
| `tools/` | Decorator-based tool registry, JSON schemas, and dispatch |
| `engine/` | Compiled-engine helpers, the `run_python` sandbox, microdata, parameter lookups, and `reference.md`/`scope_descriptor.md` generation |
| `prompts/` | System prompt text for compute, gateway, and lightweight turns |
| `conversations/` | Save/list/fetch/share/report chat history (`/conversations/*`) |
| `billing/` | Token cost tracking, credit balance, Stripe checkout (`/billing/*`) |
| `observability/` | Tracing/metrics wiring and the segment-name catalogue |
| `config/` | Anthropic clients, model IDs, sampling, scope descriptor loading |
| `eval/` | Offline and live AI evaluation harness |

## The calculation engine

```{important}
The engine is **`policyengine-uk-compiled`** — the Rust-backed compiled model —
not the pure-Python `policyengine-uk`. The Modal image bakes the engine into the
image snapshot (`_preload_engine`) so cold starts are fast, and regenerates
`reference.md` against the deployed engine version at build time.
```

Because the engine version moves, the API reference the agent reads is
*version-stamped*: `backend/engine/reference.py` rebuilds `reference.md` (and a
compact `scope_descriptor.md` for the gateway) against whatever
`policyengine-uk-compiled` is installed, in both the Docker image and the Modal
image. A scheduled workflow redeploys when a new engine version ships on PyPI.

## Models

Several Claude models are configured via environment variables, letting the app
match model cost to task difficulty (see [The chat agent](backend/chat.md) for
the selection logic):

- **Fast model** (`ANTHROPIC_FAST_MODEL`, default `claude-haiku-4-5`) — the
  gateway, titles, follow-up suggestions, and lightweight replies.
- **Complex model** (`ANTHROPIC_COMPLEX_MODEL`, default `claude-sonnet-4-6`) —
  large-context compute turns.
- **Reasoning model** (`ANTHROPIC_REASONING_MODEL`, default `claude-opus-4-5`) —
  reform and distributional analysis, and charts mode.
