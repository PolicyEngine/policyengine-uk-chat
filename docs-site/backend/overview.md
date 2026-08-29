# Backend overview

The backend is a FastAPI application whose entrypoint is `backend/api/main.py`.
It hosts the chat agent and persists conversations. In production it runs as a
[Modal](https://modal.com) ASGI app; locally it runs in Docker on port 8001.

## App setup

`api/main.py` wires up the application:

- The app is constructed as
  `FastAPI(title="Microsim Public Chatbot API", default_response_class=NaNSafeJSONResponse)`.
- **Three routers** are mounted: `billing` and `conversations` at the root, and
  `chat` under the `/chat` prefix. The billing router is guarded by the
  opt-in `BILLING_ENABLED` feature flag and returns `404` while disabled.
- **CORS** is driven by the comma-separated `HOSTNAMES` environment variable.
  An unset value fails closed and allows no cross-origin requests.
- **`NaNSafeJSONResponse`** (`api/errors.py`) is the default response class — it
  converts `NaN`/`Inf` floats (which appear in microsimulation output) to `null`
  so responses are valid JSON.
- **Observability** is initialised with `init_observability(app, service_role="api")`
  (see the Observability section below).
- **Rate limiting** uses `slowapi`; the handler in `api/errors.py` returns `429`
  with a `Retry-After` header.
- On **startup**, `verify_database_schema()` confirms that PostgreSQL is at the
  repository's Alembic revision without changing schema; on **shutdown**,
  observability is flushed.

## Operational endpoints

```{list-table}
:header-rows: 1

* - Method & path
  - Description
* - `GET /health`
  - Liveness check — returns `{"status": "ok"}`.
* - `GET /version`
  - Returns `engine`, `engine_version`, and `policyengine_uk` (or `"unknown"` for an unavailable package version).
```

The `/version` endpoint confirms which policyengine.py and UK package versions a
deployment is serving.

## Model catalogue and scope

Capabilities discover the live model through typed tools. Variable,
parameter, entity, reform-target, household-input, and output discovery are
separate calls so the model retrieves only the catalog area it needs.

A private relevance capability performs a bounded scope assessment on every
turn. It can identify a clearly unsupported jurisdiction or unrelated request,
but cannot choose another capability or construct calculation input.

## Rate limits

`api/rate_limit.py` defines per-user and per-IP limits for the chat endpoint
with a key function that prefers the authenticated user (`X-User-Id` header) and
falls back to client IP (from `X-Forwarded-For`). The defaults:

- `RATE_LIMIT_CHAT_PER_MIN` (default `5`) and `RATE_LIMIT_CHAT_PER_HOUR`
  (default `60`) — per user (or per IP if anonymous).
- `RATE_LIMIT_CHAT_IP_PER_MIN` (default `30`) — per-IP defence in depth.

Exceeding a limit yields a `429` with `Retry-After`. Storage is in-memory per
process by default; point `slowapi` at Redis for cross-container accuracy.

## Observability

`backend/observability/` wires `policyengine-observability` into the app:

- `init_observability(app, service_role="api")` (`observability/fastapi.py`)
  configures tracing, spans (prefix `uk_chat`, service `policyengine-uk-chat`),
  and metrics, and instruments FastAPI and `httpx`.
- `observability/segments.py` defines a `SegmentName` catalogue including
  `MODEL_STREAM`, `TOOL_EXECUTE`, `BILLING_RECORD_USAGE`, `SUGGESTIONS`, and
  `TITLE_GENERATE`, used as `segment(...)` context managers around external and
  deterministic operations.

Every registered capability and tool call also produces a sanitized,
parent-aware invocation record.
