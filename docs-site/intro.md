# PolicyEngine UK Chat

**PolicyEngine UK Chat** is a conversational interface for analysing UK tax and
benefit policy. Ask a question in plain English — _"How much does a £2,000
personal allowance increase cost?"_, _"What's the poverty impact of abolishing
the two-child limit?"_ — and the assistant answers by computing the result
against the PolicyEngine UK microsimulation engine.

Every quantitative answer is computed, not recalled: the agent never quotes
numbers from memory. It calls typed simulation tools (or writes Python),
executes them against the compiled PolicyEngine UK model, and reports results
straight from the run — often with a chart you can read inline.

## What's in this documentation

```{tableofcontents}
```

## The stack at a glance

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 15 + React 19, Mantine UI, deployed on Vercel |
| Backend | FastAPI (Python 3.13), served as a Modal ASGI app |
| Agent | Anthropic Claude models driven through a streaming tool-use loop |
| Engine | `policyengine-uk-compiled` (the Rust-backed UK tax-benefit model) |
| Gateway | A cheap per-turn pre-pass that classifies and routes each message |
| Auth & storage | Supabase (auth) + Postgres (conversation history) |
| Billing | Stripe checkout with per-token cost tracking |
| Observability | `policyengine-observability` tracing/metrics across the turn |

```{note}
The calculation engine is the **compiled** `policyengine-uk-compiled` package
(Rust-backed), not the pure-Python `policyengine-uk`. Code and reviews that
assume the pure-Python engine are usually wrong for this repo.
```

## Repository layout

The backend is organised by **topic packages** — each subdirectory of
`backend/` owns one concern.

```text
policyengine-uk-chat/
├── backend/                FastAPI app + chat agent (topic packages)
│   ├── api/                App entrypoint, CORS, error/NaN handling, rate limits
│   ├── chat/               The streaming agent loop, model selection, system blocks
│   ├── gateway/            Cheap per-turn classify-and-route pre-pass
│   ├── tools/              Decorator-based tool registry, schemas, dispatch
│   ├── engine/             Compiled-engine helpers, sandbox, lookups, reference gen
│   ├── prompts/            System prompt text (compute + gateway/lightweight)
│   ├── conversations/      Save / list / share / report chat history (Postgres)
│   ├── billing/            Token cost model, credits, Stripe
│   ├── observability/      Tracing/metrics wiring and segment names
│   ├── config/             Anthropic clients, model IDs, sampling, scope descriptor
│   └── eval/               Offline + live AI evaluation harness
├── frontend/               Next.js app (chat UI, charts, auth, sharing)
├── supabase/               Database migrations
├── docs-site/              This Jupyter Book
├── docs/engineering/       AI-facing engineering guidance/harness (separate)
├── modal_app.py            Modal deployment definition
├── docker-compose.yml      Local dev stack (frontend + backend + Postgres)
└── Makefile                Dev shortcuts (make up / down / logs / test / ...)
```

```{note}
`docs/engineering/skills/` is the canonical **AI-facing** engineering
guidance/harness (referenced by `CLAUDE.md` and `AGENTS.md`). This public
developer documentation lives in `docs-site/` so the two don't blur together.
```
