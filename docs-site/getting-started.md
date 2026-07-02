# Getting started

This page gets the full stack — frontend, backend, and Postgres — running
locally with Docker.

## Prerequisites

- Docker and Docker Compose
- An **Anthropic API key** (the agent calls Claude models)
- A **Supabase** project (URL + anon/service-role keys) for auth and billing
- Optionally a `POLICYENGINE_UK_DATA_TOKEN` for gated economy-wide datasets

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
| `ANTHROPIC_FAST_MODEL` | Cheap model for titles/gateway/light work (default `claude-haiku-4-5`) |
| `ANTHROPIC_COMPLEX_MODEL` | Larger model for big-context analysis (default `claude-sonnet-4-6`) |
| `ANTHROPIC_REASONING_MODEL` | Reasoning model for reform/distributional work (default `claude-opus-4-5`) |
| `ANTHROPIC_TITLE_MODEL` | Model used to title conversations (defaults to the fast model) |
| `DATABASE_URL` | Postgres connection string for conversation history |
| `NEXT_PUBLIC_SUPABASE_URL` / `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Frontend Supabase auth |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` | Backend Supabase access (billing/credits) |
| `POLICYENGINE_UK_DATA_TOKEN` | Access to gated PolicyEngine UK datasets |
| `STRIPE_SECRET_KEY` / `STRIPE_WEBHOOK_SECRET` | Billing (optional locally) |

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

The suite lives in `backend/tests/` and covers the API (`test_api.py`), the
agent tools (`test_agent_tools.py`), the gateway (`test_gateway.py`), model
selection (`test_model_selection.py`), billing (`test_billing.py`),
observability (`test_observability.py`), prompts (`test_prompts.py`), and the
evaluation harness (`test_evaluation.py`). `make test-backend` runs the same
suite the CI `tests.yml` workflow uses.

## Smoke-checking the backend

```bash
curl http://localhost:8001/health
# {"status": "ok"}

curl http://localhost:8001/version
# {"policyengine_uk_compiled": "<version>"}
```

The `/version` value tells you which engine build a given instance is serving —
it is the same version the agent's generated `reference.md` is stamped against
(see [Backend overview](backend/overview.md)).
