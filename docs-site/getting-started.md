# Getting started

This page gets the full stack — frontend, backend, and Postgres — running
locally with Docker.

## Prerequisites

- Docker and Docker Compose
- An **Anthropic API key** (the agent calls Claude models)
- A **Supabase** project (public URL + anon key) when testing frontend auth
- A `HUGGING_FACE_TOKEN` with access to the private Enhanced FRS dataset for
  economy-wide simulations

## 1. Configure environment

The repo ships an `.env.example`. Copy it and fill in your keys:

```bash
make init        # copies .env.example -> .env if .env doesn't exist
```

Then edit `.env`. The key variables (see `.env.example` and
`docker-compose.yml` for the full set):

| Variable | Purpose |
| --- | --- |
| `ANTHROPIC_API_KEY` | Required — authenticates the chat agent |
| `ANTHROPIC_FAST_MODEL` | Fast model for titles and bounded model-assisted operations (default `claude-haiku-4-5`) |
| `ANTHROPIC_COMPLEX_MODEL` | Larger model for big-context analysis (default `claude-sonnet-4-6`) |
| `ANTHROPIC_REASONING_MODEL` | Reasoning model for reform/distributional work (default `claude-opus-4-5`) |
| `ANTHROPIC_TITLE_MODEL` | Model used to title conversations (defaults to the fast model) |
| `DATABASE_URL` | Postgres connection string for conversation history |
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Frontend Supabase auth |
| `BILLING_ENABLED` | Opt-in billing switch (default `false`) |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Backend billing access; required only when billing is enabled |
| `HUGGING_FACE_TOKEN` | Access to the private Enhanced FRS dataset |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | Required only when billing is enabled |

## 2. Start the stack

```bash
make up           # docker compose up (live reload)
```

This launches three services:

| Service | Container | Local URL |
| --- | --- | --- |
| Frontend (Next.js) | `frontend-microsim-public` | <http://localhost:3006> |
| Backend (FastAPI) | `backend-microsim-public` | <http://localhost:8001> |
| Database (Postgres 16) | `db-microsim-public` | `localhost:5433` |

Open <http://localhost:3006> and start chatting. The backend's interactive API
docs are at <http://localhost:8001/docs> (FastAPI's built-in Swagger UI).

## Useful Make targets

```bash
make up-d           # start detached
make logs           # tail all logs
make logs-backend   # tail just the backend
make build          # rebuild images after dependency changes
make rebuild        # down + build + up
make down           # stop and remove containers
make shell-backend  # open a shell in the backend container
make test           # run backend + frontend tests
make test-backend   # pytest backend/tests
```

## 3. Run the backend tests

The backend tests run in the **`python313` conda environment** (not `base`):

```bash
conda activate python313
cd backend
pytest
```

The suite lives in `backend/tests/` and covers the API, capability composition
and execution, typed tools, persistence and migrations, billing, observability,
prompts, and the evaluation harness. `make test-backend` runs the same suite the
CI `tests.yml` workflow uses.

## Smoke-checking the backend

```bash
curl http://localhost:8001/health
# {"status": "ok"}

curl http://localhost:8001/version
# {"engine":"policyengine.py","engine_version":"<version>","policyengine_uk":"<version>"}
```

The `/version` response tells you which policyengine.py and UK country-package
versions the instance is serving (see [Backend overview](backend/overview.md)).
