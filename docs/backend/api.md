# API reference

All endpoints are served by the FastAPI backend. Interactive Swagger docs are
available at `/docs` on a running instance (e.g. <http://localhost:8001/docs>).

```{note}
Request and response shapes below are summarised from the route handlers in
`backend/routes/`. For exact, always-current schemas use the live `/docs`.
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
  - Returns `{"policyengine_uk_compiled": "<version>"}`.
```

## Chat — `/chat`

Defined in `routes/chatbot.py`. This is the core of the app: a `pydantic-ai`
agent running Claude with tool use, streamed over Server-Sent Events.

```{list-table}
:header-rows: 1
:widths: 30 70

* - Endpoint
  - Description
* - `POST /chat/message`
  - Send a user message and stream the agent's response (text + tool activity)
    back over SSE. Rate-limited per user and per IP.
* - `POST /chat/title`
  - Generate a short title for a conversation using the fast title model.
```

## Conversations — `/conversations`

Defined in `routes/conversations.py`. Persists chat sessions to Postgres
(`chat_conversations` table) and supports sharing.

```{list-table}
:header-rows: 1
:widths: 40 60

* - Endpoint
  - Description
* - `POST /conversations`
  - Create/save a conversation; returns the stored detail.
* - `GET /conversations`
  - List the caller's saved conversations.
* - `GET /conversations/{conversation_id}`
  - Fetch a single conversation's detail.
* - `DELETE /conversations/{conversation_id}`
  - Delete a conversation (`204 No Content`).
* - `POST /conversations/{conversation_id}/share`
  - Create a public share token for a conversation.
* - `GET /conversations/shared/{share_token}`
  - Fetch a shared conversation by its public token (no auth).
* - `POST /conversations/{conversation_id}/report`
  - Report a conversation (e.g. for moderation).
```

Shared conversations are surfaced in the frontend at `/s/[token]`.

## Billing — `/billing`

Defined in `routes/billing.py`. Tracks token cost, manages credit, and runs
Stripe checkout. See [Billing](billing.md) for the cost model.

```{list-table}
:header-rows: 1
:widths: 30 70

* - Endpoint
  - Description
* - `GET /billing/balance`
  - Current credit balance for the user.
* - `GET /billing/usage`
  - Usage/cost history.
* - `POST /billing/checkout`
  - Start a Stripe checkout session to add credit.
* - `POST /billing/webhook`
  - Stripe webhook receiver (payment confirmation).
```
