# Architecture

PolicyEngine UK Chat is a two-tier application: a Next.js frontend and a FastAPI
backend that hosts an LLM agent. The agent answers policy questions by writing
and executing Python against the compiled PolicyEngine UK engine.

## High-level flow

```text
   ┌──────────────┐   SSE / JSON    ┌───────────────────────────────────┐
   │  Next.js UI  │ ──────────────▶ │            FastAPI backend         │
   │  (port 3006) │ ◀────────────── │             (Modal / 8001)         │
   └──────┬───────┘   stream         │                                   │
          │                          │  ┌─────────────────────────────┐  │
          │ Supabase auth            │  │  pydantic-ai Agent (Claude) │  │
          ▼                          │  │   tools: run_python,        │  │
   ┌──────────────┐                  │  │          generate_chart     │  │
   │   Supabase   │                  │  └──────────────┬──────────────┘  │
   │  auth + DB   │ ◀────────────────┤                 │ executes        │
   └──────────────┘  conversations   │                 ▼                 │
                                      │  ┌─────────────────────────────┐  │
                                      │  │ policyengine_uk_compiled    │  │
                                      │  │ (Rust-backed UK model)      │  │
                                      │  └─────────────────────────────┘  │
                                      └───────────────────────────────────┘
```

## Request lifecycle

1. **User sends a message.** The frontend posts to the backend's
   `POST /chat/message` endpoint (proxied in production through a Next.js route
   handler at `/api/proxy/[...slug]`).
2. **The agent plans.** A `pydantic-ai` `Agent` wraps an Anthropic Claude model.
   The system prompt instructs it to *always compute with Python* and to begin
   by inspecting `capabilities()` to ground itself in the available datasets,
   years, and programmes.
3. **Tool use.** The agent calls `run_python` to execute code against the
   preloaded PolicyEngine UK objects (`pe`, `Simulation`, `Parameters`,
   `StructuralReform`, `aggregate_microdata`, …), and `generate_chart` to render
   results.
4. **Streaming response.** Tokens and tool activity stream back to the UI over
   Server-Sent Events.
5. **Persistence & billing.** The conversation is saved to Postgres via the
   `conversations` router; token usage is priced and tracked by the `billing`
   router.

## Backend modules

| Module | Responsibility |
| --- | --- |
| `main.py` | FastAPI app, CORS, NaN-safe JSON, rate-limit handler, `/health`, `/version` |
| `routes/chatbot.py` | The agent loop, system prompt, SSE streaming (`/chat/*`) |
| `routes/conversations.py` | Save/list/fetch/share/report/delete chat history (`/conversations/*`) |
| `routes/billing.py` | Token cost tracking, credit balance, Stripe checkout (`/billing/*`) |
| `agent_tools.py` | `run_python` + `generate_chart` tool definitions and executor |
| `rate_limit.py` | `slowapi` limiter, per-user and per-IP chat limits |
| `reference.md` | Cached API reference injected into the agent's context |
| `scripts/build_reference.py` | Regenerates `reference.md` against the installed engine version |

## The calculation engine

```{important}
The engine is **`policyengine-uk-compiled`** — the Rust-backed compiled model —
not the pure-Python `policyengine-uk`. The Modal image bakes the engine into the
image snapshot (`_preload_engine`) so cold starts are fast, and regenerates
`reference.md` against the deployed engine version at build time.
```

Because the engine version moves, the API reference the agent reads is
*version-stamped*: `scripts/build_reference.py` rebuilds `reference.md` against
whatever `policyengine-uk-compiled` is installed, both in the Docker image and
in the Modal image. A scheduled workflow redeploys when a new engine version
ships on PyPI.

## Models

Three Claude models are configured via environment variables, letting the app
match model cost to task difficulty:

- **Fast model** (`ANTHROPIC_FAST_MODEL`, default `claude-haiku-4-5`) — titling
  and lightweight work.
- **Complex model** (`ANTHROPIC_COMPLEX_MODEL`, default `claude-sonnet-4-6`) —
  reform and distributional analysis.
- **Title model** (`ANTHROPIC_TITLE_MODEL`) — naming saved conversations.
