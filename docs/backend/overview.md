# Backend overview

The backend is a FastAPI application (`backend/main.py`) that hosts the chat
agent and persists conversations. In production it runs as a
[Modal](https://modal.com) ASGI app; locally it runs in Docker on port 8001.

## App setup

`main.py` wires up the application:

- **Three routers** are mounted: `billing`, `chatbot`, and `conversations`.
- **CORS** is driven by the `HOSTNAMES` environment variable (comma-separated;
  defaults to `*`).
- **`NaNSafeJSONResponse`** is the default response class — it converts `NaN`/
  `Inf` floats (which appear in microsimulation output) to `null` so responses
  are valid JSON.
- **Rate limiting** uses `slowapi`; a custom handler returns `429` with a
  `Retry-After` header.
- On **startup**, `conversations.ensure_table()` creates the chat history table
  if it doesn't exist.

## Operational endpoints

```{list-table}
:header-rows: 1

* - Method & path
  - Description
* - `GET /health`
  - Liveness check — returns `{"status": "ok"}`.
* - `GET /version`
  - Returns the installed `policyengine-uk-compiled` version.
```

The `/version` endpoint is how you confirm which engine build a given
deployment is serving — useful because the agent's `reference.md` is stamped to
match.

## The API reference (`reference.md`)

`backend/reference.md` is a cached, version-stamped reference document describing
the PolicyEngine UK API surface (datasets, parameters, reform recipes). It is
injected into the agent's context so it can write correct code without guessing.

It is **generated**, not hand-edited — `scripts/build_reference.py` rebuilds it
against the installed engine. This happens:

- in the **Docker image** build, and
- in the **Modal image** build (`modal_app.py` runs the script after installing
  the engine),

so the deployed agent always reads a reference that matches the engine it will
execute against.

## Rate limits

`rate_limit.py` defines per-user and per-IP limits for the chat endpoint
(`CHAT_USER_LIMIT`, `CHAT_IP_LIMIT`) with a key function that prefers the
authenticated user and falls back to client IP. Exceeding a limit yields a
`429` with `Retry-After`.
