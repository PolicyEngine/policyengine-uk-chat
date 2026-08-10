# API reference

All endpoints are served by the FastAPI backend. Interactive Swagger docs are
available at `/docs` on a running instance (e.g. <http://localhost:8001/docs>).

```{note}
Request and response shapes below are summarised from the route handlers under
`backend/`. For exact, always-current schemas use the live `/docs`.
```

## System

```{list-table}
:header-rows: 1
:widths: 30 70

* - Endpoint
  - Description
* - `GET /health`
  - Liveness probe. Returns `{"status": "ok"}`.
* - `GET /version`
  - Returns the `policyengine.py` engine version and installed `policyengine-uk` version.
```

## Chat — `/chat`

Defined in `backend/chat/routes.py`, mounted under the `/chat` prefix. This is
the core of the app: a streaming Claude tool-use loop, fronted by the gateway.

```{list-table}
:header-rows: 1
:widths: 30 70

* - Endpoint
  - Description
* - `POST /chat/message`
  - Send a user message and stream the agent's response back over Server-Sent
    Events. Accepts `messages`, `session_id`, `user_id`, `charts_mode`, and an
    optional image (`image_base64` / `image_media_type`). Rate-limited per user
    and per IP.
* - `POST /chat/title`
  - Generate a short title for a conversation using the fast title model.
```

The SSE stream emits typed events: `chunk` (text deltas), `tool_start` /
`tool_use` / `tool_result` (tool activity), `thinking_done`, `suggestions`
(follow-up chips), `done` (final usage, cost, balance, model, route, outcome),
and `error`. See [The chat agent](chat.md) for the loop.

## Conversations — `/conversations`

Defined in `backend/conversations/` (a router that aggregates the `store`,
`sharing`, and `reports` sub-routers). Persists chat sessions to Postgres
(`chat_conversations` table) and supports public sharing.

```{list-table}
:header-rows: 1
:widths: 45 55

* - Endpoint
  - Description
* - `POST /conversations`
  - Create or update (upsert by `session_id`) a conversation; returns the
    stored detail.
* - `GET /conversations`
  - List the caller's saved conversations (filtered by `user_id`).
* - `GET /conversations/{conversation_id}`
  - Fetch a single conversation's detail.
* - `DELETE /conversations/{conversation_id}`
  - Delete a conversation (`204 No Content`).
* - `POST /conversations/{conversation_id}/share`
  - Mint (or return) a public `share_token` for a conversation.
* - `GET /conversations/shared/{share_token}`
  - Fetch a shared conversation by its public token (no auth).
* - `POST /conversations/{conversation_id}/report`
  - Report a conversation — builds a GitHub issue from the transcript and
    returns its `issue_url` plus a `share_url`.
```

Shared conversations are surfaced in the frontend at `/uk/chat/s/[token]`.

## Billing — `/billing`

Defined in `backend/billing/routes.py`. Tracks token cost, manages credit, and
runs Stripe checkout. These routes return `404` unless
`BILLING_ENABLED=true`. See [Billing](billing.md) for the cost model.

```{list-table}
:header-rows: 1
:widths: 30 70

* - Endpoint
  - Description
* - `GET /billing/balance`
  - Current credit balance summary for the user.
* - `GET /billing/usage`
  - Token usage / cost history (from the `token_usage` table).
* - `POST /billing/checkout`
  - Start a Stripe checkout session to add credit.
* - `POST /billing/webhook`
  - Stripe webhook receiver — verifies the signature and credits the account.
```
