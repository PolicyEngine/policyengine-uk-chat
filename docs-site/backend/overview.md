# Backend overview

The backend is a FastAPI application whose entrypoint is `backend/api/main.py`.
It hosts the chat agent and persists conversations. In production it runs as a
[Modal](https://modal.com) ASGI app; locally it runs in Docker on port 8001.

## App setup

`api/main.py` wires up the application:

- The app is constructed as
  `FastAPI(title="Microsim Public Chatbot API", default_response_class=NaNSafeJSONResponse)`.
- **Three routers** are mounted: `billing` and `conversations` at the root, and
  `chat` under the `/chat` prefix.
- **CORS** is driven by the `HOSTNAMES` environment variable (comma-separated;
  defaults to `*`), with credentials and all methods/headers allowed.
- **`NaNSafeJSONResponse`** (`api/errors.py`) is the default response class — it
  converts `NaN`/`Inf` floats (which appear in microsimulation output) to `null`
  so responses are valid JSON.
- **Observability** is initialised with `init_observability(app, service_role="api")`
  (see the Observability section below).
- **Rate limiting** uses `slowapi`; the handler in `api/errors.py` returns `429`
  with a `Retry-After` header.
- On **startup**, `conversations.ensure_table()` creates/migrates the chat
  history table; on **shutdown**, observability is flushed.

## Operational endpoints

```{list-table}
:header-rows: 1

* - Method & path
  - Description
* - `GET /health`
  - Liveness check — returns `{"status": "ok"}`.
* - `GET /version`
  - Returns `{"policyengine_uk_compiled": "<version>"}` (or `"unknown"`).
```

The `/version` endpoint is how you confirm which engine build a given
deployment is serving — useful because the agent's `reference.md` is stamped to
match.

## The API reference (`reference.md`) and scope descriptor

`backend/reference.md` is a cached, version-stamped reference document
describing the PolicyEngine UK API surface (capabilities snapshot, public API,
reform recipes, the `Parameters` JSON schema). It is injected into the compute
agent's context so it can write correct code without guessing.

A companion `backend/scope_descriptor.md` is a compact descriptor of the
modelled programmes, datasets, years, and the "not modelled" boundary. It feeds
the lightweight gateway prompts where the full reference would be too heavy.

Both are **generated**, not hand-edited — `backend/engine/reference.py` rebuilds
them against the installed engine. This happens:

- in the **Docker image** build (`RUN python engine/reference.py`), and
- in the **Modal image** build (`modal_app.py` runs the same script after
  installing the engine),

so the deployed agent always reads a reference that matches the engine it will
execute against. Both files are git-ignored — they only exist at build time.

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
- `observability/segments.py` defines a `SegmentName` catalogue —
  `GATEWAY_CLASSIFY`, `MODEL_SELECT`, `SYSTEM_BUILD`, `MODEL_STREAM`,
  `TOOL_EXECUTE`, `BILLING_RECORD_USAGE`, `SUGGESTIONS`, `TITLE_GENERATE`, and
  more — used as `segment(...)` context managers across the orchestrator so each
  phase of a chat turn is traced and timed.

Gateway outcome/route/tool are recorded as metric attributes, so routing
behaviour is observable in aggregate.
