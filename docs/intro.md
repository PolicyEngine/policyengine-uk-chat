# PolicyEngine UK Chat

**PolicyEngine UK Chat** is a conversational interface for analysing UK tax and
benefit policy. Ask a question in plain English — _"How much does a £2,000
personal allowance increase cost?"_, _"What's the poverty impact of abolishing
the two-child limit?"_ — and the assistant answers by writing and running
reproducible Python against the PolicyEngine UK microsimulation engine.

Every quantitative answer is computed, not recalled: the agent never quotes
numbers from memory. It writes Python, executes it against the compiled
PolicyEngine UK model, and reports results straight from the run — often with a
chart you can read inline.

## What's in this documentation

```{tableofcontents}
```

## The stack at a glance

| Layer | Technology |
| --- | --- |
| Frontend | Next.js 15 + React 19, Mantine UI, deployed as a static/SSR app |
| Backend | FastAPI (Python 3.13), served as a Modal ASGI app |
| Agent | `pydantic-ai` driving Anthropic Claude models with tool use |
| Engine | `policyengine-uk-compiled` (the Rust-backed UK tax-benefit model) |
| Auth & storage | Supabase (auth) + Postgres (conversation history) |
| Billing | Stripe checkout with per-token cost tracking |

```{note}
The calculation engine is the **compiled** `policyengine-uk-compiled` package
(Rust-backed), not the pure-Python `policyengine-uk`. Code and reviews that
assume the pure-Python engine are usually wrong for this repo.
```

## Repository layout

```text
policyengine-uk-chat/
├── backend/            FastAPI app, agent tools, API reference builder
│   ├── main.py         App entrypoint, CORS, rate-limit handler, /health, /version
│   ├── routes/         chatbot, conversations, billing routers
│   ├── agent_tools.py  run_python + generate_chart tool definitions
│   ├── reference.md    Cached API reference fed to the agent (version-stamped)
│   └── scripts/        build_reference.py
├── frontend/           Next.js app (chat UI, charts, auth, sharing)
├── supabase/           Database migrations
├── docs/               This Jupyter Book
├── modal_app.py        Modal deployment definition
├── docker-compose.yml  Local dev stack (frontend + backend + Postgres)
└── Makefile            Dev shortcuts (make up / down / logs / ...)
```
